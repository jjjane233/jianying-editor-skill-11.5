"""Interactive launcher; inputs remain data, never executable Python source."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "skills" / "jianying-editor-11-5" / "scripts"))
from jyai.autocut import autocut, list_media


def main():
    folder = sys.argv[1] if len(sys.argv) > 1 else input('素材文件夹（回车用 Downloads）：').strip().strip('"')
    folder = folder or str(Path.home() / "Downloads")
    media = list_media(folder)
    if not media:
        raise ValueError("素材文件夹中没有可用视频、音频或图片：" + folder)
    print("\n可用素材：")
    for path in media:
        print("  " + Path(path).name)
    print('\n示例：竖屏 每段3秒 总共12秒 加标题 "产品展示"')
    prompt = input("一句话需求：")
    name = input("草稿名称（回车自动命名）：").strip() or None
    out_dir = input("草稿根目录（回车自动探测）：").strip().strip('"') or None
    result = autocut(folder, prompt, name=name, out_dir=out_dir)
    print("\n已生成草稿：" + result["draft_dir"])
    print("原生写入/回读完成；实际打开与预览请在剪映确认。")
    print("若首页没有刷新，正常退出剪映后重新启动。")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print("\n生成失败：" + str(exc), file=sys.stderr)
        raise SystemExit(1)
