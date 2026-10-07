"""jyai 命令行入口.

    python -m jyai.cli status
    python -m jyai.cli disable-encryption [--user-data DIR]
    python -m jyai.cli enable-encryption  [--user-data DIR]
    python -m jyai.cli list [--root DIR]
    python -m jyai.cli inspect <draft_dir>
    python -m jyai.cli build <spec.json> [-o OUTDIR] [--register/--no-register]
    python -m jyai.cli patch <draft_dir> <edits.json>
    python -m jyai.cli keys [--keystore FILE]
    python -m jyai.cli verify <spec.json> -o OUTDIR

    # --- AI 剪辑 (无限画布 / CustomAgent) ---
    python -m jyai.cli bridge                      进程内画布桥自检
    python -m jyai.cli serve [--port N]            拉起 AI 后端 app-server
    python -m jyai.cli canvas-probe [--port N]     AI 后端健康检查
    python -m jyai.cli canvas-open <draft_id> <draft_root>
    python -m jyai.cli canvas-text <draft_id> <draft_root> <text>
    python -m jyai.cli canvas-nodes <draft_id> <draft_root>

    # --- 一句话剪视频 ---
    python -m jyai.cli cut <素材目录> --prompt "竖屏 每段3秒 总共15秒 加标题"
    python -m jyai.cli media <素材目录>
    python -m jyai.cli doctor                       体检: 找出打不开的草稿

    # --- 官方 CustomAgent 运行时 (附着剪映进程, 带登录态) ---
    python -m jyai.cli agent-server                 打印 agent server 坐标
    python -m jyai.cli agent-skills                 列出官方 Agent 技能
    python -m jyai.cli agent-processors             列出官方处理器
    python -m jyai.cli agent-task "一句话创作需求"   提交官方创作任务
    python -m jyai.cli agent-run <runId>            查看 run 详情/可用指令
    python -m jyai.cli agent-advance <runId>        推进 run
    python -m jyai.cli agent-say <runId> "补充要求"  给 run 追加消息
    python -m jyai.cli agent-workspace <素材...> --task "..."   登记素材并建任务
    python -m jyai.cli agent-logs                   查看本会话服务端日志
    python -m jyai.cli agent-modes                  列出内置 Agent 与指令白名单

    # --- 画质 (超清画质 / 补分辨率 / 一键超清) ---
    python -m jyai.cli quality-plan <草稿>           值不值得开超清 (不写入)
    python -m jyai.cli quality-set <草稿> --auto      智能判定后写入, 顺带开一键超清
    python -m jyai.cli quality-set <草稿> --mode super_resolution --level 2
    python -m jyai.cli quality-off <草稿>             全部关闭
    python -m jyai.cli quality-state <草稿>           当前画质状态
    python -m jyai.cli quality-panel [--ultra-hd on]  导出面板默认开关
"""
from __future__ import annotations
import argparse, json, os, sys
from typing import Any, Dict, List, Optional

from . import draft as _draft
from . import engine as _engine
from . import keystore as _ks
from . import project as _proj
from . import settings_patch as _sp
from . import quality as _q


def _j(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2)


# --------------------------------------------------------------------------
def cmd_status(a) -> int:
    print("settings file :", _sp.settings_path(a.user_data))
    print("switches      :")
    for k, v in _sp.read_state(a.user_data).items():
        print("    %-40s %s" % (k, v))
    print("disk switches disabled:", _sp.is_fully_disabled(a.user_data))
    print("draft codec   : native DraftIO (11.5.0.14471); 磁盘开关不代表运行时明文读写")
    print("read-only lock:", _sp.is_locked(a.user_data))
    print("root_meta     :", _proj.root_meta_path(a.user_data))
    meta = _proj.load_root_meta(a.user_data)
    print("registered    :", len(meta.get("all_draft_store", [])))
    return 0


def cmd_disable(a) -> int:
    r = _sp.disable(a.user_data, do_lock=not a.no_lock)
    print("已关闭加解密开关, 备份:", r["backup"])
    print(_j(r["state"]))
    print("crc 校验:", r["crc_ok"])
    print("只读锁  :", r["locked"],
          "(实测可挡住剪映启动时的重置)" if r["locked"] else "(未加锁: 重启剪映后会被写回 true)")
    if r["locked"]:
        print()
        print("提示: 以后想改这几个开关, 先跑 enable-encryption 或手动去掉只读属性")
    return 0


def cmd_enable(a) -> int:
    _sp.unlock(a.user_data)
    r = _sp.enable(a.user_data)
    print("已解锁并恢复加解密开关, 备份:", r["backup"])
    print(_j(r["state"]))
    return 0


def cmd_list(a) -> int:
    root = a.root
    if a.root:
        root = a.root
    else:
        meta = _proj.load_root_meta(a.user_data)
        store = meta.get("all_draft_store") or []
        seen: List[str] = []
        for e in store:
            rp = e.get("draft_root_path")
            if rp and rp not in seen:
                seen.append(rp)
        root = seen[0] if seen else None
    if not root:
        print("未找到草稿根目录, 用 --root 指定")
        return 2
    for d in _draft.list_drafts(root):
        enc = "ENCRYPTED" if d.is_encrypted else "plain"
        tl = d.active_timeline() or "-"
        print("%-16s %-9s timeline=%s  %s" % (d.name, enc, tl[:8], d.path))
    return 0


def cmd_inspect(a) -> int:
    d = _draft.Draft(resolve_draft(a.draft_dir, a.user_data))
    info: Dict[str, Any] = {
        "path": d.path,
        "encrypted": d.is_encrypted,
        "timelines": d.timeline_ids(),
        "active_timeline": d.active_timeline(),
        "template_json": d.template_json(),
    }
    try:
        c = d.read_content()
        info["duration_us"] = c.get("duration")
        info["canvas"] = c.get("canvas_config")
        info["fps"] = c.get("fps")
        info["tracks"] = [(t["type"], len(t.get("segments", []))) for t in c.get("tracks", [])]
        info["materials"] = {k: len(v) for k, v in (c.get("materials") or {}).items() if v}
    except Exception as exc:
        info["read_error"] = str(exc)
    print(_j(info))
    return 0


