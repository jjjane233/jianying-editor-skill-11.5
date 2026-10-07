"""Compatibility bridge for the locally verified Jianying Pro 11.5.0.14471."""
from pathlib import Path
import os
import time
import uuid
from jyai import project
from jyai.engine import _platform_info
from jyai.repair import atomic_json, read_json, repair


def finalize(draft_dir, register=None):
    if register is None:
        register = os.environ.get('JY_NATIVE_REGISTER', '1') != '0'
    folder = Path(draft_dir).resolve()
    content = read_json(folder / 'draft_content.json')
    meta = read_json(folder / 'draft_meta_info.json')
    if not meta.get('draft_id'):
        meta['draft_id'] = str(uuid.uuid4()).upper()
    # The vendored 5.9 meta template can carry an empty/fixed ID. Avoid collisions.
    store = project.load_root_meta().get('all_draft_store', [])
    same = next((e for e in store if project.normalize_path(e.get('draft_fold_path','')) == project.normalize_path(str(folder))), None)
    if same:
        meta['draft_id'] = same['draft_id']
    elif any(e.get('draft_id','').upper() == meta['draft_id'].upper() for e in store):
        meta['draft_id'] = str(uuid.uuid4()).upper()
    content['platform'] = _platform_info()
    content['last_modified_platform'] = _platform_info()
    content['version'] = 360000
    content['new_version'] = '187.0.0'
    content['draft_type'] = 'video'
    content['source'] = 'default'
    content.setdefault('create_time', time.time_ns() // 1000)
    content['update_time'] = time.time_ns() // 1000
    atomic_json(folder / 'draft_content.json', content)
    atomic_json(folder / 'draft_meta_info.json', meta)
    if not (folder / 'Timelines/project.json').exists():
        project.write_timelines(str(folder), content)
    return repair(str(folder), register=register, backup=False, native=True)


def export_basic(draft_dir, output_path):
    from jyai.render import render_basic
    try:
        result = render_basic(draft_dir, output_path)
    except ValueError as exc:
        return {'status':'native_export_required', 'draft_path':str(Path(draft_dir).resolve()),
                'reason':str(exc), 'hint':'在剪映中打开该草稿，点击右上角导出以保留全部效果'}
    return {'status':'success', **result}
