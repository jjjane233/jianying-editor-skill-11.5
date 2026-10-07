# JianYing AI Editing Skill · Windows 11.5

**English | [简体中文](README.md)**

Turn your footage into **a JianYing (CapCut CN) draft you can open and keep editing by hand** — one-liner generation, precise builds, incremental patches, official asset-library fetching, and quality enhancement — driven by a local AI agent (Codex / Claude Code / any agent that can read & write files and run Python).

**Verified build: JianYing Pro for Windows `11.5.0.14471`, 64-bit Python 3.10+.**

## Relationship to the original

This repository is an upgraded adaptation of [isYangs/jianying-editor-skill](https://github.com/isYangs/jianying-editor-skill):

| | Original (upstream) | This repository |
| --- | --- | --- |
| Repo | [isYangs/jianying-editor-skill](https://github.com/isYangs/jianying-editor-skill) | This repo |
| JianYing version | **Works with JianYing ~5.9 only** (auto-export requires 5.9 or lower) | **JianYing Pro for Windows 11.5.0.14471** |
| Draft saving | Upstream template / plaintext flow | Native encrypted 11.5 drafts: native save & read-back verification via the installed `videoeditor.dll` DraftIO, registered on the home screen |
| Asset library | Statically cached historical URLs | **Freshly signed at runtime** through JianYing's official asset APIs (static CDN links always expire) |
| Quality tools | — | Enhancement / super-resolution / one-click ultra-HD trio with smart `--mode auto` decisions |

The upstream APIs, examples, references and LICENSE are all kept under `skills/jianying-editor-11-5/` (`README.upstream.md` is the original upstream readme). **Feature claims from the 5.9 era do not imply support in this build** — the 11.5 boundaries are defined by this repo's `SKILL.md` and [docs/验证记录.md](docs/验证记录.md) (verification log, Chinese).

## What it can do

| Capability | Details | Status |
| --- | --- | --- |
| One-liner draft generation | `cut "media dir" --prompt 'portrait 3s per clip, 12s total, add title "…"'` | ✅ Main flow |
| Precise build | `build <spec.json>`: media, source start, target start, duration, aspect ratio, audio and text — item by item | ✅ Main flow |
| Incremental edits | `patch <draft> <edits.json>`: re-reads the latest content first, untouched fields are preserved | ✅ Main flow |
| Native 11.5 drafts | Encrypted content + active timeline + cover + home-screen registration, verified by reading back after writing | ✅ Verified opened in the real app |
| Official asset library | Intros, memes, backgrounds, green screen, stickers, filters, sound effects, music — signed in real time (`cloud-search` / `cloud-list` / `cloud-get`) | ✅ Live API |
| Quality trio | Enhancement (local) / super-resolution (cloud) / one-click ultra-HD; `--quality auto` skips when it isn't worth it | ✅ auto by default |
| Draft diagnostics | `inspect` / `doctor` / `repair`: read structure, check media paths & registration, repair a given draft | ✅ |
| Basic rendering | `render`: single-track FFmpeg MP4 (sequential cuts, aspect fit, basic text & audio) | ⚠️ Basic — not JianYing's native export |
| Official CustomAgent runtime | `jyai.agentruntime` attaches to the JianYing process and calls the official agent & cloud models | ⚠️ Requires JianYing running; **consumes account AI credits** |
| Transitions / effects / stickers / multi-track | Upstream APIs and resources retained | ⚠️ Verified case by case, full compatibility not promised |

The **"understand the footage, pick the highlights, arrange the narrative"** part of natural language is done by the AI agent with whatever vision/audio tools it actually has; `cut --prompt` itself is only a rule parser (aspect ratio, seconds, quoted text, mute, frame rate) — not an LLM or speech-recognition service.

## What it cannot do

- ❌ **Not real-time GUI automation**: no clicking, no window dragging — it writes draft files. "The draft opens" ≠ "live control of JianYing".
- ❌ **No simultaneous human + AI editing of the same draft**: use staged hand-off (AI saves a round → you return to the home screen, preview & edit → save and go back home → AI re-reads the latest draft and continues).
- ❌ **No full-fidelity native auto-export**: what's verified is the manual "Export" button inside JianYing; `render` is basic FFmpeg output only.
- ❌ **No version coverage beyond**: only Windows `11.5.0.14471` is verified; other JianYing versions need codec-ABI re-verification, macOS is unverified.
- ❌ **No cracking, no modification**: draft encryption is never disabled, no JianYing install files are changed, no process injection, and no JianYing DLLs are bundled — encode/decode goes through the officially installed `videoeditor.dll`.
- ❌ **No promise to fill the requested duration**: if media is short, the actual output length is reported honestly.
- ⚠️ **Super-resolution uploads your media to the cloud and queues it**, costing time and account AI credits; the `auto` default only enables it when it's actually worth it.

## How it works

1. **It writes draft files instead of driving windows.** A JianYing draft = a directory (metadata like `draft_meta_info.json` + the encrypted `draft_content` timeline + media references) + the `draft_ids` entry in the home-screen index. Get all three right and JianYing opens it exactly like a hand-made draft.
2. **No cracking — borrow the official hands.** 11.5 content is natively encrypted, so this repo calls DraftIO from the installed `videoeditor.dll` to encode/decode on write and read-back. No Frida, no disabling encryption, no install-file edits. `schema new_version=187.0.0` comes from that build's native save, and `content.id` matches the active timeline ID (not the directory meta or the home-screen `draft_id`).
3. **The `jyai` bridge translates "intent" into "timeline".** `spec` (clips / starts / durations / aspect / text / audio) → tracks & clip structure, cover, media registration → native encrypted save → home-screen registration → read-back self-check. `patch` always re-reads the newest draft from disk first and never rebuilds from a stale list.
4. **Rules stay with the parser, semantics stay with the AI.** `--prompt` only parses quantifiable rules; "which shot is the good one, how the story is ordered, where the beat lands, what was said" is analyzed by the agent with its real vision/transcription tools into a clip list, then handed to `build`.
5. **Official assets use runtime signing.** Reverse-engineered from JianYing's bundled `res_pool.dll` and frontend asset APIs: CDN links are short-lived signed URLs, so a static cache is doomed to expire. Lists, searches and downloads therefore fetch fresh signatures at runtime. Hit order: local `Cache/artistEffect/` (already downloaded by JianYing, zero network) → official live link → let the JianYing process download it itself.
6. **Quality tools are conservative by default.** It compares the short-edge tier of your media against the export tier and skips when the gap is under the threshold — matching JianYing's own "your footage is already clear enough" logic, avoiding zero-gain uploads that burn credits.
7. **The home screen is the boundary between human and AI.** Never write a draft's files while JianYing has it open for editing; between rounds, go back to the home screen, save, and re-read — so your manual edits are never overwritten.

## Quick start

Windows PowerShell:

```powershell
git clone https://github.com/jjjane233/jianying-editor-skill-11.5.git
cd jianying-editor-skill-11.5
python -m pip install -r requirements.txt
python -X utf8 tools/preflight.py
```

If your Python command is `py -3`, replace `python` above. The preflight only checks your environment (Python bitness, JianYing DLL, dependencies, draft root) — it modifies neither JianYing nor your drafts.

Then give your AI this prompt (swap in your own paths):

```text
Read and follow C:\work\jian-ying-skill\skills\jianying-editor-11-5\SKILL.md.
Cut the videos in D:\media into portrait mode, 3 seconds per clip, 12 seconds total,
add the title "Product Demo", create a new draft named "AI_Product_Demo".
Do not overwrite existing drafts; when done, verify media paths, the timeline and the
draft registration, then tell me where it was saved.
```

You can also double-click `一句话剪视频.bat`, or run directly:

```powershell
python -X utf8 jianying.py cut "D:\media" --prompt 'portrait 3s per clip 12s total add title "Product Demo"' --name "AI_Product_Demo"
```

(The `--prompt` rule parser itself currently matches Chinese keywords such as `竖屏 每段3秒 总共12秒 加标题`; use them as shown above for reliable parsing.)

## Repository layout

```
skills/jianying-editor-11-5/   # The skill itself: SKILL.md (AI manual) + scripts/jyai backend + upstream APIs/assets/rules
docs/                          # Chinese usage docs, verification log, changelog, hand-off notes
examples/                      # spec / edits examples
tests/                         # unit tests and native smoke tests
tools/                         # preflight environment check, install_skill copier
jianying.py / 一句话剪视频.*     # repo entrypoints
```

## Documentation

- [Usage guide (Chinese)](docs/使用文档.md): environment setup, invoking the skill, generating drafts, manual editing, export & troubleshooting
- [Live view & human hand-off (Chinese)](docs/实时画面与人工接手.md): today's "write draft files" mode vs. a GUI mode
- [Verification log (Chinese)](docs/验证记录.md): what passed on real hardware, what isn't verified yet
- [Changelog (Chinese)](docs/更新记录.md): what each version added and where the boundaries are
- [SKILL.md](skills/jianying-editor-11-5/SKILL.md): the AI execution entry point

Verification commands:

```powershell
python -X utf8 -m unittest discover -s tests -v
python -X utf8 skills/jianying-editor-11-5/tools/check_repo_hygiene.py
```

Static checks, codec round-trips, actual GUI opening and native export are **four separately accepted stages** — none substitutes for another.

## License

Adapted from [isYangs/jianying-editor-skill](https://github.com/isYangs/jianying-editor-skill) (source snapshot `a20c522`). The original author's MIT LICENSE is retained (copyright line `Copyright (c) 2026 luoluoluo22`) along with the license file of the vendored [pyJianYingDraft](https://github.com/GuanYixuan/pyJianYingDraft) (Apache-2.0, © GuanYixuan) — see `LICENSE` and `skills/jianying-editor-11-5/LICENSE` at the repo root; `scripts/vendor/pyJianYingDraft/LICENSE` carries the full Apache-2.0 text.

This repository contains no user media, real drafts, debug screenshots, JianYing DLLs, secrets or machine indexes.

This project is an **independent third-party tool** with no affiliation to or endorsement by ByteDance or the official JianYing / CapCut teams; "JianYing" and related trademarks belong to their respective owners and are used here for identification only.

## Disclaimer

- This project is provided **"as is", without any express or implied warranty**, and with no fitness for a particular purpose (see the MIT terms in the LICENSE).
- Anyone using this project to generate, modify, export or publish videos, drafts, media or any other output is **solely responsible for its content, copyright, reputation and legal consequences; the author of this repository is not**.
- Users must ensure they hold the legal rights to their media and comply with the JianYing / CapCut terms of service, platform rules and the laws of their jurisdiction; the author accepts no liability for disputes, losses or penalties arising from non-compliant use.
- To the maximum extent permitted by law, the author is not liable for any direct, indirect, incidental or consequential damages arising from the use of, or inability to use, this project.
- Examples, screenshots and verification records in this project's documentation state technical facts only and are not advice on commercial use, publishing or circumventing platform rules.

## Community & support

Welcome to our QQ group to discuss usage, errors and new-version adaptations (Chinese-speaking community):

- **QQ group number: `1092215676`**
- [Join the group](https://qm.qq.com/cgi-bin/qm/qr?k=fAo0-5AHp6tCcG6-BozfatbLPcVw2Y9H&jump_from=webapi&authKey=nvoxE003gnKiqGPM1EJnkyAsO4DO0oKBfJCofWA0EcbZnoIBNZvv9ELpi2Yj21TS)

Companion **AI gateway / API relay service** for agent model calls: <https://www.pursueray.cn/>
