"""Synchronize generated draft structure and save using the verified native codec."""
from __future__ import annotations
import json
import os
import shutil
import tempfile
import time
from pathlib import Path
from . import project


def atomic_bytes(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix='.jyai-', dir=str(path.parent))
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(data)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def atomic_json(path, value):
    atomic_bytes(path, json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode('utf-8'))


def read_json(path):
    from .draft import load_json
    return load_json(str(path))


def make_cover(folder, content):
    from PIL import Image, ImageOps, ImageDraw, ImageFont
    frame = None
    paths = {m['id']: m for m in content.get('materials', {}).get('videos', [])}
    segments = sorted([s for t in content.get('tracks', []) if t.get('type') == 'video'
                       for s in t.get('segments', [])],
                      key=lambda s: s.get('target_timerange', {}).get('start', 0))
    if segments:
        seg = segments[0]
        mat = paths.get(seg.get('material_id'), {})
        path = mat.get('path', '')
        if path and os.path.isfile(path):
            if mat.get('type') == 'photo':
                frame = Image.open(path).convert('RGB')
            else:
                import cv2
                cap = cv2.VideoCapture(path)
                try:
                    cap.set(cv2.CAP_PROP_POS_MSEC, (seg.get('source_timerange') or {}).get('start', 0) / 1000)
                    ok, img = cap.read()
                    if ok:
                        frame = Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
                finally:
                    cap.release()
    source = 'video-frame'
    if frame is None:
        source = 'fallback-title'
        frame = Image.new('RGB', (640, 360), (32, 45, 64))
        draw = ImageDraw.Draw(frame)
        font_path = Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Fonts/msyh.ttc'
        font = ImageFont.truetype(str(font_path), 26) if font_path.exists() else ImageFont.load_default()
        draw.text((20, 160), folder.name[:22], fill='white', font=font)
    for name, size in [('draft_cover.jpg', (720, 720)), ('draft_local_cover.jpg', (240, 240))]:
        thumb = ImageOps.contain(frame, size)
        import io
        buf = io.BytesIO()
        thumb.save(buf, 'JPEG', quality=90)
        atomic_bytes(folder / name, buf.getvalue())
    return source


def validate(draft_dir, user_data=None):
    folder = Path(draft_dir).resolve()
    content = read_json(folder / 'draft_content.json')
    meta = read_json(folder / 'draft_meta_info.json')
    proj = read_json(folder / 'Timelines/project.json')
    layout = read_json(folder / 'timeline_layout.json')
    tid = layout.get('activeTimeline')
    issues = []
    if not tid or tid != proj.get('main_timeline_id') or not (folder / 'Timelines' / tid).is_dir():
        issues.append('活动时间线索引不一致')
    elif read_json(folder / 'Timelines' / tid / 'draft_content.json') != content:
        issues.append('根内容与活动时间线不一致')
    if content.get('id') != tid:
        issues.append('内容 ID 与活动时间线 ID 不一致')
    mats = content.get('materials') or {}
    ids = {m.get('id') for group in mats.values() if isinstance(group, list)
           for m in group if isinstance(m, dict)}
    for track in content.get('tracks', []):
        for seg in track.get('segments', []):
            for key in [seg.get('material_id')] + (seg.get('extra_material_refs') or []):
                if key and key not in ids:
                    issues.append('片段引用不存在的素材: ' + str(key))
    missing = [m['path'] for k in ('videos', 'audios', 'images') for m in mats.get(k, [])
               if m.get('path') and not os.path.isfile(m['path'])]
    if missing:
        issues.append('素材丢失: ' + ', '.join(missing))
    entry = next((e for e in project.load_root_meta(user_data).get('all_draft_store', [])
                  if project.normalize_path(e.get('draft_fold_path', '')) == project.normalize_path(str(folder))), None)
    if entry is None:
        issues.append('未登记草稿')
    else:
        if entry.get('draft_id', '').upper() != meta.get('draft_id', '').upper():
            issues.append('目录 meta 与首页登记 ID 不一致')
        if entry.get('tm_duration') != content.get('duration'):
            issues.append('首页时长未同步')
        if entry.get('draft_timeline_materials_size') != project.materials_size(str(folder)):
            issues.append('首页素材大小未同步')
    if not (folder / 'draft_cover.jpg').is_file():
        issues.append('缺少封面')
    return {'draft_dir': str(folder), 'static_valid': not issues, 'issues': issues,
            'duration_us': content.get('duration'), 'material_bytes': project.materials_size(str(folder)),
            'app_open_verified': False}


