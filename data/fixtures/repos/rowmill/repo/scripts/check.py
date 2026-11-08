"""CI と同じ検査（テストを走らせる）。"""

import subprocess
import sys
from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    return subprocess.call([sys.executable, "-m", "pytest", "-q", *sys.argv[1:]], cwd=root)


if __name__ == "__main__":
    raise SystemExit(main())
