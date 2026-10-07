"""Use Jianying 11.5's own DraftIO codec in an isolated helper process.

No process injection or application binary changes. The C++ ABI below was
verified against the installed 11.5.0.14471 DLL with an encrypt/decrypt round trip.
"""
from __future__ import annotations
import argparse
import ctypes as c
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

FACTORY_EXPORT = ('?createCipherV1@DraftIOFactoryBase@draft_store@@UEAA?AV?$shared_ptr@'
                  'UIDraftIOParam@draft_store@@@std@@XZ')
VERIFIED_VERSION = '11.5.0.14471'


class String(c.Structure):
    _fields_ = [('buf', c.c_byte * 16), ('size', c.c_uint64), ('cap', c.c_uint64)]


def msvc_string(data):
    buffer = c.create_string_buffer(data)
    value = String()
    c.c_void_p.from_buffer(value).value = c.addressof(buffer)
    value.size = len(data)
    value.cap = max(16, len(data))
    return value, buffer


class NativeIO:
    def __init__(self, apps=None):
        if c.sizeof(c.c_void_p) != 8:
            raise RuntimeError('NativeIO requires 64-bit Python')
        root = Path(apps or (Path(os.environ['LOCALAPPDATA']) / 'JianyingPro/Apps')) / VERIFIED_VERSION
        self.directory = os.add_dll_directory(str(root))
        self.dll = c.WinDLL(str(root / 'videoeditor.dll'))
        factory = getattr(self.dll, FACTORY_EXPORT)
        factory.argtypes = [c.c_void_p, c.c_void_p]
        factory.restype = c.c_void_p
        self.param = c.create_string_buffer(16)
        self.dummy = c.create_string_buffer(128)
        factory(self.dummy, self.param)
        self.obj = c.c_void_p.from_buffer(self.param).value
        if not self.obj:
            raise RuntimeError('Native DraftIO factory returned null')
        self.retained = []

    def _method(self, obj, offset, nargs):
        vtable = c.c_void_p.from_address(obj).value
        address = c.c_void_p.from_address(vtable + offset).value
        return c.WINFUNCTYPE(c.c_void_p, *([c.c_void_p] * nargs))(address)

    def _open(self, path):
        filename, backing = msvc_string(Path(path).resolve().as_posix().encode('utf-8'))
        shared = c.create_string_buffer(16)
        self._method(self.obj, 0x30, 3)(self.obj, shared, c.byref(filename))
        io = c.c_void_p.from_buffer(shared).value
        if not io:
            raise RuntimeError('Native DraftIO returned null for ' + str(path))
        # The helper exits after a bounded batch; shared ownership stays alive.
        self.retained.append(shared)
        return io

    def read(self, path):
        io = self._open(path)
        result = c.create_string_buffer(160)
        self._method(io, 0x20, 2)(io, result)
        value = String.from_buffer(result, 8)
        if value.size > 256 * 1024 * 1024 or value.cap < value.size:
            raise RuntimeError('Invalid native read result')
        pointer = c.c_void_p.from_buffer(value).value if value.cap >= 16 else c.addressof(value)
        data = c.string_at(pointer, value.size) if value.size else b''
        if not data:
            raise RuntimeError('Native reader returned empty content: ' + str(path))
        return data

    def write(self, path, data):
        io = self._open(path)
        value, backing = msvc_string(data)
        result = c.create_string_buffer(128)
        self._method(io, 0x38, 3)(io, result, c.byref(value))
        if c.c_int.from_buffer(result, 32).value != 0 or not Path(path).is_file():
            raise RuntimeError('Native write failed: ' + str(path))
        if self.read(path) != data:
            raise RuntimeError('Native write/read roundtrip mismatch: ' + str(path))


def worker(jobs_path):
    jobs = json.loads(Path(jobs_path).read_text(encoding='utf-8'))
    codec = NativeIO()
    results = []
    for job in jobs:
        if job['op'] == 'read':
            data = codec.read(job['path'])
            Path(job['output']).write_bytes(data)
            results.append({'path': job['path'], 'decoded_bytes': len(data)})
        elif job['op'] == 'write':
            data = Path(job['source']).read_bytes()
            codec.write(job['path'], data)
            results.append({'path': job['path'], 'roundtrip': True, 'plaintext_bytes': len(data)})
        else:
            raise ValueError(job['op'])
    print('JY_RESULT=' + json.dumps(results, ensure_ascii=False), flush=True)


def _run(jobs, work):
    job_path = Path(work) / 'jobs.json'
    job_path.write_text(json.dumps(jobs, ensure_ascii=False), encoding='utf-8')
    result = subprocess.run([sys.executable, '-X', 'utf8', '-m', 'jyai.native_crypto', '--worker', str(job_path)],
                            cwd=str(Path(__file__).resolve().parent.parent), capture_output=True, timeout=90,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    output = result.stdout.decode('utf-8', 'replace')
    if result.returncode != 0:
        raise RuntimeError('Native codec helper failed (%s): %s' % (result.returncode,
                           result.stderr.decode('utf-8', 'replace')[-2000:]))
    line = next((line for line in output.splitlines() if line.startswith('JY_RESULT=')), None)
    if line is None:
        raise RuntimeError('Missing native codec result')
    return json.loads(line.split('=', 1)[1])


def read_bytes(path):
    with tempfile.TemporaryDirectory(prefix='jyai-native-read-') as work:
        output = Path(work) / 'decoded.json'
        _run([{'op': 'read', 'path': str(path), 'output': str(output)}], work)
        return output.read_bytes()


def encrypt_files(paths):
    """Stage and round-trip verify the entire batch before replacing any target."""
    from .repair import atomic_bytes
    with tempfile.TemporaryDirectory(prefix='jyai-native-write-') as work:
        jobs, targets = [], []
        for index, path in enumerate(paths):
            path = Path(path)
            data = path.read_bytes()
            if not data.lstrip().startswith((b'{', b'[')):
                continue
            json.loads(data.decode('utf-8-sig'))
            source = Path(work) / ('plain-%d.json' % index)
            target = Path(work) / ('encrypted-%d.json' % index)
            source.write_bytes(data)
            jobs.append({'op': 'write', 'path': str(target), 'source': str(source)})
            targets.append((path, target))
        result = _run(jobs, work)
        for path, target in targets:
            atomic_bytes(path, target.read_bytes())
        return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--worker', required=True)
    args = parser.parse_args()
    worker(args.worker)
