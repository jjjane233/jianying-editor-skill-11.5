# 剪映 AI 剪辑 Skill · Windows 11.5

**简体中文 | [English](README.en.md)**

让本机 AI 代理（Codex / Claude Code / 任何能读写文件并执行 Python 的 Agent）把你丢进来的素材，剪成**可以在剪映里打开、继续人工编辑的草稿**：一句话生成、精确构建、增量修改、官方素材库取片、画质增强。

**已验证版本：剪映专业版 Windows `11.5.0.14471`，64 位 Python 3.10+。**

## 与原版的关系

本仓库是 [isYangs/jianying-editor-skill](https://github.com/isYangs/jianying-editor-skill) 的升级适配版：

| | 原版（上游） | 本仓库 |
| --- | --- | --- |
| 仓库 | [isYangs/jianying-editor-skill](https://github.com/isYangs/jianying-editor-skill) | 本仓库 |
| 剪映版本 | **只能操作剪映 5.9 左右**（自动导出要求 5.9 或更低） | **剪映专业版 Windows 11.5.0.14471** |
| 草稿保存 | 上游模板/明文流程 | 11.5 原生加密草稿：调用本机 `videoeditor.dll` 的 DraftIO 原生保存、回读验证、登记首页 |
| 素材库 | 静态缓存的历史 URL | 剪映官方素材接口**运行时实时取签名**（静态 CDN 直链必然过期） |
| 画质 | — | 超清画质 / 补分辨率 / 一键超清三件套，`--mode auto` 智能判定 |

上游的 API、示例、参考资料与 LICENSE 均保留在 `skills/jianying-editor-11-5/` 内（`README.upstream.md` 为上游原始说明）。**5.9 时代的能力描述不代表本版已适配**，11.5 的边界以本仓库 `SKILL.md` 与 [docs/验证记录.md](docs/验证记录.md) 为准。

## 核心功能：能做什么

| 能力 | 说明 | 状态 |
| --- | --- | --- |
| 一句话生成草稿 | `cut "素材目录" --prompt '竖屏 每段3秒 总共12秒 加标题 "…"'` | ✅ 主流程 |
| 精确构建 | `build <spec.json>`：素材、源起点、目标起点、时长、画幅、音频、文字逐条指定 | ✅ 主流程 |
| 增量修改 | `patch <草稿> <edits.json>`：重读最新内容再改，保留未触及字段 | ✅ 主流程 |
| 原生 11.5 草稿 | 加密内容 + 活动时间线 + 封面 + 首页登记，写后回读验证 | ✅ 已实机打开验证 |
| 官方素材库 | 片头/热梗/背景/绿幕/贴纸/滤镜/音效/音乐实时签名取用（`cloud-search` / `cloud-list` / `cloud-get`） | ✅ 实时接口 |
| 画质三件套 | 超清画质（本地）/ 补分辨率（云端超分）/ 一键超清；`--quality auto` 不值得就跳过 | ✅ 默认 auto |
| 草稿体检 | `inspect` / `doctor` / `repair`：读结构、查素材路径与登记、修指定草稿 | ✅ |
| 基础渲染 | `render`：FFmpeg 单视频轨 MP4（连续裁剪、画幅适配、基础文字与音频） | ⚠️ 基础能力，不等于剪映导出 |
| 官方 CustomAgent 运行时 | `jyai.agentruntime` 附着剪映进程，调官方 Agent 与云模型 | ⚠️ 需剪映在运行，**消耗账号 AI 点数** |
| 转场 / 特效 / 贴纸 / 多轨 | 上游接口与资源保留 | ⚠️ 逐项验证中，不承诺全兼容 |

自然语言里**“理解画面、选精彩片段、安排叙事”**这部分由 AI 代理及其实际可用的视觉/音频工具完成；`cut --prompt` 本身只是规则解析器（画幅、秒数、引号文字、静音、帧率），不是大模型或语音识别服务。

## 核心边界：不能做什么

- ❌ **不是实时 GUI 自动化**：不点鼠标、不拖窗口，写的是草稿文件；“能打开草稿”≠“已实现实时控制剪映”。
- ❌ **不支持人机同时编辑同一草稿**：采用分阶段接手（AI 存一轮 → 你回首页预览修改 → 保存并返回首页 → AI 重读最新内容再续剪）。
- ❌ **不提供剪映原生全保真自动导出**：已验收的是在剪映里手动“导出”；`render` 只是基础 FFmpeg 渲染。
- ❌ **不覆盖版本范围**：只验证 Windows `11.5.0.14471`；其他剪映版本需重新验证编解码 ABI，macOS 未验证。
- ❌ **不破解、不改装**：不关闭草稿加密、不修改剪映安装文件、不注入进程、不附带任何剪映 DLL——加解密走本机已安装的官方 `videoeditor.dll`。
- ❌ **不承诺凑满时长**：素材不足时如实报告实际成片时长。
- ⚠️ **补分辨率（超分）会把素材上传云端排队**，消耗时间与账号 AI 点数；默认 `auto` 只在值得时才开。

## 实现思路

1. **写草稿文件，而不是操作窗口。** 剪映的草稿 = 一个目录（`draft_meta_info.json` 等元信息 + 加密的 `draft_content` 时间线 + 素材引用）+ 首页索引里的 `draft_ids` 登记。把这三处写对，剪映就能像打开人工草稿一样打开它。
2. **加密不破解，借官方的手。** 11.5 的内容是原生加密的，本仓库在写入/回读时调用本机已安装 `videoeditor.dll` 提供的 DraftIO 完成编解码，因此不需要 Frida、不需要关加密、不需要改安装文件；`schema new_version=187.0.0` 来自该版本的原生保存结果，`content.id` 对齐活动 timeline ID（不是目录 meta 或首页 `draft_id`）。
3. **适配桥 `jyai` 把“意图”翻译成“时间线”。** `spec`（片段/起点/时长/画幅/文字/音频）→ 生成轨道与片段结构、封面、素材登记 → 原生加密保存 → 登记首页 → 写后回读自检。`patch` 永远先重读磁盘上最新的草稿再增量修改，绝不从旧清单重建。
4. **规则的归规则，语义的归 AI。** `--prompt` 只解析可量化的规则；“哪段是精彩画面、剧情怎么排、哪里卡点、说了什么”由 AI 代理用它真正可用的视觉/转录工具分析后，落成一份片段清单再交给 `build`。
5. **官方素材走实时签名。** 逆向自剪映自带 `res_pool.dll` 与前端的素材接口，CDN 直链是短时签名，静态缓存必然过期；因此列表、搜索、下载全部在运行时现场取新签名，命中顺序：本地 `Cache/artistEffect/`（剪映下过的，零网络）→ 官方实时直链 → 让剪映进程自己下载。
6. **画质默认克制。** 对比素材与导出档位的短边差距，差距小于阈值就跳过——官方自己的判据也是“素材已足够清晰，暂不需要处理”，避免零提升还上传排队烧点数。
7. **人机协作以“首页”为界。** 草稿被剪映打开编辑时不写它的文件；每轮之间回到首页、保存、重新读取，避免覆盖你在剪映里做的修改。

## 快速开始

Windows PowerShell：

```powershell
git clone https://github.com/jjjane233/jianying-editor-skill-11.5.git
cd jianying-editor-skill-11.5
python -m pip install -r requirements.txt
python -X utf8 tools/preflight.py
```

如果你的 Python 命令是 `py -3`，把上面的 `python` 换成 `py -3`。预检只检查环境（Python 位数、剪映 DLL、依赖、草稿根目录），不修改剪映也不动你的草稿。

给 AI 这段话（路径换成你自己的）：

```text
读取并使用 C:\work\jian-ying-skill\skills\jianying-editor-11-5\SKILL.md。
把 D:\视频素材 中的视频剪成竖屏，每段3秒，总共12秒，
加标题“产品展示”，新建草稿“AI_产品展示”。
不要覆盖已有草稿；完成后检查素材路径、时间线和草稿登记，并告诉我实际保存位置。
```

也可以双击 `一句话剪视频.bat`，或直接跑：

```powershell
python -X utf8 jianying.py cut "D:\视频素材" --prompt '竖屏 每段3秒 总共12秒 加标题 "产品展示"' --name "AI_产品展示"
```

## 仓库结构

```
skills/jianying-editor-11-5/   # skill 本体：SKILL.md(AI 说明书) + scripts/jyai 后端 + 上游 API/素材/规则
docs/                          # 中文使用文档、验证记录、更新记录、人工接手说明
examples/                      # spec / edits 示例
tests/                         # 单元测试与原生 smoke 测试
tools/                         # preflight 环境预检、install_skill 安装到技能目录
jianying.py / 一句话剪视频.*     # 仓库入口
```

## 文档

- [中文使用文档](docs/使用文档.md)：环境准备、调用 skill、生成草稿、人工修改、导出与排错
- [实时画面与人工接手](docs/实时画面与人工接手.md)：当前“写草稿”模式与 GUI 模式的区别
- [验证记录](docs/验证记录.md)：哪些已实机通过、哪些尚未验证
- [更新记录](docs/更新记录.md)：每个版本新增了什么、边界在哪
- [SKILL.md](skills/jianying-editor-11-5/SKILL.md)：AI 执行入口

验证命令：

```powershell
python -X utf8 -m unittest discover -s tests -v
python -X utf8 skills/jianying-editor-11-5/tools/check_repo_hygiene.py
```

静态检查、编解码、实际 GUI 打开、原生导出是**四个分别验收的阶段**，互不替代。

## 许可

基于上游 [isYangs/jianying-editor-skill](https://github.com/isYangs/jianying-editor-skill)（源快照 `a20c522`）适配，保留原作者 MIT LICENSE（版权行 `Copyright (c) 2026 luoluoluo22`）及 vendored [pyJianYingDraft](https://github.com/GuanYixuan/pyJianYingDraft)（Apache-2.0，© GuanYixuan）的许可文件，见根目录 `LICENSE` 与 `skills/jianying-editor-11-5/LICENSE`；`scripts/vendor/pyJianYingDraft/LICENSE` 为 Apache-2.0 全文。

仓库不包含用户素材、真实草稿、调试截图、剪映 DLL、密钥或本机索引。

本项目是**第三方独立工具**，与字节跳动及其剪映 / CapCut 官方无任何隶属或背书关系；「剪映」及相关商标、产品名称归其权利人所有，此处仅作指代用途。

## 免责声明

- 本项目按「原样」提供，**不附带任何明示或暗示的担保**，亦不对特定用途的适用性作任何承诺（详见 LICENSE 中的 MIT 条款）。
- 任何个人或组织使用本项目生成、修改、导出、发布的视频、草稿、素材及其他产物，**其内容、版权、名誉与法律后果均由使用者本人承担，与本仓库作者无关**。
- 使用者须自行确保拥有所用素材的合法权利，并遵守剪映 / CapCut 用户协议、平台规则及所在地法律法规；因违规使用产生的一切纠纷、损失或处罚，作者概不负责。
- 在法律允许的最大范围内，作者不对因使用或无法使用本项目造成的任何直接、间接、附带或后果性损失承担责任。
- 本项目文档中的示例、截图与验证记录仅说明技术事实，不构成任何商用、发布或规避平台规则的建议。

## 交流与支持

欢迎加入交流群，讨论剪映 Skill 的使用、报错与新版本适配：

- **QQ 群号：`1092215676`**
- [点此进群](https://qm.qq.com/cgi-bin/qm/qr?k=fAo0-5AHp6tCcG6-BozfatbLPcVw2Y9H&jump_from=webapi&authKey=nvoxE003gnKiqGPM1EJnkyAsO4DO0oKBfJCofWA0EcbZnoIBNZvv9ELpi2Yj21TS)

配套 **AI 中转站网关**（Agent 调用模型的 API 服务）：<https://www.pursueray.cn/>
