from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path
from types import ModuleType

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def load_check_long_lines() -> ModuleType:
    module_path = REPOSITORY_ROOT / "scripts" / "check_long_lines.py"
    spec = importlib.util.spec_from_file_location("check_long_lines", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load {module_path}")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


check_long_lines = load_check_long_lines()


def git_ref_exists(ref: str) -> bool:
    result = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", ref],
        check=False,
        capture_output=True,
    )
    return result.returncode == 0


def changed_files(base_ref: str = "master") -> list[Path]:
    if not git_ref_exists(base_ref):
        return check_long_lines.tracked_files()

    result = subprocess.run(
        ["git", "diff", "--name-only", "-z", f"{base_ref}...HEAD"],
        check=True,
        capture_output=True,
        text=False,
    )
    return [Path(item.decode()) for item in result.stdout.split(b"\0") if item]


def main() -> int:
    paths = changed_files()
    findings = check_long_lines.scan_paths(paths)

    if findings:
        print("Changed-file text hygiene issues found:")
        print("\n".join(findings))
        return 1

    print(
        "Changed-files text hygiene check passed: changed active text files "
        "contain no CR bytes, hidden/control formatting characters, dangerous "
        "Unicode escape literals, unexpected control characters, or lines over "
        f"{check_long_lines.MAX_LINE_LENGTH} characters."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
