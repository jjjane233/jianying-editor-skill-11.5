"""Copy this repository's skill to a selected local skill root without overwrites."""
import argparse
import os
from pathlib import Path
import shutil


def main():
    parser = argparse.ArgumentParser(description="复制剪映 skill；不覆盖已有同名目录")
    parser.add_argument("--destination", type=Path, help="客户端扫描的技能根目录")
    args = parser.parse_args()
    repo = Path(__file__).resolve().parent.parent
    source = repo / "skills" / "jianying-editor-11-5"
    codex_home = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))).expanduser()
    skills_root = args.destination or codex_home / "skills"
    target = skills_root.expanduser().resolve() / source.name
    if target.exists():
        raise FileExistsError("目标已经存在，未覆盖：" + str(target))
    shutil.copytree(source, target, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.log", ".git"))
    print("已复制完整 skill：" + str(target))
    print("请重新打开客户端并确认技能列表；尚未被发现时可直接让 AI 读取其中 SKILL.md。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
