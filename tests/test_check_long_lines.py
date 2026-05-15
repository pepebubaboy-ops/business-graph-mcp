from pathlib import Path

from scripts import check_long_lines


def scan_single(path: Path) -> list[str]:
    return check_long_lines.scan_paths([path])


def unicode_escape_literal(codepoint: int) -> str:
    return "\\" + f"u{codepoint:04x}"


def test_lf_text_file_passes(tmp_path):
    path = tmp_path / "normal.md"
    path.write_text("# Title\n\nReadable text.\n", encoding="utf-8", newline="\n")

    assert scan_single(path) == []


def test_cyrillic_text_file_passes(tmp_path):
    path = tmp_path / "cyrillic.md"
    path.write_text("# Заголовок\n\nОбычный русский текст.\n", encoding="utf-8")

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
    path.write_text(
        f"first{chr(0x2028)}second{chr(0x2029)}third\n",
        encoding="utf-8",
        newline="\n",
    )

    findings = scan_single(path)

    assert any("U+2028" in finding for finding in findings)
    assert any("U+2029" in finding for finding in findings)


def test_bidi_controls_fail(tmp_path):
    path = tmp_path / "bidi.md"
    path.write_text(f"safe{chr(0x202E)}text\n", encoding="utf-8", newline="\n")

    findings = scan_single(path)

    assert len(findings) == 1
    assert "U+202E" in findings[0]


def test_utf8_bom_fails(tmp_path):
    path = tmp_path / "bom.md"
    path.write_bytes(b"\xef\xbb\xbf# Title\n")

    findings = scan_single(path)

    assert len(findings) == 1
    assert "U+FEFF" in findings[0]


def test_zero_width_characters_fail(tmp_path):
    path = tmp_path / "zero-width.md"
    path.write_text(
        f"a{chr(0x200B)}b{chr(0x200C)}c{chr(0x200D)}d\n",
        encoding="utf-8",
        newline="\n",
    )

    findings = scan_single(path)

    assert any("U+200B" in finding for finding in findings)
    assert any("U+200C" in finding for finding in findings)
    assert any("U+200D" in finding for finding in findings)


def test_word_joiner_fails(tmp_path):
    path = tmp_path / "word-joiner.md"
    path.write_text(f"safe{chr(0x2060)}text\n", encoding="utf-8", newline="\n")

    findings = scan_single(path)

    assert len(findings) == 1
    assert "U+2060" in findings[0]


def test_soft_hyphen_fails(tmp_path):
    path = tmp_path / "soft-hyphen.md"
    path.write_text(f"soft{chr(0x00AD)}hyphen\n", encoding="utf-8", newline="\n")

    findings = scan_single(path)

    assert len(findings) == 1
    assert "U+00AD" in findings[0]


def test_no_break_spaces_fail(tmp_path):
    path = tmp_path / "no-break-spaces.md"
    path.write_text(
        f"a{chr(0x00A0)}b{chr(0x202F)}c\n",
        encoding="utf-8",
        newline="\n",
    )

    findings = scan_single(path)

    assert any("U+00A0" in finding for finding in findings)
    assert any("U+202F" in finding for finding in findings)


def test_arbitrary_format_character_fails(tmp_path):
    path = tmp_path / "format-character.md"
    path.write_text(f"a{chr(0x061C)}b\n", encoding="utf-8", newline="\n")

    findings = scan_single(path)

    assert len(findings) == 1
    assert "U+061C" in findings[0]


def test_unexpected_control_character_fails(tmp_path):
    path = tmp_path / "control-character.md"
    path.write_text(f"a{chr(0x0007)}b\n", encoding="utf-8", newline="\n")

    findings = scan_single(path)

    assert len(findings) == 1
    assert "unexpected control character U+0007" in findings[0]


def test_dangerous_unicode_escape_literal_fails(tmp_path):
    path = tmp_path / "escape-literal.py"
    literal = unicode_escape_literal(0x202E)
    path.write_text(f"TEXT = 'safe {literal} text'\n", encoding="utf-8", newline="\n")

    findings = scan_single(path)

    assert len(findings) == 1
    assert "dangerous Unicode escape literal" in findings[0]


def test_hygiene_script_source_has_no_dangerous_unicode_literals():
    source = Path("scripts/check_long_lines.py").read_text(encoding="utf-8").lower()

    for codepoint in check_long_lines.FORBIDDEN_CODEPOINTS:
        assert unicode_escape_literal(codepoint) not in source
        assert ("\\" + f"u{codepoint:08x}") not in source
