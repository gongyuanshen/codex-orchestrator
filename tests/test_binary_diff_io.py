from pathlib import Path

from orchestrator.workspace_mgr import _read_text_lines_or_binary, _synthesize_unified_diff


def test_binary_probe_never_reads_whole_file(tmp_path, monkeypatch):
    path = tmp_path / "large.apk"
    with path.open("wb") as stream:
        stream.write(b"PK\x00\x01")
        stream.seek(64 * 1024 * 1024)
        stream.write(b"end")
    original = Path.read_bytes
    def reject_whole_binary(self):
        if self == path:
            raise AssertionError("binary file was read whole")
        return original(self)
    monkeypatch.setattr(Path, "read_bytes", reject_whole_binary)
    lines, binary = _read_text_lines_or_binary(path)
    assert binary
    assert "Binary files /dev/null and b/large.apk differ" in _synthesize_unified_diff("large.apk", None, lines, is_binary=binary)
    assert "Binary files a/large.apk and /dev/null differ" in _synthesize_unified_diff("large.apk", lines, None, is_binary=binary)
    assert "Binary files a/large.apk and b/large.apk differ" in _synthesize_unified_diff("large.apk", lines, lines, is_binary=binary)


def test_binary_nul_after_first_chunk_and_text(tmp_path):
    path = tmp_path / "late.bin"
    path.write_bytes(b"a" * (128 * 1024) + b"\x00")
    assert _read_text_lines_or_binary(path) == ([], True)
    path.write_text("中文\nlast line", encoding="utf-8", newline="")
    assert _read_text_lines_or_binary(path) == (["中文\n", "last line"], False)
