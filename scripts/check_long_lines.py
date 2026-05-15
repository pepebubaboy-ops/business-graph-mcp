from __future__ import annotations

import subprocess
from pathlib import Path

MAX_LINE_LENGTH = 220
UNICODE_LINE_SEPARATORS = {
    "\u2028": "U+2028",
    "\u2029": "U+2029",
}
BIDI_CONTROL_CHARACTERS = {
    "\u202a": "U+202A",
    "\u202b": "U+202B",
    "\u202c": "U+202C",
    "\u202d": "U+202D",
    "\u202e": "U+202E",
    "\u2066": "U+2066",
    "\u2067": "U+2067",
    "\u2068": "U+2068",
    "\u2069": "U+2069",
}
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


def inspect_text_file(path: Path) -> list[str]:
    findings: list[str] = []
    data = path.read_bytes()
    if not is_text(data):
        return findings

    text = data.decode("utf-8")

    if b"\r" in data:
        findings.append(f"{path}: contains raw carriage return bytes")

    for character, label in UNICODE_LINE_SEPARATORS.items():
        if character in text:
            findings.append(f"{path}: contains Unicode line separator {label}")

    for character, label in BIDI_CONTROL_CHARACTERS.items():
        if character in text:
            findings.append(f"{path}: contains Unicode bidi control {label}")

    for line_number, line in enumerate(text.splitlines(), start=1):
        line_length = len(line)
        if line_length > MAX_LINE_LENGTH:
            findings.append(
                f"{path}:{line_number}: line is {line_length} characters (max {MAX_LINE_LENGTH})"
            )

    return findings


def scan_paths(paths: list[Path]) -> list[str]:
    findings: list[str] = []

    for path in paths:
        if should_skip(path) or not path.is_file():
            continue
        findings.extend(inspect_text_file(path))

    return findings


def main() -> int:
    findings = scan_paths(tracked_files())

    if findings:
        print("Text hygiene issues found:")
        print("\n".join(findings))
        return 1

    print(
        "Text hygiene check passed: no active tracked text files contain "
        "CR bytes, Unicode line separators, bidi controls, or lines over "
        f"{MAX_LINE_LENGTH} characters."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
