from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def build_frontend() -> bool:
    """Run npm ci && npm run build; return True on success, False on failure."""
    frontend_dir = Path(__file__).resolve().parents[2] / "c"
    package_json = frontend_dir / "package.json"
    if not package_json.exists():
        print("[yamibo-build-frontend] c/package.json not found, skipping")
        return True

    static_dir = Path(__file__).resolve().parent / "web" / "static"
    if static_dir.exists() and any(static_dir.iterdir()):
        print("[yamibo-build-frontend] static/ already has content, skipping")
        return True

    try:
        if not (frontend_dir / "node_modules").exists():
            print("[yamibo-build-frontend] running npm ci...")
            subprocess.run(["npm", "ci"], cwd=frontend_dir, check=True)
        print("[yamibo-build-frontend] running npm run build...")
        subprocess.run(["npm", "run", "build"], cwd=frontend_dir, check=True)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        print(f"[yamibo-build-frontend] build failed (non-fatal): {exc}", file=sys.stderr)
        return False


def main() -> None:
    build_frontend()


if __name__ == "__main__":
    main()