def repair(draft_dir, user_data=None, register=True, backup=True, native=True):
    folder = Path(draft_dir).resolve()
    content = read_json(folder / 'draft_content.json')
    meta = read_json(folder / 'draft_meta_info.json')
    if meta.get('draft_is_infinite_canvas_draft'):
        raise ValueError('无限画布不使用普通视频草稿修复流程')
    backup_path = None
    if backup:
        backup_path = folder / '.backup' / ('jyai-repair-' + time.strftime('%Y%m%d-%H%M%S'))
        backup_path.mkdir(parents=True, exist_ok=False)
        for child in list(folder.iterdir()):
            if child.name == '.backup':
                continue
            dest = backup_path / child.name
            if child.is_dir():
                shutil.copytree(child, dest)
            else:
                shutil.copy2(child, dest)
    now = time.time_ns() // 1000
    # 原生 11.5 实测: content.id 等于活动 timeline ID, 并非 meta.draft_id。
    content['name'] = folder.name
    content['duration'] = max([s['target_timerange']['start'] + s['target_timerange']['duration']
                              for t in content.get('tracks', []) for s in t.get('segments', [])
                              if s.get('target_timerange')] or [0])
    content['update_time'] = now
    data = json.dumps(content, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    for rel in ('draft_content.json', 'draft_content.json.bak', 'template-2.tmp'):
        atomic_bytes(folder / rel, data)
    proj_path = folder / 'Timelines/project.json'
    if not proj_path.exists():
        project.write_timelines(str(folder), content)
    proj = read_json(proj_path)
    tid = proj['main_timeline_id']
    content['id'] = tid
    content['new_version'] = '187.0.0'
    data = json.dumps(content, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    for rel in ('draft_content.json', 'draft_content.json.bak', 'template-2.tmp'):
        atomic_bytes(folder / rel, data)
    for rel in ('draft_content.json', 'draft_content.json.bak', 'template-2.tmp', 'template.json'):
        atomic_bytes(folder / 'Timelines' / tid / rel, data)
    atomic_json(proj_path.with_suffix('.json.bak'), proj)
    atomic_json(folder / 'timeline_layout.json', {'activeTimeline': tid,
        'dockItems': [{'dockIndex': 0, 'ratio': 1, 'timelineIds': [tid], 'timelineNames': ['时间线01']}],
        'layoutOrientation': 1})
    cover_source = make_cover(folder, content)
    shutil.copy2(folder / 'draft_cover.jpg', folder / 'Timelines' / tid / 'draft_cover.jpg')
    defaults = {
        'performance_opt_info.json': {'manual_cancle_precombine_segs': None, 'need_auto_precombine_segs': None},
        'draft_agency_config.json': {'is_auto_agency_enabled': False, 'is_auto_agency_popup': False,
            'is_single_agency_mode': False, 'marterials': None, 'use_converter': False, 'video_resolution': 720},
        'draft_biz_config.json': {'timeline_settings': {tid: {'linkage_enabled': False}}},
        'key_value.json': {},
    }
    for name, value in defaults.items():
        if not (folder / name).exists():
            atomic_json(folder / name, value)
    fwd = folder.as_posix()
    meta.update({'draft_name': folder.name, 'draft_fold_path': fwd,
        'draft_root_path': folder.parent.as_posix(), 'draft_json_file': fwd + '/draft_content.json',
        'draft_cover': 'draft_cover.jpg', 'draft_is_infinite_canvas_draft': False,
        'draft_timeline_materials_size_': project.materials_size(str(folder)),
        'tm_draft_modified': now, 'tm_duration': content['duration']})
    meta.setdefault('tm_draft_create', content.get('create_time') or now)
    atomic_json(folder / 'draft_meta_info.json', meta)
    native_result = []
    if native:
        from .native_crypto import encrypt_files
        paths = [folder / rel for rel in ('draft_content.json', 'draft_content.json.bak',
                 'template-2.tmp', 'draft_meta_info.json')]
        paths += [folder / 'Timelines' / tid / rel for rel in
                  ('draft_content.json', 'draft_content.json.bak', 'template-2.tmp')]
        native_result = encrypt_files(paths)
    if register:
        project.register(str(folder), folder.name, content['duration'], user_data)
    result = validate(str(folder), user_data) if register else {'draft_dir': str(folder)}
    result.update({'backup': str(backup_path) if backup_path else None, 'cover_source': cover_source,
                   'native_cipher': 256 if native else 0, 'native_roundtrip_files': len(native_result)})
    return result
