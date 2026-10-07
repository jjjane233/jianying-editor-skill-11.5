"""Isolated native-codec smoke test; never registers or opens a user draft."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / 'skills' / 'jianying-editor-11-5' / 'scripts'
sys.path.insert(0, str(SCRIPTS))


def main():
    if sys.platform != 'win32':
        raise RuntimeError('此测试需要 Windows 和剪映 11.5.0.14471')
    from jyai.engine import build_from_scratch, patch_existing
    from jyai.repair import read_json, repair, atomic_json
    from jyai.draft import Draft
    from jyai.project import root_meta_path
    from jyai.render import ffmpeg_exe, render_basic

    index = Path(root_meta_path())
    before = index.read_bytes() if index.exists() else None
    try:
        with tempfile.TemporaryDirectory(prefix='jianying-release-smoke-') as tmp:
            work = Path(tmp)
            media = work / 'input.mp4'
            run = subprocess.run([ffmpeg_exe(), '-hide_banner', '-loglevel', 'error', '-y',
                '-f', 'lavfi', '-i', 'color=c=blue:s=320x240:r=30',
                '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000',
                '-t', '2', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac', str(media)],
                capture_output=True, timeout=60)
            if run.returncode:
                raise RuntimeError(run.stderr.decode('utf-8', 'replace'))
            spec = {'name': 'AI_release_smoke', 'width': 320, 'height': 240, 'fps': 30,
                'video': [{'path': str(media), 'start': 0, 'duration': 2, 'source_start': 0}],
                'audio': [], 'text': [{'text': '发布测试', 'start': 0, 'duration': 1}]}
            result = build_from_scratch(spec, str(work / 'drafts'), register=False)
            folder = Path(result['draft_dir'])
            assert not (folder / 'draft_content.json').read_bytes().lstrip().startswith(b'{')
            content = read_json(folder / 'draft_content.json')
            project = read_json(folder / 'Timelines/project.json')
            tid = project['main_timeline_id']
            assert content['id'] == tid
            assert content['new_version'] == '187.0.0'
            assert content['duration'] == 2_000_000
            assert read_json(folder / 'Timelines' / tid / 'draft_content.json') == content
            assert result['draft_id'] is None
            assert (folder / 'draft_cover.jpg').stat().st_size > 0
            try:
                build_from_scratch(spec, str(work / 'drafts'), register=False)
            except FileExistsError:
                pass
            else:
                raise AssertionError('重复名称没有被阻止')
            export = render_basic(folder, work / 'preview.mp4')
            assert export['bytes'] > 0 and export['duration_seconds'] == 2
            print('PASS: native engine roundtrip, timeline, cover, duplicate-name check, basic MP4')

            # Simulate saved manual changes and a stale plaintext template.
            latest = Draft(str(folder)).read_content()
            latest['materials']['texts'][0]['content'] = '{"text":"人工已改"}'
            latest['manual_test_field'] = {'keep': True}
            Draft(str(folder)).write_content(latest)
            repair(folder, register=False, backup=False)
            atomic_json(folder / 'Timelines' / tid / 'template.json', content)
            assert Draft(str(folder)).read_content()['manual_test_field'] == {'keep': True}
            # Route index writes to a temporary index, never the real homepage.
            with mock.patch('jyai.project.root_meta_path', return_value=str(work / 'isolated-index.json')):
                patch_existing(str(folder), {'add_text': [
                    {'text': '续剪标题', 'start': 0, 'duration': 1}
                ]})
            patched = read_json(folder / 'draft_content.json')
            assert patched['manual_test_field'] == {'keep': True}
            assert patched['materials']['texts'][0]['content'] == '{"text":"人工已改"}'
            assert len(patched['materials']['texts']) == 2
            assert read_json(folder / 'Timelines' / tid / 'draft_content.json') == patched
            print('PASS: incremental editing rereads latest content and preserves manual changes')

            os.environ['JY_NATIVE_REGISTER'] = '0'
            from jy_wrapper import JyProject
            upstream = JyProject('AI_upstream_smoke', 320, 240,
                                 drafts_root=str(work / 'upstream'), overwrite=False)
            upstream.add_clip(str(media), '0s', '2s')
            upstream.save()
            adapted = Path(upstream.root) / upstream.name
            native = read_json(adapted / 'draft_content.json')
            tid = read_json(adapted / 'Timelines/project.json')['main_timeline_id']
            assert native['id'] == tid and native['duration'] == 2_000_000
            assert not (adapted / 'draft_content.json').read_bytes().lstrip().startswith(b'{')
            print('PASS: upstream JyProject.add_clip/save native adaptation')
    finally:
        after = index.read_bytes() if index.exists() else None
        assert before == after, '真实首页索引发生变化（并发剪映保存也可能导致变化），请检查'
    print('PASS: real index unchanged; all generated artifacts were isolated and cleaned up')
    print('未执行 GUI 打开、实时操作或剪映原生导出。')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
