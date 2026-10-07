"""Read-only environment check; no app launch, draft write or registration."""
import importlib.util
import os
from pathlib import Path
import struct
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "skills" / "jianying-editor-11-5" / "scripts"))


def main():
    from jyai.project import root_meta_path
    from jyai.native_crypto import VERIFIED_VERSION
    failures = []

    def check(label, ok, detail):
        print(("[OK] " if ok else "[!]  ") + label + ": " + str(detail))
        if not ok:
            failures.append(label)

    check("Windows", sys.platform == "win32", sys.platform)
    check("Python", sys.version_info >= (3, 10), sys.executable)
    check("64位", struct.calcsize("P") == 8, struct.calcsize("P") * 8)
    dll = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData/Local"))) / "JianyingPro" / "Apps" / VERIFIED_VERSION / "videoeditor.dll"
    check("已验证剪映 DLL", dll.is_file(), dll)
    for name in ("PIL", "cv2", "pymediainfo", "numpy", "imageio_ffmpeg", "uiautomation"):
        check("依赖 " + name, importlib.util.find_spec(name) is not None, name)
    index = Path(root_meta_path())
    check("剪映首页索引", index.is_file(), index)
    try:
        from jyai.autocut import _default_draft_root
        draft_root = Path(_default_draft_root())
        print("[INFO] 默认草稿根目录：" + str(draft_root))
        if not draft_root.is_dir():
            print("[INFO] 该目录尚不存在；生成时会创建，也可以用 -o 指定其他目录。")
    except Exception as exc:
        print("[INFO] 草稿根目录未探测到，请为 cut/build 指定 -o：" + str(exc))
    print("只读预检完成；未启动剪映、未修改草稿，未验证 GUI 打开。")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
