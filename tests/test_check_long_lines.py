from pathlib import Path

from scripts import check_long_lines


def scan_single(path: Path) -> list[str]:
    return check_long_lines.scan_paths([path])


def test_lf_text_file_passes(tmp_path):
    path = tmp_path / "normal.md"
    path.write_text("# Title\n\nReadable text.\n", encoding="utf-8", newline="\n")

    assert scan_single(path) == []


def test_long_line_fails(tmp_path):
    path = tmp_path / "long.md"
    path.write_text(f"{'x' * 221}\n", encoding="utf-8", newline="\n")

    findings = scan_single(path)

    assert len(findings) == 1
    assert "line is 221 characters" in findings[0]


def test_carriage_return_bytes_fail(tmp_path):
    path = tmp_path / "cr.md"
    path.write_bytes(b"first\r\nsecond\n")

    findings = scan_single(path)

    assert findings == [f"{path}: contains raw carriage return bytes"]


def test_unicode_line_separators_fail(tmp_path):
    path = tmp_path / "unicode-separators.md"
    path.write_text("first\u2028second\u2029third\n", encoding="utf-8", newline="\n")

    findings = scan_single(path)

    assert f"{path}: contains Unicode line separator U+2028" in findings
    assert f"{path}: contains Unicode line separator U+2029" in findings


def test_bidi_controls_fail(tmp_path):
    path = tmp_path / "bidi.md"
    path.write_text("safe\u202etext\n", encoding="utf-8", newline="\n")

    findings = scan_single(path)

    assert findings == [f"{path}: contains Unicode bidi control U+202E"]
