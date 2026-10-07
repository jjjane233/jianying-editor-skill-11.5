# 剪映 11.5 画质能力 — 逆向证据

逆向自 Windows 剪映专业版 **11.5.0.14471**：
`videoeditor.dll` / `VECreator.dll` / `Resources/po/zh-Hans.po` /
`User Data/MMKV/settings_json` / 真实草稿。

## 命名对照（po 文案 → 内部标识）

| UI 文案 | msgid | 内部标识 |
| --- | --- | --- |
| 一键超清 | `pc_exp_ai_uhd` / `pc_m10n_export_ultra_hd_switch` | `export_one_click_ultra_hd` / `is_use_one_click_ultra_hd` |
| 超清导出中 | `pc_exporting_in_ultra_hd` / `pc_exp_ai_uhd_ing` | — |
| 补分辨率 | `pc_export_super_resolution` / `pc_super_resolution` | `super_resolution` |
| 超清画质 | `pc_feature_image_enhance` / `pc_recommend_quality_enhance` | `quality_enhance` |
| AI 画质提升 | `pc_enhance_quality` | `quality_enhance` |
| AI HD 超清 | `pc_ai_hd` | `quality_enhance.level == 3` |

关键提示文案（判定语义直接来自官方）：

```
pc_export_super_resolution_tell   = 素材已达到该分辨率，不需要再处理了
pc_export_super_resolution_remind = 智能将素材补分辨率到设置值，让画面更清晰
pc_export_super_resolution_unify  = 检测到部分片段使用此功能，开启后将应用于全部片段
pc_export_super_resolution_auto_start = 检测到全草稿使用了此功能，当前自动开启
pc_exp_ai_uhd_intro  = 智能使用画质提升能力（超清画质、补分辨率、AI补帧），使导出视频更清晰流畅
pc_exp_ai_uhd_no     = 素材已经足够清晰，暂不需要超清处理
pc_quality_4k_enough = 4k的视频已经足够清晰，不需要再做超清处理啦
pc_m10n_ultra_hd_conflict_toast = 无法处理，当前画质已经最高或者格式不支持
pc_export_super_resolution_warning = 当前为关闭状态，草稿中已应用的效果会消失
```

## 云端 vs 本地

`pc_enhance_quality_pop_up_agree`：

> 为了增强您所选素材的画质，**需将其上传到我们的服务器进行处理**。当超清画质处理完成后，
> 我们将立即删除上传内容……

`videoeditor.dll` 字符串：

```
LYRA_CLI_UPLOAD_VID
super_resolution upload timeout
upscale_resolution upload failed
upscale_resolution input/output path empty
upscale_resolution missing input width/height
upscale_resolution timeout
play_url / upscale_resolution result missing url
upscale_resolution output file check failed
sr_plugin / target_width / target_height / 4x_mode
```

`settings_json`：

```json
"super_resolution_config": {
  "duration_limit": 30,
  "image_async": false,
  "image_async_params": {"4x_mode": true, "level": 2, "log_level": 1},
  "image_params":       {"4x_mode": true, "level": 2, "log_level": 1},
  "support_hdr": true,
  "unsupport_suffix": ["m2ts","m2t","tiff","mpeg","mpg","m2v","vob","cr2",...],
  "video_params": {"4x_mode": true, "cq": 1, "crf": 20, "gop": 30,
                   "level": 3, "log_level": 1, "preset": 1, "profile": 1}
},
"quality_enhance_setting":        {"enable_v2": true},
"quality_enhance_setting_config": {"cancel_server": false, "duration_limit": 30,
                                   "resolution_limit_image": 6144,
                                   "resolution_limit_video": 4096,
                                   "support_hdr": false,
                                   "trial_duration_limit": 1800},
"super_resolution_ab_test": {"group": 3}
```

`CommonSetting.ini`：`quality_enhance_last_choose=1`（档位记忆）、
`super_resolution_used=false`（用过没）。
`Export.ini`：`userFirstVideoEnhanceExport`（导出面板持久状态）。

## 落点

**草稿级**（`attachment_editing.json`，根级与 `Timelines/<tid>/` 各一份）：

```
editing_draft.is_use_one_click_ultra_hd : bool
```

同文件还有一组同类开关：
`is_use_one_click_beauty` `is_use_retouch_face` `is_use_noise_reduction`
`is_use_audio_separation` `is_use_loudness_unify` `is_use_subtitle_recognition`
`is_use_chroma_key` `is_use_smart_motion` `is_use_text_to_audio` …（共 45 键）

**片段级**（`draft_content.json` → `materials.videos[].video_algorithm`）：

```json
{
  "quality_enhance": null,          // 开启后: {"from":"multi_track","level":N}
  "super_resolution": null,         // 开启后: {"from":"multi_track", ...params}
  "noise_reduction": null,
  "deflicker": null,
  "algorithms": [],
  "motion_blur_config": null,
  "complement_frame_config": null,
  "smart_complement_frame": null,
  "ai_background_configs": [],
  "gameplay_configs": [],
  "path": "",
  "skip_algorithm_index": [],
  "time_range": null
}
```

`level` 取自 `videoeditor.dll` 的迁移脚本：
`downgrade_136_to_135_quality_ai_hd_level` —— `level == 3` 即 AI HD；
`downgrade_148_to_147_video_ai_hd_level` —— 非 `photo` 类型才降级。

**draft 级记录**（`function_assistant_info`）：

```
enhance_quality              : bool
enhance_quality_fixed        : bool
enhance_quality_segid_list   : list[segment_id]
```

实测 `6月7日` 草稿：片段已有 `{"from":"multi_track","level":1}`，
而 `function_assistant_info.enhance_quality` 仍为 `false` ——
**权威字段是片段级 `video_algorithm`**，draft 级标记不是生效必需。

## DraftService 接口（原生，未走）

`videoeditor.dll` / `VECreator.dll` 导出符号：

```
setSuperResolution / startConvertSuperResolution / cancelSuperResolution
notifySuperResolutionComplete / getSuperResolutionPath
SuperResolutionService
applySuperResolutionToAllAction
setQualityEnhance / startConvertQualityEnhance / cancelQualityEnhance
getQualityEnhancePath / QualityEnhanceService
set_is_use_one_click_ultra_hd (AttachmentEditingDraft)
```

这些是进程内接口，外部客户端要经过 native bridge 的 JSB 才会调到；
本模块直接写草稿文件，不依赖剪映在跑。

## JSB 方法表（`VECreator.dll` @208425300）

```
openExportWindow  openShortcutPanel  exportDraft  enterMultitrack
selectMedia  importMedia  importMediaV2  getMaterialListV2
notifyExportEnableState  notifyPreviewEnableState  notifyDownloadEnableState
getRemoveAigcWatermark  setRemoveAigcWatermark  ...
```

`exportDraft` 的参数结构未进一步解出；导出面板是原生 Qt UI，
没有走 web 前端的开关。一键超清是通过草稿字段 + 导出面板状态驱动的。