def cmd_build(a) -> int:
    spec = json.loads(open(a.spec, encoding="utf-8").read())
    out = a.out or os.path.dirname(os.path.abspath(a.spec))
    if a.name:
        spec["name"] = a.name
    r = _engine.build_from_scratch(spec, out, register=a.register)
    print("草稿目录:", r["draft_dir"])
    print("轨道数  :", r["tracks"])
    if r.get("draft_id"):
        print("draft_id:", r["draft_id"])
    print("下一步: 打开剪映, 草稿列表里应出现该草稿")
    return 0


def cmd_patch(a) -> int:
    edits = json.loads(open(a.edits, encoding="utf-8").read())
    r = _engine.patch_existing(a.draft_dir, edits)
    print(_j(r))
    return 0


def cmd_keys(a) -> int:
    p = a.keystore or os.path.join(
        _sp.settings_path(a.user_data).rsplit(os.sep, 2)[0], "Config", "crypto_key_store.dat")
    for e in _ks.load(p):
        print("%-4s %s  type=%s  uri=%s" % (e.get("name"), e["cipher_key_hex"],
                                            e["cipher_type"], e["uri"]))
    return 0


def cmd_verify(a) -> int:
    """构建草稿并做静态自检 (不需要启动剪映)."""
    spec = json.loads(open(a.spec, encoding="utf-8").read())
    out = a.out or os.path.dirname(os.path.abspath(a.spec))
    r = _engine.build_from_scratch(spec, out)
    d = _draft.Draft(r["draft_dir"])
    c = d.read_content()
    checks = {
        "draft_content.json 存在": os.path.isfile(d.content_path),
        "写盘为原生密文且可解码": d.is_encrypted,
        "version=360000": c.get("version") == 360000,
        "有 platform.app_id": (c.get("platform") or {}).get("app_id") == 3704,
        "canvas 合法": bool((c.get("canvas_config") or {}).get("width")),
        "tracks 非空": len(c.get("tracks") or []) > 0,
        "duration>0": (c.get("duration") or 0) > 0,
        "Timelines/project.json": os.path.isfile(os.path.join(d.path, "Timelines", "project.json")),
        "timeline_layout 指向有效时间线": d.active_timeline() in d.timeline_ids(),
        "template.tmp 是官方骨架": os.path.getsize(
            os.path.join(d.path, "Timelines", d.active_timeline(), "template.tmp")) == 3941,
        "素材路径存在": all(
            os.path.isfile(m["path"]) for m in (c["materials"].get("videos") or [])
            if m.get("path")),
    }
    print("草稿:", r["draft_dir"])
    ok = True
    for k, v in checks.items():
        print("  [%s] %s" % ("OK" if v else "!!", k))
        ok = ok and v
    print("结果:", "PASS" if ok else "FAIL", "(静态/编解码检查, 不是 GUI 打开验证)")
    return 0 if ok else 1


def cmd_hook(a) -> int:
    """用 Frida 启动剪映并注入运行时补丁 (不改配置文件, 升级后仍有效)."""
    import subprocess, tempfile
    js = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                      "frida", "hook_draft_crypto.js")
    if not os.path.isfile(js):
        print("缺少", js); return 2
    exe = a.exe or os.path.join(_proj.user_data_dir(), os.pardir, "Apps")
    if os.path.isdir(exe):
        cands = []
        for d in os.listdir(exe):
            p2 = os.path.join(exe, d, "JianyingPro.exe")
            if os.path.isfile(p2):
                cands.append((d, p2))
        cands.sort()
        exe = cands[-1][1] if cands else exe
    if not os.path.isfile(exe):
        print("找不到 JianyingPro.exe:", exe); return 2
    print("目标:", exe)
    print("脚本:", js)
    if a.attach:
        import time as _t
        js_path = js.replace("\\", "/")
        cmd = ["frida", "-n", "JianyingPro.exe", "-l", js_path, "-q", "--runtime=v8"]
    else:
        cmd = ["frida", "-f", exe, "-l", js, "--runtime=v8"]
    print("执行:", " ".join(cmd))
    return subprocess.call(cmd)


def cmd_agent(a) -> int:
    """探测 deepagents_capi.dll (剪映自带 AI Agent 运行时)."""
    from . import agent as _ag
    d = _ag.find_dll(a.apps)
    print("dll:", d)
    if not d:
        return 2
    print(_j(_ag.DeepAgent(d).describe()))
    print("agent_edit_api 路由:")
    for r in _ag.api_routes():
        print("   ", r)
    return 0



# --------------------------------------------------------------------------
# AI 剪辑 (无限画布 / CustomAgent app-server)
# --------------------------------------------------------------------------
def cmd_bridge(a) -> int:
    """进程内画布桥 (infinite-canvas native bridge) 自检."""
    from . import bridge as _br
    if _br is None:
        print("bridge 模块不可用"); return 2
    info = _br.describe(probe=a.probe, timeout=a.timeout)
    disc = info.get("discovery") or {}
    print("discovery :", disc.get("discovery_path"))
    print("pid       :", disc.get("pid"))
    print("port      :", info.get("port"))
    print("token     :", info.get("token_prefix"))
    print("health    :", _j(info.get("health")))
    reg = info.get("registered", [])
    print("registered: %d" % len(reg))
    if not a.probe:
        print("   (加 --probe 逐个调用验证)")
    for item in reg:
        if isinstance(item, (list, tuple)):
            print("     + %-30s code=%s" % (item[0], item[1]))
        else:
            print("     +", item)
    if info.get("unregistered"):
        print("unregistered:", info["unregistered"])
    if info.get("skipped_side_effect"):
        print("skipped(副作用):", ", ".join(info["skipped_side_effect"]))
    if info.get("probe_errors"):
        print("probe_errors:", info["probe_errors"])
    return 0


