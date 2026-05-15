from __future__ import annotations

import subprocess
from pathlib import Path

MAX_LINE_LENGTH = 220
EXCLUDED_PARTS = {
    ".git",
    ".venv",
}
EXCLUDED_PREFIXES = ("legacy/relation-memory-cowork/",)
EXCLUDED_SUFFIXES = (
    ".xlsx",
    ".mcpb",
    ".zip",
    ".tar.gz",
)


def tracked_files() -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        check=True,
        capture_output=True,
        text=False,
    )
    return [Path(item.decode()) for item in result.stdout.split(b"\0") if item]


def should_skip(path: Path) -> bool:
    path_text = path.as_posix()
    return (
        any(part in EXCLUDED_PARTS for part in path.parts)
        or any(path_text.startswith(prefix) for prefix in EXCLUDED_PREFIXES)
        or path_text.endswith(EXCLUDED_SUFFIXES)
    )


def is_text(data: bytes) -> bool:
    if b"\0" in data:
        return False
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


def main() -> int:
    findings: list[str] = []

    for path in tracked_files():
        if should_skip(path) or not path.is_file():
            continue

        data = path.read_bytes()
        if not is_text(data):
            continue

        text = data.decode("utf-8")
        for line_number, line in enumerate(text.splitlines(), start=1):
            line_length = len(line)
            if line_length > MAX_LINE_LENGTH:
                findings.append(f"{path}:{line_number}: {line_length}")

    if findings:
        print("Suspicious long lines found:")
        print("\n".join(findings))
        return 1

    print(
        "Long-line sanity check passed: "
        f"no active tracked text lines over {MAX_LINE_LENGTH} characters."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
