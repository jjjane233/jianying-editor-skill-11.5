"""Render the basic cut/text/audio subset to an MP4 for viewing independently of the editor."""
from __future__ import annotations
import os
import subprocess
import tempfile
from pathlib import Path
from .repair import read_json


def ffmpeg_exe():
    import shutil
    exe = shutil.which('ffmpeg')
    if exe:
        return exe
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def render_basic(draft_dir, output):
    import json
    content = read_json(Path(draft_dir) / 'draft_content.json')
    tracks = content.get('tracks', [])
    video_tracks = [t for t in tracks if t.get('type') == 'video' and t.get('segments')]
    if len(video_tracks) != 1:
        raise ValueError('基础 MP4 渲染只支持单视频轨; 多轨/特效工程请使用剪映原生导出')
    mats = content.get('materials', {})
    if any(mats.get(k) for k in ('effects', 'video_effects', 'transitions', 'stickers', 'filters')):
        raise ValueError('工程有特效/转场/贴纸, 需要剪映原生导出以保留效果')
    mid = {m['id']: m for group in mats.values() if isinstance(group, list)
           for m in group if isinstance(m, dict) and 'id' in m}
    segments = sorted(video_tracks[0]['segments'], key=lambda s:s['target_timerange']['start'])
    width = content['canvas_config']['width']
    height = content['canvas_config']['height']
    fps = content.get('fps', 30)
    duration = content['duration'] / 1e6
    output = Path(output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg = ffmpeg_exe()
    def run(args):
        result = subprocess.run([ffmpeg, '-hide_banner', '-loglevel', 'error', '-y'] + args,
                                capture_output=True, timeout=300,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if result.returncode:
            raise RuntimeError(result.stderr.decode('utf-8', 'replace')[-3000:])
    with tempfile.TemporaryDirectory(prefix='jyai-render-') as work:
        clips = []
        cursor = 0
        for index, seg in enumerate(segments):
            start = seg['target_timerange']['start'] / 1e6
            take = seg['target_timerange']['duration'] / 1e6
            if abs(start - cursor) > .002 or float(seg.get('speed', 1)) != 1:
                raise ValueError('基础渲染要求片段连续且速度为 1; 使用剪映导出复杂时间线')
            mat = mid[seg['material_id']]
            path = mat['path']
            if not os.path.isfile(path):
                raise FileNotFoundError(path)
            clip = Path(work) / ('clip%04d.mp4' % index)
            source = (seg.get('source_timerange') or {}).get('start', 0) / 1e6
            args = ['-loop', '1'] if mat.get('type') == 'photo' else ['-ss', str(source)]
            args += ['-i', path]
            volume = float(seg.get('volume', 1))
            has_audio = mat.get('has_audio', True) and mat.get('type') != 'photo'
            if not has_audio:
                args += ['-f', 'lavfi', '-i', 'anullsrc=r=48000:cl=stereo']
            args += ['-t', str(take), '-vf',
                f'scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={fps}',
                '-map', '0:v:0', '-map', '0:a:0' if has_audio else '1:a:0',
                '-af', f'volume={volume},apad', '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '22',
                '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-ar', '48000', '-ac', '2', '-shortest', str(clip)]
            run(args)
            clips.append(clip)
            cursor = start + take
        concat = Path(work) / 'clips.txt'
        concat.write_text(''.join("file '%s'\n" % p.as_posix() for p in clips), encoding='utf-8')
        stitched = Path(work) / 'stitched.mp4'
        run(['-f', 'concat', '-safe', '0', '-i', str(concat), '-c', 'copy', str(stitched)])
        filters = []
        font = (Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Fonts/msyh.ttc').as_posix().replace(':', '\\:')
        for ti, track in enumerate(tracks):
            if track.get('type') != 'text':
                continue
            for si, seg in enumerate(track.get('segments', [])):
                mat = mid[seg['material_id']]
                text = json.loads(mat['content']).get('text', '')
                textfile = Path(work) / ('text%d-%d.txt' % (ti,si))
                textfile.write_text(text, encoding='utf-8')
                escaped = textfile.as_posix().replace(':', '\\:')
                start = seg['target_timerange']['start'] / 1e6
                end = start + seg['target_timerange']['duration'] / 1e6
                transform = (seg.get('clip') or {}).get('transform') or {}
                x = float(transform.get('x', 0))
                y = float(transform.get('y', 0))
                filters.append(f"drawtext=fontfile='{font}':textfile='{escaped}':fontsize={max(20,int(height*.04))}:fontcolor=white:borderw=2:bordercolor=black:x=(w-text_w)/2+{x}*w/2:y=(h-text_h)/2-{y}*h/2:enable='between(t,{start},{end})'")
        args = ['-i', str(stitched)]
        audio_segments = [s for t in tracks if t.get('type') == 'audio' for s in t.get('segments', [])]
        for seg in audio_segments:
            args += ['-ss', str((seg.get('source_timerange') or {}).get('start',0)/1e6), '-i', mid[seg['material_id']]['path']]
        if filters:
            args += ['-vf', ','.join(filters)]
        if audio_segments:
            audio_filter = []
            for i, seg in enumerate(audio_segments, 1):
                take = seg['target_timerange']['duration']/1e6
                delay = seg['target_timerange']['start']//1000
                audio_filter.append(f'[{i}:a]atrim=duration={take},asetpts=PTS-STARTPTS,volume={seg.get("volume",1)},adelay={delay}|{delay}[music{i}]')
            audio_filter.append('[0:a]' + ''.join(f'[music{i}]' for i in range(1,len(audio_segments)+1)) + f'amix=inputs={len(audio_segments)+1}:normalize=0[aout]')
            args += ['-filter_complex',';'.join(audio_filter),'-map','0:v:0','-map','[aout]']
        else:
            args += ['-map','0:v:0','-map','0:a:0']
        args += ['-t',str(duration),'-c:v','libx264','-preset','veryfast','-crf','22','-pix_fmt','yuv420p','-c:a','aac','-movflags','+faststart',str(output)]
        run(args)
    if not output.exists() or output.stat().st_size == 0:
        raise RuntimeError('MP4 输出为空')
    return {'output':str(output), 'bytes':output.stat().st_size, 'duration_seconds':duration, 'renderer':'ffmpeg-basic'}