def cmd_serve(a) -> int:
    """拉起剪映自带的 CustomAgent app-server (AI 剪辑后端)."""
    from . import appserver as _as
    h = _as.start(a.port, a.app_dir, a.state_dir, wait=a.wait)
    print("listening : http://127.0.0.1:%d" % h.port)
    print("pid       : %d" % h.pid)
    print("app_dir   : %s" % h.app_dir)
    print("log       : %s" % h.log_path)
    if a.foreground:
        import time as _t
        try:
            while h.alive():
                _t.sleep(1)
        except KeyboardInterrupt:
            pass
        finally:
            h.stop()
    return 0


def _canvas_client(a):
    from . import canvas as _cv
    if a.port is None:
        a.port = 54397
    cp = _cv.CanvasProtocol(port=a.port, token=getattr(a, "token", None))
    return _cv, cp


def cmd_canvas_probe(a) -> int:
    _cv, cp = _canvas_client(a)
    print(_j(cp.health()))
    return 0


def cmd_canvas_open(a) -> int:
    _cv, cp = _canvas_client(a)
    cp.set_host_app_context(device_id=a.device_id)
    cp.resolve_canvas_instance(a.draft_id)
    cp.create_session("jyai canvas")
    snap = cp.open(a.draft_id, a.draft_root)
    print("instance  :", cp.instance_id)
    print("session   :", cp.session_id)
    print("generation:", cp.generation_id)
    print("revision  :", cp.revision)
    print("nodes     :", len(cp.nodes()))
    for n in cp.nodes():
        print("   %-8s %-36s %s" % (n.get("type"), n.get("id"),
                                    repr(n.get("data", {}).get("text", ""))[:40]))
    return 0


def cmd_canvas_text(a) -> int:
    _cv, cp = _canvas_client(a)
    cp.set_host_app_context(device_id=a.device_id)
    cp.resolve_canvas_instance(a.draft_id)
    cp.create_session("jyai canvas")
    cp.open(a.draft_id, a.draft_root)
    nid = cp.add_text(a.draft_id, a.text)
    print("nodeId    :", nid)
    print("revision  :", cp.revision)
    return 0


def cmd_canvas_nodes(a) -> int:
    _cv, cp = _canvas_client(a)
    cp.set_host_app_context(device_id=a.device_id)
    cp.resolve_canvas_instance(a.draft_id)
    cp.create_session("jyai canvas")
    cp.open(a.draft_id, a.draft_root)
    print(_j(cp.nodes()))
    return 0



# --------------------------------------------------------------------------
# 一句话剪视频
# --------------------------------------------------------------------------
def cmd_cut(a) -> int:
    from . import autocut as _ac
    res = _ac.autocut(a.folder, a.prompt or "", name=a.name,
                      per_clip=a.per_clip, total=a.total,
                      out_dir=a.out, register=not a.no_register,
                      recursive=a.recursive, limit=a.limit,
                      quality=a.quality, level=a.level,
                      target_height=a.target_height)
    print("草稿目录 :", res.get("draft_dir") or res.get("path"))
    print("草稿 ID  :", res.get("draft_id"))
    spec = res.get("spec", {})
    print("画幅     : %sx%s @ %sfps" % (spec.get("width"), spec.get("height"), spec.get("fps")))
    print("片段     :")
    for v in spec.get("video", []):
        print("     %-52s %6.1f - %-6.1f s"
              % (os.path.basename(v["path"])[:52], v["start"],
                 v["start"] + v["duration"]))
    if spec.get("text"):
        print("文字     :", ", ".join(t["text"][:20] for t in spec["text"]))
    qres = res.get("quality")
    if qres:
        print("画质策略 :", qres["mode"],
              "| 判定:", qres.get("auto_reason") or qres.get("recommend", {}).get("reason"))
        st = qres.get("stats") or {}
        if st:
            print("改写片段 : 超清画质 %d / 补分辨率 %d"
                  % (st.get("quality_enhance", 0), st.get("super_resolution", 0)))
        print("一键超清 :", "已开" if qres.get("ultra_hd") else "未开")
    if res.get("quality_error"):
        print("画质警告 :", res["quality_error"])
    if res.get("register_error"):
        print("注册警告 :", res["register_error"])
    print()
    print("打开剪映, 首页即可看到「%s」" % spec.get("name"))
    return 0


def cmd_media(a) -> int:
    from . import autocut as _ac
    items = _ac.list_media(a.folder, recursive=a.recursive)
    print("共 %d 个素材:" % len(items))
    for m in items:
        print("   %-56s %8.1f MB" % (os.path.basename(m)[:56],
                                     os.path.getsize(m) / 1048576))
    return 0


def _official_api():
    """定位脚本目录里的 jy_official_api (scripts/ 已在 sys.path)."""
    import sys as _sys
    scripts = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if scripts not in _sys.path:
        _sys.path.insert(0, scripts)
    from jy_official_api import OfficialMaterialAPI
    return OfficialMaterialAPI()


def cmd_cloud_categories(a) -> int:
    """列出剪映官方素材库的分类 (panel 常量)."""
    api = _official_api()
    panel = a.panel
    cats = api.panel_categories(panel, resource_count=a.count)
    print("panel=%s  panel_source=%s  categories=%d" % (panel, api.panel_source(panel), len(cats)))
    for c in cats:
        print("  %-14s %-24s key=%s" % (c.get("category_id"), (c.get("category_name") or "")[:24],
                                        c.get("category_key")))
    if a.json:
        print(_j(cats))
    return 0


