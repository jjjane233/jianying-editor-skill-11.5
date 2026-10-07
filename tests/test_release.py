import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / 'skills' / 'jianying-editor-11-5' / 'scripts'
sys.path.insert(0, str(SCRIPTS))
from jyai.autocut import parse_prompt, build_spec, _default_draft_root


class ReleaseTests(unittest.TestCase):
    def test_prompt_rules(self):
        plan = parse_prompt('竖屏 每段3秒 总共12秒 加标题 "产品展示" 静音 30fps')
        self.assertEqual((plan['width'], plan['height']), (1080, 1920))
        self.assertEqual(plan['per_clip'], 3)
        self.assertEqual(plan['total_duration'], 12)
        self.assertEqual(plan['texts'], ['产品展示'])
        self.assertTrue(plan['mute'])

    def test_spec_times_and_mute(self):
        spec = build_spec(['01.mp4', '02.mp4', '03.mp4', '04.mp4'],
                          '横屏 每段3秒 总共12秒 静音', name='AI_test',
                          probe=lambda path, default: (10_000_000, 1920, 1080))
        self.assertEqual([v['start'] for v in spec['video']], [0, 3, 6, 9])
        self.assertEqual(sum(v['duration'] for v in spec['video']), 12)
        self.assertEqual(spec['audio'], [])
        self.assertTrue(spec['mute'])

    def test_explicit_default_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {'JY_DRAFTS_ROOT': tmp}):
                self.assertEqual(_default_draft_root(), str(Path(tmp).resolve()))

    def test_index_default_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {'JY_DRAFTS_ROOT': ''}):
                with mock.patch('jyai.project.load_root_meta', return_value={
                    'all_draft_store': [{'draft_root_path': tmp}]
                }):
                    self.assertEqual(_default_draft_root(), str(Path(tmp).resolve()))

    def test_root_entrypoint(self):
        run = subprocess.run([sys.executable, '-X', 'utf8', str(ROOT / 'jianying.py'), '--help'],
                             capture_output=True, encoding='utf-8', timeout=30)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn('cut', run.stdout)
        self.assertIn('repair', run.stdout)

    def test_examples_are_json(self):
        for name in ('spec.example.json', 'edits.example.json'):
            with self.subTest(name=name):
                value = json.loads((ROOT / 'examples' / name).read_text(encoding='utf-8'))
                self.assertIsInstance(value, dict)

    def test_install_to_isolated_root_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            cmd = [sys.executable, '-X', 'utf8', str(ROOT / 'tools/install_skill.py'), '--destination', tmp]
            run = subprocess.run(cmd, capture_output=True, encoding='utf-8', timeout=30)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertTrue((Path(tmp) / 'jianying-editor-11-5/SKILL.md').is_file())
            self.assertTrue((Path(tmp) / 'jianying-editor-11-5/scripts/jyai/native_crypto.py').is_file())
            again = subprocess.run(cmd, capture_output=True, encoding='utf-8', timeout=30)
            self.assertNotEqual(again.returncode, 0)


if __name__ == '__main__':
    unittest.main()
