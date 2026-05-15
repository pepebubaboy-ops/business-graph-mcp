from __future__ import annotations

import subprocess
import unicodedata
from pathlib import Path

MAX_LINE_LENGTH = 220
ALLOWED_CONTROL_CODEPOINTS = {
    0x0009,
    0x000A,
}
FORBIDDEN_CODEPOINTS = {
    0xFEFF: "BOM U+FEFF",
    0x2028: "LINE SEPARATOR U+2028",
    0x2029: "PARAGRAPH SEPARATOR U+2029",
    0x202A: "BIDI U+202A",
    0x202B: "BIDI U+202B",
    0x202C: "BIDI U+202C",
    0x202D: "BIDI U+202D",
    0x202E: "BIDI U+202E",
    0x2066: "BIDI U+2066",
    0x2067: "BIDI U+2067",
    0x2068: "BIDI U+2068",
    0x2069: "BIDI U+2069",
    0x200B: "ZERO WIDTH SPACE U+200B",
    0x200C: "ZERO WIDTH NON-JOINER U+200C",
    0x200D: "ZERO WIDTH JOINER U+200D",
    0x2060: "WORD JOINER U+2060",
    0x00AD: "SOFT HYPHEN U+00AD",
    0x00A0: "NO-BREAK SPACE U+00A0",
    0x202F: "NARROW NO-BREAK SPACE U+202F",
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


def dangerous_escape_literals() -> list[str]:
    literals: list[str] = []
    for codepoint in sorted(FORBIDDEN_CODEPOINTS):
        literals.append("\\" + f"u{codepoint:04x}")
        literals.append("\\" + f"U{codepoint:08x}")
    return literals


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

    for offset, character in enumerate(text):
        codepoint = ord(character)
        category = unicodedata.category(character)

        if codepoint == 0x000D:
            continue

        if codepoint in FORBIDDEN_CODEPOINTS:
            findings.append(
                f"{path}: contains hidden/control character "
                f"{FORBIDDEN_CODEPOINTS[codepoint]} at offset {offset}"
            )
        elif category == "Cf":
            findings.append(
                f"{path}: contains Unicode format character "
                f"U+{codepoint:04X} "
                f"{unicodedata.name(character, 'UNKNOWN')} at offset {offset}"
            )
        elif category == "Cc" and codepoint not in ALLOWED_CONTROL_CODEPOINTS:
            findings.append(
                f"{path}: contains unexpected control character "
                f"U+{codepoint:04X} "
                f"{unicodedata.name(character, 'UNKNOWN')} at offset {offset}"
            )

    lowered_text = text.lower()
    for literal in dangerous_escape_literals():
        literal_lower = literal.lower()
        offset = lowered_text.find(literal_lower)
        if offset >= 0:
            findings.append(
                f"{path}: contains dangerous Unicode escape literal "
                f"{literal} at offset {offset}"
            )

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
        "CR bytes, hidden/control formatting characters, dangerous Unicode "
        "escape literals, unexpected control characters, or lines over "
        f"{MAX_LINE_LENGTH} characters."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