def cmd_cloud_search(a) -> int:
    """搜索官方素材库 (视频/图片/贴纸/音效/音乐)."""
    api = _official_api()
    kind = a.kind
    results = []
    if kind in ("music",):
        data = api.search_songs(a.query, count=a.count, offset=a.offset)
        for s in (data.get("songs") or []):
            results.append({
                "resource_id": str(s.get("id") or ""), "title": s.get("title") or "",
                "author": s.get("author") or "", "duration_s": s.get("duration") or 0,
                "kind": "audio", "download_url": s.get("preview_url") or "",
            })
    elif kind == "sound":
        cats = api.panel_categories("audio")
        target = None
        for c in cats:
            if (c.get("category_name") or "") in a.query or a.query in (c.get("category_name") or ""):
                target = c
                break
        target = target or (cats[0] if cats else None)
        if target:
            d = api.list_category_items(target.get("category_id"), target.get("category_key"),
                                        panel="audio", count=a.count, offset=a.offset)
            for it in api.iter_normalized(d.get("effect_item_list") or []):
                if a.query and a.query not in (it.get("title") or "") and not target:
                    continue
                it["kind"] = "audio"
                results.append(it)
    else:
        et = {"video": 201, "image": 201, "sticker": 201, "any": 201}.get(kind, 201)
        data = api.search(a.query, count=a.count, offset=a.offset, effect_type=et)
        for it in api.iter_normalized(data.get("effect_item_list") or []):
            it["kind"] = "audio" if (it.get("effect_type") in (3, 4, 11)) else "video"
            if a.only_downloadable and not it.get("download_url"):
                continue
            results.append(it)
    print("找到 %d 条:" % len(results))
    for r in results:
        dl = "✓" if r.get("download_url") else "×"
        print("  %-20s %-8s %s %s  %s" % (r.get("resource_id"), r.get("kind"), dl,
                                          ("%.1fs" % (r.get("duration_s") or 0))[:6],
                                          (r.get("title") or "")[:36]))
    if a.json:
        print(_j(results))
    return 0


def cmd_cloud_list(a) -> int:
    """列出某分类下的官方素材 (默认 material-lib 的第一个分类)."""
    api = _official_api()
    cid, ckey = a.category_id, a.category_key
    if cid is None:
        cats = api.panel_categories(a.panel)
        if not cats:
            print("没有拿到分类")
            return 1
        cid, ckey = cats[0].get("category_id"), cats[0].get("category_key")
    data = api.list_category_items(cid, ckey, panel=a.panel, offset=a.offset, count=a.count)
    items = api.iter_normalized(data.get("effect_item_list") or [])
    print("category=%s items=%d has_more=%s next_offset=%s"
          % (cid, len(items), data.get("has_more"), data.get("next_offset")))
    for it in items:
        dl = "✓" if it.get("download_url") else "×"
        print("  %-20s %-6s %s  %s" % (it.get("resource_id"), it.get("effect_type"), dl,
                                       (it.get("title") or "")[:40]))
    if a.json:
        print(_j(items))
    return 0


def cmd_cloud_get(a) -> int:
    """按 resource_id / 关键词下载官方素材, 打印本地路径."""
    import sys as _sys
    scripts = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if scripts not in _sys.path:
        _sys.path.insert(0, scripts)
    from official_asset_manager import OfficialAssetManager
    mgr = OfficialAssetManager(cache_dir=a.cache_dir) if a.cache_dir else OfficialAssetManager()
    path = mgr.download(a.query, force=a.force)
    if not path:
        print("下载失败: %s" % a.query)
        return 1
    print("RESULT_PATH|%s" % path)
    return 0


def resolve_draft(value, user_data=None):
    if os.path.isdir(value):
        return os.path.abspath(value)
    for e in _proj.load_root_meta(user_data).get("all_draft_store", []):
        if e.get("draft_name") == value and os.path.isdir(e.get("draft_fold_path", "")):
            return e["draft_fold_path"]
    raise FileNotFoundError("找不到草稿: " + value)


def cmd_repair(a) -> int:
    from .repair import repair
    if a.draft_dir:
        paths = [resolve_draft(a.draft_dir, a.user_data)]
    elif a.generated:
        paths = [e["draft_fold_path"] for e in _proj.load_root_meta(a.user_data).get("all_draft_store", [])
                 if e.get("draft_name", "").startswith("AI_")
                 and not e.get("draft_is_infinite_canvas_draft")
                 and os.path.isfile(os.path.join(e.get("draft_fold_path", ""), "draft_content.json"))]
    else:
        raise ValueError("指定草稿名称/路径, 或 --generated 修复 AI_ 开头的生成草稿")
    failed = 0
    for path in paths:
        try:
            result = repair(path, user_data=a.user_data)
            print(_j(result))
            failed += not result.get("static_valid")
        except Exception as exc:
            failed += 1
            print(_j({"draft_dir": path, "error": str(exc)}))
    return 1 if failed else 0


def _quality_target(a) -> str:
    """草稿名/路径 -> 草稿目录."""
    try:
        return resolve_draft(a.draft, a.user_data)
    except Exception:
        return a.draft


def cmd_quality_plan(a) -> int:
    """只判断值不值得开超清, 不改任何文件."""
    st = _q.plan(_quality_target(a), target_height=a.target_height)
    rec = st["recommend"]
    print("草稿      :", st["name"])
    cc = st.get("canvas") or {}
    print("画布      : %sx%s" % (cc.get("width"), cc.get("height")))
    print("导出目标  : %sP" % st.get("target_height"))
    print("视频素材  :", st["videos"], "个")
    print("素材分辨率:", st["source_heights"])
    print("一键超清  :", "已开" if st["ultra_hd"] else ("未开" if st["ultra_hd"] is not None else "无 attachment_editing.json"))
    fa = st["function_assistant"]
    print("超清画质  :", "已开" if fa.get("enhance_quality") else "未开",
          "| segid %d 个" % len(fa.get("enhance_quality_segid_list") or []))
    for s in st["segments"]:
        print("   - %-40s %sx%s qe=%s sr=%s"
              % ((s["name"] or "")[:40], s["width"], s["height"],
                 bool(s["quality_enhance"]), bool(s["super_resolution"])))
    print()
    print("建议      :", "开" if rec["enable"] else "不开")
    print("理由      :", rec["reason"])
    if rec.get("est_upload"):
        print("代价      : 需把素材上传服务器排队处理, 会消耗时间/AI点数")
    return 0


