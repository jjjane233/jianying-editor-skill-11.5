# 剪映 11.5 官方 CustomAgent 运行时 — 逆向能力清单

逆向自 `Resources/canvas_agent/runtime/app-server.mjs`（5.5 MB / bun 承载）
与 `VECreator.dll`，实测版本 **Windows 剪映专业版 11.5.0.14471**。

## 附着方式

```
JianyingPro.exe
  └─ VECreator.dll  native bridge   %TEMP%\infinite-canvas-native-bridge.json
       └─ JSB getLocalAgentServerContext  → {localServerToken, port}
            └─ bun app-server             → broker → lv-pc-api.ulikecam.com
```

鉴权头：

- native bridge：`x-infinite-canvas-native-token: <bridge token>`
- agent server：`x-custom-agent-token: <localServerToken>`
- runtime 绑定：`x-agent-runtime-instance-id` + `x-agent-runtime-session-id`

## 控制平面 (agent-runtime)

- `GET /api/agent-runtime/v1/host/health`
- `POST /api/agent-runtime/v1/instances:resolve`
- `GET /api/agent-runtime/v1/instances/{id}/sessions`
- `POST /api/agent-runtime/v1/instances/{id}/sessions`
- `POST /api/agent-runtime/v1/host/app-context`
- `POST /api/agent-runtime/v1/host/auth-context`

## 产品平面路由（86 条，实测存在于打包运行时）

- `GET /api/agent-runtime/v1/evaluation/runtime/runs/detail`
- `GET /api/agent-runtime/v1/evaluation/runtime/state`
- `POST /api/custom-agent/artifacts/save-final-video`
- `GET /api/custom-agent/asset-operations`
- `PATCH /api/custom-agent/asset-operations`
- `POST /api/custom-agent/asset-operations`
- `POST /api/custom-agent/asset-operations/complete`
- `GET /api/custom-agent/assets/contents`
- `POST /api/custom-agent/assets/generated`
- `POST /api/custom-agent/assets/reveal-in-folder`
- `POST /api/custom-agent/assets/reveal-target`
- `POST /api/custom-agent/assets/reveal-workspace-folder`
- `POST /api/custom-agent/assets/reveal-workspace-target`
- `POST /api/custom-agent/authoring/agent-profile/complete`
- `POST /api/custom-agent/authoring/agent-profile/tags`
- `POST /api/custom-agent/biz-configs`
- `POST /api/custom-agent/canvas/scripts/actions/generate-edit-storyboard`
- `POST /api/custom-agent/canvas/scripts/actions/generate-editing-film`
- `POST /api/custom-agent/commands`
- `GET /api/custom-agent/commands/receipt`
- `POST /api/custom-agent/commerce/get_triplet_config`
- `POST /api/custom-agent/diagnostics/execute`
- `GET /api/custom-agent/diagnostics/fornax-traces`
- `GET /api/custom-agent/diagnostics/host-environment`
- `POST /api/custom-agent/diagnostics/native-crash`
- `POST /api/custom-agent/diagnostics/native-crash/abort`
- `POST /api/custom-agent/diagnostics/native-crash/access`
- `GET /api/custom-agent/fornax-traces`
- `POST /api/custom-agent/host-capabilities/invoke`
- `GET /api/custom-agent/host-capabilities/manifest`
- `POST /api/custom-agent/host-context`
- `POST /api/custom-agent/infra/auth/start`
- `POST /api/custom-agent/infra/auth/status`
- `GET /api/custom-agent/local-assets`
- `POST /api/custom-agent/local-assets`
- `POST /api/custom-agent/local-assets/materialize`
- `POST /api/custom-agent/local-assets/scoped`
- `POST /api/custom-agent/local-inputs/expand-folders`
- `POST /api/custom-agent/local-inputs/import-web-drop`
- `POST /api/custom-agent/local-inputs/import-web-drop-folder`
- `GET /api/custom-agent/local-inputs/metadata`
- `POST /api/custom-agent/local-inputs/pick`
- `GET /api/custom-agent/local-inputs/preview`
- `GET /api/custom-agent/local-inputs/preview-status`
- `GET /api/custom-agent/local-inputs/read`
- `POST /api/custom-agent/local-inputs/register-paths`
- `POST /api/custom-agent/local-write-access/authorize-directory`
- `GET /api/custom-agent/local-write-access/roots`
- `GET /api/custom-agent/logs`
- `POST /api/custom-agent/logs`
- `GET /api/custom-agent/mcps`
- `POST /api/custom-agent/mcps/install`
- `POST /api/custom-agent/mcps/uninstall`
- `DELETE /api/custom-agent/model-providers/custom`
- `GET /api/custom-agent/model-providers/custom`
- `PATCH /api/custom-agent/model-providers/custom`
- `POST /api/custom-agent/model-providers/custom`
- `POST /api/custom-agent/model-providers/custom/test`
- `POST /api/custom-agent/pick-working-directory`
- `POST /api/custom-agent/projects/covers`
- `POST /api/custom-agent/runs/actions/advance`
- `POST /api/custom-agent/runs/stages/actions`
- `GET /api/custom-agent/runtime/state/summary`
- `GET /api/custom-agent/skills`
- `POST /api/custom-agent/skills/command/parse`
- `POST /api/custom-agent/skills/disable`
- `POST /api/custom-agent/skills/enable`
- `POST /api/custom-agent/skills/install`
- `GET /api/custom-agent/skills/local`
- `GET /api/custom-agent/skills/mentionable`
- `POST /api/custom-agent/skills/uninstall`
- `DELETE /api/custom-agent/tasks`
- `PATCH /api/custom-agent/tasks`
- `POST /api/custom-agent/tasks/workspace`
- `GET /api/custom-agent/tasks/workspace-assets`
- `PATCH /api/custom-agent/tasks/workspace-assets`
- `POST /api/custom-agent/tasks/workspace-assets/assets`
- `GET /api/custom-agent/tasks/workspace-canvas`
- `PATCH /api/custom-agent/tasks/workspace-canvas`
- `POST /api/custom-agent/tasks/workspace-canvas/assets`
- `POST /api/custom-agent/tasks/workspace/prepare`
- `POST /api/custom-agent/tos/upload`
- `GET /api/custom-agent/workflows`
- `POST /api/processor-executions`
- `GET /api/processor-executions/:processorExecutionId`
- `GET /api/processors`

