"""Repository entrypoint for the self-contained skill backend."""
from pathlib import Path
import sys

SCRIPTS = Path(__file__).resolve().parent / "skills" / "jianying-editor-11-5" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from jyai.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