def cmd_quality_set(a) -> int:
    import time
    target = _quality_target(a)
    r = _q.apply(target, mode=a.mode, level=a.level,
                 target_height=a.target_height,
                 dry_run=a.dry_run)
    if a.dry_run:
        print("[dry-run] 不会写入")
    print("草稿      :", r["draft"])
    print("模式      :", r["mode"])
    if r.get("auto_reason"):
        print("自动判定  :", r["auto_reason"])
    if a.target_height is None:
        a.target_height = _q.infer_target_height(_q.state(target))
    print("目标分辨率:", a.target_height, "P")
    print("素材分辨率:", r.get("source_heights"))
    st = r.get("stats") or {}
    if st:
        print("改写片段  : 超清画质 %d / 补分辨率 %d"
              % (st.get("quality_enhance", 0), st.get("super_resolution", 0)))
    print("一键超清  :", "已开" if r.get("ultra_hd") else "未开")
    if a.panel:
        w = _q.set_export_panel(a.user_data,
                                ultra_hd=bool(r.get("ultra_hd")),
                                mark_sr_used=(r["mode"] == _q.MODE_SR))
        for k, v in w.items():
            print("导出面板  :", k, "->", v)
    print()
    if r["mode"] == _q.MODE_OFF:
        print("结论: 素材已达导出分辨率, 不需要超清, 不浪费时间/AI点数")
    else:
        print("下一步: 回首页后重启剪映, 打开导出面板确认「一键超清」已勾选")
    return 0


def cmd_quality_off(a) -> int:
    r = _q.apply(_quality_target(a), mode=_q.MODE_OFF)
    print("草稿      :", r["draft"])
    print("已关闭    : 超清画质 + 补分辨率 + 一键超清")
    if a.panel:
        _q.set_export_panel(a.user_data, ultra_hd=False)
        print("导出面板  : userFirstVideoEnhanceExport=false")
    return 0


def cmd_quality_state(a) -> int:
    st = _q.state(_quality_target(a))
    print(_j({k: v for k, v in st.items() if k != "segments"}))
    if st["segments"]:
        print("片段:")
        for s in st["segments"]:
            print("   ", _j(s))
    return 0


def cmd_quality_panel(a) -> int:
    if a.ultra_hd is None and a.remember_level is None and a.sr_used is None:
        print(_j(_q.read_export_panel(a.user_data)))
        return 0
    uh = None if a.ultra_hd is None else (a.ultra_hd == "on")
    sr = None if a.sr_used is None else (a.sr_used == "on")
    w = _q.set_export_panel(a.user_data, ultra_hd=uh,
                            remember_level=a.remember_level, mark_sr_used=sr)
    print("已写入:")
    for k, v in w.items():
        print("   %-14s %s" % (k, v))
    print("注意: 这些值在剪映**关闭时**写入, 重新打开后生效")
    return 0


def cmd_render(a) -> int:
    from .render import render_basic
    print(_j(render_basic(resolve_draft(a.draft_dir, a.user_data), a.output)))
    return 0


def cmd_doctor(a) -> int:
    from .repair import validate
    root = a.root
    store = _proj.load_root_meta(a.user_data).get("all_draft_store", [])
    bad = 0
    for e in store:
        path = e.get("draft_fold_path", "")
        if root and _proj.normalize_path(os.path.dirname(path)) != _proj.normalize_path(root):
            continue
        if e.get("draft_is_infinite_canvas_draft"):
            print("[画布] %s: 与视频时间线分开检查" % e.get("draft_name"))
            continue
        cp = os.path.join(path, "draft_content.json")
        if _draft.is_base64_blob(cp) and not e.get("draft_name", "").startswith("AI_"):
            print("[加密] %s: 外部静态检查跳过, 不据此判断损坏" % e.get("draft_name"))
            continue
        try:
            r = validate(path, a.user_data)
            print("[%s] %s: %s" % ("OK" if r["static_valid"] else "!", e.get("draft_name"), r["issues"]))
            bad += not r["static_valid"]
        except Exception as exc:
            bad += 1
            print("[!] %s: %s" % (e.get("draft_name"), exc))
    print("静态检查完成, %d 个问题草稿; 此结果不等同于剪映打开验证" % bad)
    return 1 if bad else 0


def _legacy_cmd_doctor(a) -> int:
    """体检: 找出剪映里打不开的草稿并说明原因."""
    from . import project as _P
    root = a.root or None
    if root is None:
        for c in (r"D:\Jianying\Data1\JianyingPro Drafts",
                  os.path.expanduser(r"~\Documents\JianyingPro Drafts")):
            if os.path.isdir(c):
                root = c
                break
    if not root or not os.path.isdir(root):
        print("找不到草稿目录, 用 --root 指定"); return 2

    meta = _P.load_root_meta()
    store = {}
    for e in meta.get("all_draft_store", []):
        k = (e.get("draft_fold_path") or "").replace("\\", "/") \
            .replace("\\", "/").lower()
        store[k] = e

    bad = 0
    print("草稿目录:", root)
    print()
    for name in sorted(os.listdir(root)):
        d = os.path.join(root, name)
        if not os.path.isdir(d) or name.startswith("."):
            continue
        problems = []
        mp = os.path.join(d, "draft_meta_info.json")
        fid = None
        if not os.path.exists(mp):
            problems.append("缺 draft_meta_info.json")
        else:
            try:
                with open(mp, "r", encoding="utf-8") as f:
                    fid = (json.load(f) or {}).get("draft_id")
            except Exception:
                problems.append("draft_meta_info.json 加密/损坏 (旧版草稿)")
        if not os.path.exists(os.path.join(d, "draft_content.json")):
            # 11.x 的 content 在 Timelines/<id>/ 下; 加密时只有 .bak/template.tmp 是明文
            tl = os.path.join(d, "Timelines")
            has_plain = False
            if os.path.isdir(tl):
                for base, _dd, ff in os.walk(tl):
                    if "draft_content.json" in ff or "draft_info.json" in ff:
                        has_plain = True
                        break
            if has_plain:
                pass
            else:
                problems.append("内容被加密 (不影响剪映打开, 只影响外部读)")
        fwd = os.path.abspath(d).replace("\\", "/").lower()
        reg = store.get(fwd)
        if not reg:
            problems.append("未注册进剪映列表")
        elif fid and (reg.get("draft_id") or "").upper() != fid.upper():
            problems.append("draft_id 与注册表不一致")

        # 画布草稿: 只有 meta 是正常的, 需要在 AI 画布里打开
        is_canvas = False
        if os.path.exists(mp):
            try:
                with open(mp, "r", encoding="utf-8") as f:
                    is_canvas = bool((json.load(f) or {}).get("draft_is_infinite_canvas_draft"))
            except Exception:
                pass
        if is_canvas:
            problems = [p for p in problems
                        if "draft_content" not in p and "内容被加密" not in p]

        if problems:
            bad += 1
            print("  [!] %s" % name)
            for p in problems:
                print("        - %s" % p)
            if is_canvas:
                print("        = 这是 AI 无限画布草稿, 没有 draft_content.json 是正常的;")
                print("          要在剪映的 AI 创作/无限画布入口里打开, 不是普通剪辑入口")
            print()
    if bad == 0:
        print("  全部正常, 没有发现打不开的草稿")
    else:
        print("共 %d 个草稿有问题" % bad)
    return 0