## 官方 Agent 技能（`GET /api/custom-agent/skills`，实测 14 个）

- `aigc-character-asset-creator` — AIGC 角色资产创建器
- `aigc-text-to-singlefilm` — AIGC单集文字成片
- `lark-shared` — 飞书认证与共享能力
- `lark-doc` — 飞书文档
- `fornax-run-trace` — 当前运行 Trace 查询
- `media-acquisition` — 媒体导入与物化
- `media-collection` — 媒体收集与物化
- `script-logic` — Script 镜头结构
- `platform_adaptation` — 平台适配
- `brand_guidelines` — 品牌规范
- `audio_rhythm` — 音频节奏
- `audio_voice_first` — 音频人声优先
- `studio.edit.reference_recall` — 通用包装风格
- `studio.lyra.xml_timeline_builder` — XML草稿编排

## 官方处理器（`GET /api/processors`，实测 8 个）

- `aigc_reference_image_preprocess` [image] — 生图参考图预处理
- `aigc_reference_video_preprocess` [video] — 生视频参考视频预处理
- `capture_video_frames` [video] — 视频截帧
- `change_audio_speed` [audio] — 音频变速
- `compose_finished_video` [finished_video] — 生成成片
- `crop_image` [image] — 裁剪图片
- `trim_audio` [audio] — 裁剪音频
- `trim_video` [video] — 裁剪视频

## 运行时指令 kind（`POST /api/custom-agent/commands`）

`SubmitTask` `AdvanceRun` `RunNextStep` `OpenWorkspaceRun` `OpenTask`
`SendUserMessage` `ConfirmPlan` `ConfirmPlanAndContinue` `ResolvePermission`
`ResolvePermissionAndContinue` `ApprovePermission` `RunStage` `CancelRun`
`CancelStage` `RecoverRun` `DispatchAvailableCommand` `RouteAgentMode`
`RegenerateStage` `RegenerateRuntimeOutput` `SelectStageHistoryAsset`
`DisableRuntimeOutput` `ResolveMemoryProposal` `UpsertAgent` `RegisterAgent`
`UpdateAgent` `DisableAgent` `RemoveAgent` `SetAgentAvailability` `RemoveTask`
`RenameTask` `ResetUserState` `ResolveProviderAuth`

## 内置 Agent

| id | 角色 | 输出契约 |
| --- | --- | --- |
| `v2-director-agent` | 创作统筹（main） | — |
| `v2-script-agent` | 文案与粗剪内容选择（sub） | `script` |
| `v2-editing-agent` | 时间线装配与包装（sub） | `edited_draft` |
| `v2-review-agent` | 证据驱动审片（sub） | `review_report` |

## 内置 Agent 工具 (toolId)

`AnalyzeAsset` `MaterialRecall` `SegmentRecall` `MediaIndex` `DraftEditing`
`EditStoryboard` `PlanCreate` `PlanUpdate` `SubmitResult` `Skill` `Agent`
`Read` `Write` `Edit` `MultiEdit` `Bash` `PowerShell` `Grep` `Glob` `LS`
`WebFetch` `WebSearch` `TodoWrite` `SendMessage`

媒体/资产：`asset_list` `asset_get` `asset_import` `asset_download`
`asset_operation_get` `submit_draft_nodes`

AIGC：`generate_image` `generate_audio` `generate_aigcharacter`
`compose_aigcasset` `compose_aigcfilm` `compose_aigcscript` `compose_aigcstoryboard`
`image_to_aigcasset`

## host capabilities（进程级，外部不可调用）

`asr.transcribe` `vlm.media_analyze` `vlm.media_recall` `vlm.media_timeline`

这四个由 runtime 为**子进程**签发 grant 后才可调用（`CUSTOM_AGENT_HOST_CAPABILITY_TOKEN`）。
外部客户端带 `x-custom-agent-token` + instance/session 绑定仍返回
`401 invalid_host_capability_token`（实测）。

## 实测证据

```
commerce_model_authorization_result   authorized:true   model: ds_responses_flash
workspace_chat_llm_progress_emit      estimatedTokens: 21742
workspace_readonly_tool_done          toolId: asset_list  status: succeeded
sandbox_process_exit                  label: built-in-tool:AnalyzeAsset
media_index_background_job_completed  kind: probe
```

即官方 Agent 真实调用云端模型（消耗账号 AI 点数）、执行内置工具、
分析素材并建立媒体索引。

