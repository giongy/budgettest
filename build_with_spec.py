from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def main() -> int:
    project_root = Path(__file__).resolve().parent
    spec_path = project_root / "budget.spec"
    if not spec_path.exists():
        print(f"[!] Spec file not found: {spec_path}")
        return 1

    cmd = [sys.executable, "-m", "PyInstaller", str(spec_path)]
    print("Running:", " ".join(cmd))

    result = subprocess.run(cmd, cwd=project_root)
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