# --------------------------------------------------------------------------

# --------------------------------------------------------------------------
# 官方 CustomAgent 运行时 (附着剪映进程, 带 host context / 登录态)
# --------------------------------------------------------------------------
def _agent_runtime():
    from . import agentruntime as _ar
    srv = _ar.find_live_server()
    return _ar, _ar.AgentRuntimeClient(srv)


def cmd_agent_server(a) -> int:
    """打印剪映当前 agent server 的坐标 (port/token/pid)."""
    from . import agentruntime as _ar
    srv = _ar.find_live_server()
    print("agent server port :", srv.port)
    print("token             :", srv.token[:12] + "..." if srv.token else "(none)")
    print("剪映 pid          :", srv.pid or "?")
    print("native bridge port:", srv.bridge_port)
    if a.json:
        print(_j({"port": srv.port, "pid": srv.pid,
                  "bridge_port": srv.bridge_port,
                  "has_token": bool(srv.token)}))
    return 0


def cmd_agent_skills(a) -> int:
    _ar, c = _agent_runtime()
    print("实例:", c.instance_id or "(未绑定)")
    print("会话:", c.session_id or "(未绑定)")
    for s in c.skills():
        print("  %-34s %-22s %s" % (s.get("skillId"), s.get("name"),
                                    "enabled" if s.get("enabled") else "disabled"))
    return 0


def cmd_agent_processors(a) -> int:
    _ar, c = _agent_runtime()
    for p in c.processors():
        print("  %-34s %-16s %s" % (p.get("processorId"), p.get("kind"),
                                    p.get("name")))
    return 0


def cmd_agent_task(a) -> int:
    _ar, c = _agent_runtime()
    c.bind(a.resource or "jyai-task")
    r = c.submit_task(a.task, agent_id=a.agent)
    run_id = _ar.AgentRuntimeClient.extract_run_id(r)
    print("instance :", c.instance_id)
    print("session  :", c.session_id)
    print("runId    :", run_id)
    rs = r.get("resultSummary") or {}
    print("status   :", rs.get("status"))
    if a.advance:
        r2 = c.advance(run_id)
        print("advance  :", (r2.get("resultSummary") or {}).get("status"))
    print()
    print("用 `agent-run %s` 查看节点与可用指令。" % run_id)
    return 0


def cmd_agent_run(a) -> int:
    _ar, c = _agent_runtime()
    c.instance_id = a.instance or c.instance_id
    c.session_id = a.session or c.session_id
    if not c.instance_id:
        c.bind(a.resource or "jyai-run")
    v = (c.open_run(a.run_id) if a.full
         else c.command({"kind": "OpenWorkspaceRun", "runId": a.run_id}))
    rv = _ar.AgentRuntimeClient.run_view(v) or {}
    print("runId    :", rv.get("runId"))
    print("status   :", rv.get("status"))
    print("workspace:", rv.get("workspacePath"))
    for n in rv.get("nodes", []):
        print("  node %-22s %-12s %-10s %s"
              % (n.get("nodeId"), n.get("kind"), n.get("status"), n.get("label")))
    cmds = rv.get("availableCommands") or []
    if cmds:
        print("available:")
        for cmd in cmds:
            print("   ", cmd.get("commandId") or cmd.get("kind") or cmd)
    if rv.get("failures"):
        print("failures :", _j(rv["failures"])[:600])
    return 0


def cmd_agent_advance(a) -> int:
    _ar, c = _agent_runtime()
    if a.instance:
        c.instance_id = a.instance
    if a.session:
        c.session_id = a.session
    if not c.instance_id:
        c.bind(a.resource or "jyai-adv")
    for i in range(max(1, a.times)):
        r = c.advance(a.run_id, reason="jyai cli")
        print("[%d] %s" % (i + 1, (r.get("resultSummary") or {}).get("status")))
        if a.interval:
            import time as _t
            _t.sleep(a.interval)
    return 0


def cmd_agent_say(a) -> int:
    _ar, c = _agent_runtime()
    if a.instance:
        c.instance_id = a.instance
    if a.session:
        c.session_id = a.session
    if not c.instance_id:
        c.bind(a.resource or "jyai-say")
    r = c.send_message(a.message, a.run_id)
    print("status:", (r.get("resultSummary") or {}).get("status"))
    return 0



def cmd_agent_workspace(a) -> int:
    """登记本地素材到 runtime workspace, 并建一个官方创作任务."""
    _ar, c = _agent_runtime()
    c.bind(a.resource or "jyai-hub")
    wp = c.prepare_workspace()
    print("instance   :", c.instance_id)
    print("session    :", c.session_id)
    print("workspace  :", wp)
    assets = c.register_local_paths(a.paths, workspace_path=wp)
    print("登记素材   :", len(assets), "个")
    for al in assets:
        print("   %-10s %-28s %s" % (al.get("kind"), (al.get("title") or "")[:28],
                                     al.get("assetId")))
    if a.list_only:
        return 0
    out = c.create_workspace_task(a.task, assets, workspace_path=wp,
                                  material_directory=a.material_dir,
                                  agent_id=a.agent)
    task = out.get("task") or {}
    print("taskId     :", task.get("taskId"))
    print("runId      :", task.get("runId"))
    print("status     :", task.get("status"))
    print()
    print("用 `agent-advance %s` 推进。" % (task.get("runId") or ""))
    return 0


