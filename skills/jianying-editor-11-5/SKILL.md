---
name: jianying-editor-11-5
description: 为 Windows 剪映专业版 11.5.0.14471 创建、裁剪和增量修改可编辑视频草稿，使用本机原生 DraftIO 保存加密时间线并登记首页。给本地素材生成剪映工程时使用；不是实时 GUI 操作 skill，其他版本需要验证编解码 ABI。
---

# 剪映 11.5 可编辑草稿

基于 [isYangs/jianying-editor-skill](https://github.com/isYangs/jianying-editor-skill)，保留原库 API、资源和 LICENSE。只验证 Windows `11.5.0.14471`，不自动推广至未来版本或 macOS。

## 执行

找到本 `SKILL.md` 所在目录，安装同目录 `requirements.txt`，从其 `scripts` 目录使用本机 64 位 Python 3.10+：

```powershell
python -X utf8 -m jyai.cli cut "D:\视频素材" --prompt '竖屏 每段3秒 总共12秒 加标题 "产品展示"' --name "AI_产品展示"
```

示例路径换成用户真实路径；不要硬编码开发者的 Python 或用户名。草稿根目录默认读取 `JY_DRAFTS_ROOT` 或本机首页索引；探测失败时显式使用 `-o <实际草稿根目录>`。

`cut` 默认带 `--quality auto`：按素材档位判定值不值得补分辨率，不值得就跳过
（不给 1080P 素材开 1080P 超清），结果会打印「画质策略 / 判定 / 一键超清」。
要强制或关掉见下面的「画质」章节。

先整理片段清单：素材、源起点、目标起点、时长、画幅、音频和文字。`cut --prompt` 只解析画幅、秒数、引号文字等规则；画面理解、剧情、精彩片段选择、语音转录或真正卡点需代理使用实际可用工具分析，不能宣称规则解析器完成了语义理解。

精确剪辑：`python -X utf8 -m jyai.cli build <spec.json> -o <草稿根目录>`。
Spec 支持 `name,material_dir,width,height,fps,video,audio,text,mute`。片段 `path,source_start,start,duration` 中时间单位为秒；相对路径按 `material_dir` 或工作目录解析。素材不足时报告实际成片时长。

新建使用唯一名称，不覆盖用户已有草稿。已有工程使用 `patch <具体草稿目录> <edits.json>`；先重新读取实际最新内容，不从旧清单重建。人工新增复杂效果时保留未触及字段并在剪映复核。

上游 API：将本目录 `scripts` 加到 `sys.path`，导入 `jy_wrapper.JyProject`，显式传 `drafts_root`，新建使用唯一名称并设 `overwrite=False`，调用 `add_clip()` 后 `save()`。不要用上游模板加载/重建流程覆盖用户已有加密工程；现有工程用适配后的 `jyai` 增量接口。`save()` 会经过本适配桥生成时间线、封面、原生加密内容并登记具体草稿目录。

## 官方素材库

剪映自带的官方素材库（片头/片尾/热梗/背景/绿幕/贴纸/滤镜/音效/音乐…）走的是官方接口，
**CDN 直链是短时签名，静态缓存必然过期**。因此本 skill 不再使用 `data/cloud_*.csv` 里的历史
URL（2026-03 签发，已全部 403），而是**运行时实时取新签名**。

接口（逆向自剪映 11.5 自带 `res_pool.dll` 与 `canvas_agent/app` 前端）：

- Host `https://lv-api.ulikecam.com`
- 鉴权只要 query 里的 `aid=3704` + `effect_sdk_version`；不绑定机器或账号，无需 sign/cookie。
- 面板分类 `/artist/v1/panel/get_panel_info`（panel=`material-lib` / `audio` / `sticker` / `filter` / `effects2` / `text-template`）
- 素材列表 `/artist/v1/effect/get_resources_by_category_id`（`common_attr.download_info.url` 即当场签发直链）
- 素材搜索 `/artist/v1/effect/search`（effect_type=201）
- 单素材   `/artist/v1/effect/mget_item`
- 音乐     `/lv/v1/get_collections`、`/lv/v1/get_collection_songs`、`/lv/v1/search/songs`（`keyword` 参数）
- 音效     panel=`audio` 的 `get_resources_by_category_id`

命令行：

```powershell
python -X utf8 -m jyai.cli cloud-categories --panel material-lib          # 列分类
python -X utf8 -m jyai.cli cloud-search "绿幕" --count 10                  # 搜素材
python -X utf8 -m jyai.cli cloud-search "烟火" --kind music --count 5       # 搜音乐
python -X utf8 -m jyai.cli cloud-search "热门" --kind sound --count 5       # 搜音效
python -X utf8 -m jyai.cli cloud-list --category-id 10239 --count 20       # 列某分类
python -X utf8 -m jyai.cli cloud-get 7224413118917528884                   # 下载, 打印本地路径
```

上游 API：`JyProject.add_cloud_media(query)` 与 `add_cloud_music(query)` 现在都会实时解析
（`query` 可以是 `resource_id`，也可以是关键词）。spec 里也可直接引用：

```json
{"video": [{"cloud_query": "绿幕", "start": 0, "duration": 3.0},
           {"cloud_id": "7323307927278751027", "start": 3.0, "duration": 2.0}],
 "audio": [{"cloud_id": "6896679322715786510", "start": 0, "duration": 2.0}]}
```

刷新本地索引（可选，索引只提供名称与分类，下载永远走实时通道）：

```powershell
python -X utf8 scripts/sync_official_assets.py           # 全量
python -X utf8 scripts/sync_official_assets.py --quick   # 每个分类首页
```

命中优先级：本地 `Cache/artistEffect/<id>/`（剪映自己下过的，零网络）→ 官方实时直链 →
`item_urls`/`cover` 兜底 → 让剪映进程自己下载（native bridge）。

## 官方 CustomAgent 运行时（附着剪映进程）

完整路由/技能/处理器/工具清单见
[`references/agent-runtime-capabilities.md`](references/agent-runtime-capabilities.md)。

剪映 11.5 自带一整套 **CustomAgent 运行时**，由 bun 承载（`Resources/canvas_agent/runtime/app-server.mjs`）。
它有两种用法，本 skill 都支持：

| 通道 | 模块 | 是否有登录态 | 用途 |
| --- | --- | --- | --- |
| 隔离自启 | `jyai.appserver` | ❌ 无 host context | 无剪映也能起的本地 API；broker 类接口会 500 |
| **附着剪映** | `jyai.agentruntime` | ✅ 带 tdid/pf/appvr + cookie | 官方 Agent、云模型、素材/ASR/VLM、导出 |

附着通道的链路（逆向自 `VECreator.dll` + `app-server.mjs`，实测 2026-10）：

    JianyingPro.exe
      └─ VECreator.dll  进程内 native bridge
           %TEMP%\infinite-canvas-native-bridge.json  {port, token}
           └─ JSB getLocalAgentServerContext
                → {localServerToken, port=<agent server>}
                    └─ bun app-server  →  broker → lv-pc-api.ulikecam.com/openagent/v1/*

鉴权（实测）：

- native bridge：头 `x-infinite-canvas-native-token: <bridge token>`
- agent server：头 `x-custom-agent-token: <localServerToken>`（非空即通过）
- 需要 runtime 绑定的路由，再加 `x-agent-runtime-instance-id` 与 `x-agent-runtime-session-id`

```powershell
python -X utf8 -m jyai.cli agent-server        # agent server 坐标
python -X utf8 -m jyai.cli agent-modes         # 内置 Agent + 指令白名单
python -X utf8 -m jyai.cli agent-skills        # 官方 Agent 技能 (实测 14 个)
python -X utf8 -m jyai.cli agent-processors    # 官方处理器 (实测 8 个)

# 建带素材的官方创作任务 (目录自动展开、按扩展名过滤)
python -X utf8 -m jyai.cli agent-workspace "D:\素材" --task "剪成10秒竖屏短视频"
python -X utf8 -m jyai.cli agent-run <runId>          # 节点 / 可用指令
python -X utf8 -m jyai.cli agent-advance <runId> --times 3 --interval 6
python -X utf8 -m jyai.cli agent-say <runId> "再短一点"
python -X utf8 -m jyai.cli agent-logs                 # 服务端日志
```

Python：

```python
from jyai import agentruntime as ar
c = ar.AgentRuntimeClient().bind("my-task")
wp = c.prepare_workspace()
assets = c.register_local_paths([r"D:\素材"], workspace_path=wp)
out = c.create_workspace_task("剪成10秒竖屏短视频", assets, workspace_path=wp)
run_id = ar.AgentRuntimeClient.extract_run_id(out)
c.advance(run_id)
```

控制平面：`POST /api/agent-runtime/v1/instances:resolve`（201 新建）、
`GET .../instances/<id>/sessions`、`GET .../host/health`。
运行时指令：`POST /api/custom-agent/commands`，实测 32 种 kind（`SubmitTask` /
`AdvanceRun` / `OpenWorkspaceRun` / `SendUserMessage` / `RunStage` / `ConfirmPlan` /
`ResolvePermission` / `CancelRun` …）。

实测证据（`agent-workspace` 后推进 run，服务端日志）：

```
commerce_model_authorization_result   authorized:true   model: ds_responses_flash
workspace_chat_llm_progress_emit      estimatedTokens: 21742
workspace_readonly_tool_done          toolId: asset_list  status: succeeded
sandbox_process_exit                  label: built-in-tool:AnalyzeAsset
media_index_background_job_completed  kind: probe
```

即：官方 Agent 真的在调**云模型**（消耗账号 AI 点数）、跑内置工具、对素材做
分析并建媒体索引。

**边界（必须如实报告）**：

- `GET /api/custom-agent/host-capabilities/manifest|invoke` 只认 runtime 为子进程
  签发的进程级 grant。外部客户端即使带上 `x-custom-agent-token` + instance/session
  绑定，也只会得到 `401 invalid_host_capability_token`（实测），**不要伪造成功能**。
- 附着通道依赖剪映正在运行；`find_live_server()` 定位的是剪映**当前**那个 server，
  重启剪映后 port/token 会变（每次重新发现即可）。
- 长任务（真实 LLM + 素材分析）会让该 server 阻塞到分钟级，请求可能超时；
  用 `agent-logs` 看进度，或加大 timeout 后重试。

## 画质 (超清画质 / 补分辨率 / 一键超清)

导出面板里那三个开关在实现上是**三件不同的事**, 落点也不同:

| 面板项 | 实现 | 落点 | 代价 |
| --- | --- | --- | --- |
| `一键超清` (`pc_exp_ai_uhd`) | 导出策略, 自动挑画质提升能力 | `<草稿>/attachment_editing.json` → `editing_draft.is_use_one_click_ultra_hd` | 取决于它挑中什么 |
| `补分辨率` (`pc_super_resolution`) | **云端**超分 | 片段 `materials.videos[].video_algorithm.super_resolution` | 素材要**上传服务器排队**, 消耗时间/AI 点数 |
| `超清画质` / AI 画质提升 (`pc_feature_image_enhance`) | **本地**降噪/去频闪/锐化 | 片段 `video_algorithm.quality_enhance = {"from":"multi_track","level":N}` | 便宜, 不排队 |

`level`: 1 高清 / 2 超清 / 3 AI HD (取自 `videoeditor.dll` 的
`downgrade_136_to_135_quality_ai_hd_level`, `level == 3` 即 AI HD)。

**先说结论: 信息解说类视频通常不需要开。** 官方自己的判据是
`pc_export_super_resolution_tell`「素材已达到该分辨率，不需要再处理了」和
`pc_exp_ai_uhd_no`「素材已经足够清晰，暂不需要超清处理」——
素材本来就是 1080P、导出也是 1080P 时, 开了等于"零提升 + 上传排队 + 消耗点数"。
真需要的只有三种: 素材档位低于导出档 (如 720P 素材出 1080P)、老旧糊素材要救、要出 4K。

所以默认走 `--mode auto` **智能判定**, 不再无脑开。

```powershell
# 先看不写: 值不值得开
python -X utf8 -m jyai.cli quality-plan "草稿名"
python -X utf8 -m jyai.cli quality-plan "草稿名" --target-height 2160

# 智能判定后写入 (达标就自动跳过, 不浪费时间)
python -X utf8 -m jyai.cli quality-set "草稿名" --mode auto --panel

# 一句话剪视频时直接带上 (默认就是 auto)
python -X utf8 -m jyai.cli cut "D:\素材" --prompt "竖屏 每段3秒" --quality auto
python -X utf8 -m jyai.cli cut "D:\素材" --prompt "竖屏 每段3秒" --quality off

# 强制指定
python -X utf8 -m jyai.cli quality-set "草稿名" --mode quality_enhance --level 2
python -X utf8 -m jyai.cli quality-set "草稿名" --mode super_resolution --dry-run
python -X utf8 -m jyai.cli quality-off "草稿名"

python -X utf8 -m jyai.cli quality-state "草稿名"       # 当前状态
python -X utf8 -m jyai.cli quality-panel               # 导出面板默认值
```

Python:

```python
from jyai import quality as q
q.plan("草稿名")                                  # 只判定
q.apply("草稿名", mode="auto", target_height=1080) # 判定 + 写入
q.set_ultra_hd("草稿名", True)                     # 只开一键超清
```

判定口径: 比较**短边档位** (1920x1080 与 1080x1920 都是 1080P),
取最低档素材, 差距小于 13% 就不值得上传。
`infer_target_height()` 只认标准画布尺寸, 长图草稿 (如 1920x3414) 一律回退 1080P ——
画布高度不是导出分辨率, 两者不能混用。

边界 (必须如实报告):

- 片段级 `super_resolution` 的**非空结构**在本机全盘草稿里 0 例, 是按
  `settings_json:super_resolution_config.video_params` 的同名参数构造的。
  开启后请在剪映 GUI 复核一次。`quality_enhance` 有真实草稿佐证
  (`6月7日`: `{"from":"multi_track","level":1}`)。
- `User Data/Config/Export.ini` 的 `userFirstVideoEnhanceExport` 是导出面板的
  持久状态。剪映**运行时会用自己的内存态回写**, 所以 `--panel` 只在剪映关闭时生效
  (检测到运行中会跳过并说明)。
- 默认不写 `function_assistant_info.enhance_quality`: 实测真实草稿里片段已开启而该
  draft 级标记仍为 `False`, 说明权威字段是片段级 `video_algorithm`, 默认不碰。
  需要时用 `set_draft_flag=True`。

## 预览与人工接手

当前模式直接写草稿，不控制剪映窗口，不保证热加载。修改目标草稿前先从编辑器返回首页；现有代码没有可靠的打开占用检测。

推荐 AI 保存一轮 → 用户打开预览/修改 → 剪映保存并返回首页 → AI 重新读取最新草稿再续剪。用户要求停下预览时停止后续写入。不要一边让剪映编辑同一工程，一边写它的文件；不要把可打开草稿说成已经实现实时 GUI 控制。

## 格式与验证

- `content.id` 对齐活动 timeline ID，不是目录 meta 或首页的 `draft_id`。
- 根内容、活动时间线和备份同步；schema `new_version=187.0.0` 来自该版本原生保存结果。
- 内容与目录 meta 使用已安装 `videoeditor.dll` 的 DraftIO；写入后回读验证，`template.json` 保留明文工作副本。
- 首页 `draft_ids` 保留原值；素材大小来自实际本地文件，不写死 0。
- 不以“磁盘开关关闭”推断运行时格式，不需要 Frida、关闭加密或修改安装文件。

`inspect <名称/路径>` 读草稿；`doctor` 检查结构、素材和登记；`repair <名称/路径>` 修复指定生成草稿。不用于无限画布。

静态检查、编解码、实际 GUI 打开、原生导出分别验收。2026-09-30 用户已确认一个生成的 12 秒、4 视频片段、1 文字轨草稿实际打开；不据此声称所有效果或所有版本已验证。

## 导出

原生导出：在剪映打开并使用右上角“导出”。旧 `JianyingController.export_draft()` 未验证为 11.5 的可靠自动接口。

```powershell
python -X utf8 -m jyai.cli render "AI_产品展示" -o "D:\导出\产品展示.mp4"
```

这是 FFmpeg 基础单视频轨渲染，支持连续裁剪、画幅适配、基础文字和音频，不保证与剪映布局完全一致。特效、转场、贴纸、多视频轨使用剪映原生导出。上游 `JyProject.export_video()` 本地 Windows 分支调用基础渲染，检测到不支持结构返回 `native_export_required`，不能报告成功。

保留的画布、Agent、录屏、特效/云素材等参考资料仅在实际涉及对应功能时读取；旧上游资料不覆盖本版的兼容与验证边界。
