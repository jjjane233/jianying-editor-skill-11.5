# Project guidance

- The supported native draft codec is Windows Jianying Pro 11.5.0.14471 only.
- Quality features (one-click ultra HD / super-resolution / quality-enhance) are drafts-only
  writes: `editing_draft.is_use_one_click_ultra_hd` in `attachment_editing.json` and
  `video_algorithm.quality_enhance` / `.super_resolution` per segment. Super-resolution uploads
  media to the cloud and queues; default to `--mode auto` and report the recommendation instead
  of enabling it unconditionally.
- The maintained entrypoint is `skills/jianying-editor-11-5/SKILL.md`; older upstream docs are historical references.
- Current editing writes draft files, not live GUI actions. Return the target project to the home screen before writing it; reload saved user edits before incremental editing.
- The official CustomAgent runtime is reachable through `jyai.agentruntime` (attaches to the Jianying process via the native bridge JSB). Capability survey lives in `skills/jianying-editor-11-5/references/agent-runtime-capabilities.md`. Runtime broker calls require Jianying to be running and consume the account's cloud AI credits; never report them as offline/local capability.
- Preserve upstream and vendored licenses. Do not commit user media, actual drafts, machine indexes, credentials, DLLs, screenshots or local backups.
- Run `python -X utf8 -m unittest discover -s tests -v`; on a prepared Windows machine also run `python -X utf8 tests/smoke_native.py`.
- Static and codec checks do not establish actual GUI opening or native export. Report those stages separately.