def cmd_agent_logs(a) -> int:
    """打印本会话的服务端日志 (run / 模型 / 工具调用)."""
    _ar, c = _agent_runtime()
    if a.instance:
        c.instance_id = a.instance
    if a.session:
        c.session_id = a.session
    if not c.instance_id:
        c.bind(a.resource or "jyai-logs")
    logs = c.tool_logs(a.limit)
    for l in logs[-a.limit:]:
        print("  ", l.get("message") or l)
    if not logs:
        print("(无日志)")
    return 0


def cmd_agent_modes(a) -> int:
    """打印官方内置 Agent 画像与可用指令白名单."""
    from . import agentruntime as _ar
    print("内置 Agent:")
    for k, v in _ar.RUNTIME_MODES.items():
        print("   %-20s %s" % (k, v))
    print()
    print("可用指令 (%d):" % len(_ar.COMMAND_KINDS))
    for k in _ar.COMMAND_KINDS:
        print("   ", k)
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="jyai", description="剪映 AI 剪辑接口")
    ap.add_argument("--user-data", default=None, help="JianyingPro User Data 目录")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status").set_defaults(fn=cmd_status)
    p2 = sub.add_parser("disable-encryption")
    p2.add_argument("--no-lock", action="store_true",
                    help="只改值不加只读锁 (剪映下次启动会把值写回 true)")
    p2.set_defaults(fn=cmd_disable)
    sub.add_parser("enable-encryption").set_defaults(fn=cmd_enable)

    p = sub.add_parser("list"); p.add_argument("--root"); p.set_defaults(fn=cmd_list)
    p = sub.add_parser("inspect"); p.add_argument("draft_dir"); p.set_defaults(fn=cmd_inspect)
    p = sub.add_parser("build")
    p.add_argument("spec"); p.add_argument("-o", "--out")
    p.add_argument("--name"); p.add_argument("--register", dest="register", action="store_true", default=True)
    p.add_argument("--no-register", dest="register", action="store_false")
    p.set_defaults(fn=cmd_build)
    p = sub.add_parser("patch"); p.add_argument("draft_dir"); p.add_argument("edits"); p.set_defaults(fn=cmd_patch)
    p = sub.add_parser("keys"); p.add_argument("--keystore"); p.set_defaults(fn=cmd_keys)
    p = sub.add_parser("verify"); p.add_argument("spec"); p.add_argument("-o", "--out"); p.set_defaults(fn=cmd_verify)
    p = sub.add_parser("hook"); p.add_argument("--exe"); p.add_argument("--attach", action="store_true"); p.set_defaults(fn=cmd_hook)
    p = sub.add_parser("agent"); p.add_argument("--apps"); p.set_defaults(fn=cmd_agent)

    p = sub.add_parser("bridge"); p.add_argument("--probe", action="store_true",
                    help="逐个调用 JSB 方法验证注册情况");
    p.add_argument("--timeout", type=float, default=4.0); p.set_defaults(fn=cmd_bridge)
    p = sub.add_parser("serve")
    p.add_argument("--port", type=int, default=54397); p.add_argument("--app-dir")
    p.add_argument("--state-dir"); p.add_argument("--wait", type=float, default=40.0)
    p.add_argument("--foreground", action="store_true"); p.set_defaults(fn=cmd_serve)
    p = sub.add_parser("canvas-probe"); p.add_argument("--port", type=int)
    p.add_argument("--token"); p.set_defaults(fn=cmd_canvas_probe)
    p = sub.add_parser("canvas-open")
    p.add_argument("draft_id"); p.add_argument("draft_root")
    p.add_argument("--port", type=int); p.add_argument("--token")
    p.add_argument("--device-id", required=True); p.set_defaults(fn=cmd_canvas_open)
    p = sub.add_parser("canvas-text")
    p.add_argument("draft_id"); p.add_argument("draft_root"); p.add_argument("text")
    p.add_argument("--port", type=int); p.add_argument("--token")
    p.add_argument("--device-id", required=True); p.set_defaults(fn=cmd_canvas_text)
    p = sub.add_parser("canvas-nodes")
    p.add_argument("draft_id"); p.add_argument("draft_root")
    p.add_argument("--port", type=int); p.add_argument("--token")
    p.add_argument("--device-id", required=True); p.set_defaults(fn=cmd_canvas_nodes)

    p = sub.add_parser("cut", help="一句话剪视频")
    p.add_argument("folder"); p.add_argument("--prompt", default="")
    p.add_argument("--name"); p.add_argument("--per-clip", type=float)
    p.add_argument("--total", type=float); p.add_argument("-o", "--out")
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--recursive", action="store_true")
    p.add_argument("--no-register", action="store_true")
    p.set_defaults(fn=cmd_cut)
    p.add_argument("--quality", default="auto",
                   choices=["auto", "off", "quality_enhance", "super_resolution",
                            "one_click_ultra_hd"],
                   help="画质策略, 默认 auto (按素材档位判定, 不给 1080P 素材开 1080P 超清)")
    p.add_argument("--level", type=int, default=2, help="1 高清 / 2 超清 / 3 AI HD")
    p.add_argument("--target-height", dest="target_height", type=int, default=None,
                   help="导出档位短边, 默认按草稿推断")
    p = sub.add_parser("media", help="列出目录里的素材")
    p.add_argument("folder"); p.add_argument("--recursive", action="store_true")
    p.set_defaults(fn=cmd_media)
    p = sub.add_parser("cloud-categories", help="列出剪映官方素材库分类")
    p.add_argument("--panel", default="material-lib")
    p.add_argument("--count", type=int, default=50)
    p.add_argument("--json", action="store_true"); p.set_defaults(fn=cmd_cloud_categories)
    p = sub.add_parser("cloud-search", help="搜索剪映官方素材库")
    p.add_argument("query")
    p.add_argument("--kind", default="video",
                   choices=["video", "image", "sticker", "sound", "music", "any"])
    p.add_argument("--count", type=int, default=20)
    p.add_argument("--offset", type=int, default=0)
    p.add_argument("--only-downloadable", action="store_true")
    p.add_argument("--json", action="store_true"); p.set_defaults(fn=cmd_cloud_search)
    p = sub.add_parser("cloud-list", help="列出官方素材库某分类下的素材")
    p.add_argument("--panel", default="material-lib")
    p.add_argument("--category-id")
    p.add_argument("--category-key")
    p.add_argument("--count", type=int, default=50)
    p.add_argument("--offset", type=int, default=0)
    p.add_argument("--json", action="store_true"); p.set_defaults(fn=cmd_cloud_list)
    p = sub.add_parser("cloud-get", help="下载官方素材 (resource_id 或关键词)")
    p.add_argument("query")
    p.add_argument("--cache-dir"); p.add_argument("--force", action="store_true")
    p.set_defaults(fn=cmd_cloud_get)
    p = sub.add_parser("doctor", help="体检草稿")
    p.add_argument("--root"); p.set_defaults(fn=cmd_doctor)
    p = sub.add_parser("repair", help="修复生成草稿的首页登记/封面/活动时间线副本")
    p.add_argument("draft_dir", nargs="?")
    p.add_argument("--generated", action="store_true")
    p.set_defaults(fn=cmd_repair)
    p = sub.add_parser("agent-modes", help="列出内置 Agent 与指令白名单")
    p.set_defaults(fn=cmd_agent_modes)
    p = sub.add_parser("agent-workspace", help="登记素材并建官方创作任务")
    p.add_argument("paths", nargs="+", help="素材文件或目录 (目录自动展开)")
    p.add_argument("--task", default="把素材剪成一条短视频")
    p.add_argument("--agent", default="v2-director-agent")
    p.add_argument("--material-dir", dest="material_dir", default=None)
    p.add_argument("--resource", default=None)
    p.add_argument("--list-only", action="store_true", help="只登记素材, 不建任务")
    p.set_defaults(fn=cmd_agent_workspace)
    p = sub.add_parser("agent-logs", help="查看本会话服务端日志")
    p.add_argument("--limit", type=int, default=80)
    p.add_argument("--instance"); p.add_argument("--session")
    p.add_argument("--resource", default=None)
    p.set_defaults(fn=cmd_agent_logs)
    p = sub.add_parser("agent-server", help="打印剪映 agent server 坐标")
    p.add_argument("--json", action="store_true"); p.set_defaults(fn=cmd_agent_server)
    p = sub.add_parser("agent-skills", help="列出官方 Agent 技能")
    p.set_defaults(fn=cmd_agent_skills)
    p = sub.add_parser("agent-processors", help="列出官方处理器")
    p.set_defaults(fn=cmd_agent_processors)
    p = sub.add_parser("agent-task", help="提交官方创作任务")
    p.add_argument("task")
    p.add_argument("--agent", default="v2-director-agent")
    p.add_argument("--resource", default=None,
                   help="runtime 实例绑定的资源 id (默认自动生成)")
    p.add_argument("--advance", action="store_true")
    p.set_defaults(fn=cmd_agent_task)
    p = sub.add_parser("agent-run", help="查看 run 节点与可用指令")
    p.add_argument("run_id")
    p.add_argument("--full", action="store_true")
    p.add_argument("--instance"); p.add_argument("--session")
    p.add_argument("--resource", default=None)
    p.set_defaults(fn=cmd_agent_run)
    p = sub.add_parser("agent-advance", help="推进 run")
    p.add_argument("run_id")
    p.add_argument("--times", type=int, default=1)
    p.add_argument("--interval", type=float, default=0.0)
    p.add_argument("--instance"); p.add_argument("--session")
    p.add_argument("--resource", default=None)
    p.set_defaults(fn=cmd_agent_advance)
    p = sub.add_parser("agent-say", help="给 run 追加一条用户消息")
    p.add_argument("run_id"); p.add_argument("message")
    p.add_argument("--instance"); p.add_argument("--session")
    p.add_argument("--resource", default=None)
    p.set_defaults(fn=cmd_agent_say)

    p = sub.add_parser("quality-plan", help="判断要不要开超清 (不写入)")
    p.add_argument("draft", help="草稿名或草稿目录")
    p.add_argument("--target-height", dest="target_height", type=int, default=None,
                   help="导出分辨率高度, 默认按草稿推断 1080")
    p.set_defaults(fn=cmd_quality_plan)

    p = sub.add_parser("quality-set", help="开启画质能力并写一键超清")
    p.add_argument("draft", help="草稿名或草稿目录")
    p.add_argument("--mode", default="auto",
                   choices=["auto", "quality_enhance", "super_resolution",
                            "one_click_ultra_hd", "off"])
    p.add_argument("--level", type=int, default=2,
                   help="1 高清 / 2 超清 / 3 AI HD")
    p.add_argument("--target-height", dest="target_height", type=int, default=1080,
                   help="导出目标高度 (1080/2160)")
    p.add_argument("--dry-run", dest="dry_run", action="store_true")
    p.add_argument("--panel", action="store_true",
                   help="同时把导出面板默认开关写进 User Data/Config")
    p.set_defaults(fn=cmd_quality_set)

    p = sub.add_parser("quality-off", help="关闭全部画质增强")
    p.add_argument("draft")
    p.add_argument("--panel", action="store_true")
    p.set_defaults(fn=cmd_quality_off)

    p = sub.add_parser("quality-state", help="查看当前画质状态")
    p.add_argument("draft")
    p.set_defaults(fn=cmd_quality_state)

    p = sub.add_parser("quality-panel", help="读/写导出面板默认开关")
    p.add_argument("--ultra-hd", dest="ultra_hd", choices=["on", "off"], default=None)
    p.add_argument("--sr-used", dest="sr_used", choices=["on", "off"], default=None)
    p.add_argument("--remember-level", dest="remember_level", type=int, default=None)
    p.set_defaults(fn=cmd_quality_panel)

    p = sub.add_parser("render", help="把单视频轨的基础剪辑直接渲染为 MP4")
    p.add_argument("draft_dir"); p.add_argument("-o", "--output", required=True)
    p.set_defaults(fn=cmd_render)

    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
