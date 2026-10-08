"""Fix workspace helpers (P08): digests, line endings, flags, git-compatible patches, applying
recipe edits on changed text and the derived manifest."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from crp_analysis.fixes.changeset import (
    FileChange,
    RevisionInfo,
    apply_on_current,
    content_digest,
    derive_entries,
    edit_flags,
    editable_path,
    file_patch,
    keep_line_endings,
    line_count,
)
from crp_analysis.fixes.patching import Edit, PatchError
from crp_analysis.manifest import ManifestEntry
from crp_core.domain.states import ChangeAction, FileDisposition

SHA_A = "a" * 64
SHA_B = "b" * 64


def _git(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed git argv in a test directory
        ["git", *args],  # noqa: S607
        cwd=cwd,
        capture_output=True,
        text=True,
        check=check,
    )


def _tree(root: Path, files: dict[str, str | bytes]) -> None:
    for path, content in files.items():
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            target.write_bytes(content)
        else:
            target.write_bytes(content.encode("utf-8"))


def test_digest_is_order_independent_and_content_bound() -> None:
    one = FileChange("a.js", ChangeAction.MODIFY, SHA_A, SHA_B)
    two = FileChange("b.js", ChangeAction.DELETE, SHA_A, None)
    assert content_digest([one, two]) == content_digest([two, one])
    assert content_digest([one]) != content_digest([one, two])
    changed = FileChange("a.js", ChangeAction.MODIFY, SHA_A, SHA_A)
    assert content_digest([one]) != content_digest([changed])
    assert len(content_digest([])) == 64


def test_browser_edits_keep_crlf_line_endings() -> None:
    assert keep_line_endings("a\r\nb\r\n", "a\nc\n") == "a\r\nc\r\n"
    assert keep_line_endings("a\nb\n", "a\nc\n") == "a\nc\n"
    assert keep_line_endings(None, "new\n") == "new\n"
    assert keep_line_endings("a\r\n", "already\r\ncrlf\r\n") == "already\r\ncrlf\r\n"


def test_line_count() -> None:
    assert line_count("") == 0
    assert line_count("a") == 1
    assert line_count("a\n") == 1
    assert line_count("a\nb") == 2


def test_manual_flags_are_information_and_recipe_flags_are_strict() -> None:
    before = "const a = 1;\n"
    after = "// eslint-disable-next-line\nconst a = 1;\n"
    assert "suppression_added" in edit_flags("src/a.js", before, after, strict=False)
    config = edit_flags("package.json", '{"a": 1}\n', '{"a": 2}\n', strict=False)
    assert config == ["config_change"]
    test_before = "it('x', () => { expect(a).toBe(1); });\n"
    test_after = "it.skip('x', () => { expect(a).toBe(1); });\n"
    assert "test_weakened" in edit_flags("src/a.test.js", test_before, test_after, strict=False)
    # Strict mode keeps size and scope codes that manual edits do not report.
    big = "x\n" * 500
    assert "too_large" in edit_flags("src/a.js", "x\n", big, strict=True)
    assert "too_large" not in edit_flags("src/a.js", "x\n", big, strict=False)


@pytest.mark.parametrize(
    ("path", "ok"),
    [
        ("src/a.js", True),
        ("a b/c d.java", True),
        ("../a.js", False),
        ("/etc/passwd", False),
        (".git/config", False),
        ("src/.git/hooks/x", False),
        ("a\\b.js", False),
        ("src/", False),
        ("", False),
    ],
)
def test_editable_path(path: str, ok: bool) -> None:
    assert editable_path(path) is ok


def test_patch_applies_with_git_only_on_the_exact_base(tmp_path: Path) -> None:
    base = {
        "src/app.js": "const a = 1;\nconsole.log(a)\n",
        "src/old.js": "remove me\n",
        "docs/my notes.txt": "line one\nline two",
        "win/App.cls": "public class App {\r\n  void a() {}\r\n}\r\n",
    }
    after = {
        "src/app.js": "const a = 1;\nconsole.log(a);\n",
        "src/new.js": "export const b = 2;\n",
        "docs/my notes.txt": "line one\nline 2",
        "win/App.cls": "public class App {\r\n  void b() {}\r\n}\r\n",
    }
    patch = "".join(
        [
            file_patch("src/app.js", base["src/app.js"], after["src/app.js"]),
            file_patch("src/new.js", None, after["src/new.js"]),
            file_patch("src/old.js", base["src/old.js"], None),
            file_patch("docs/my notes.txt", base["docs/my notes.txt"], after["docs/my notes.txt"]),
            file_patch("win/App.cls", base["win/App.cls"], after["win/App.cls"]),
        ]
    )
    assert "new file mode 100644" in patch
    assert "deleted file mode 100644" in patch
    repo = tmp_path / "repo"
    repo.mkdir()
    _tree(repo, base)
    (tmp_path / "w.diff").write_text(patch, encoding="utf-8", newline="")
    _git(repo, "apply", "--check", "-p1", str(tmp_path / "w.diff"))
    _git(repo, "apply", "-p1", str(tmp_path / "w.diff"))
    for path, text in after.items():
        assert (repo / path).read_bytes() == text.encode("utf-8"), path
    assert not (repo / "src/old.js").exists()

    drifted = tmp_path / "drifted"
    drifted.mkdir()
    _tree(drifted, {**base, "src/app.js": "const a = 2;\nconsole.log(a)\n"})
    result = _git(drifted, "apply", "--check", "-p1", str(tmp_path / "w.diff"), check=False)
    assert result.returncode != 0


def test_unchanged_file_has_an_empty_patch() -> None:
    assert file_patch("a.js", "same\n", "same\n") == ""


def test_executable_mode_is_kept_for_added_files(tmp_path: Path) -> None:
    patch = file_patch("bin/run.sh", None, "#!/bin/sh\necho hi\n", mode="100755")
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    (tmp_path / "x.diff").write_text(patch, encoding="utf-8")
    _git(repo, "apply", "-p1", str(tmp_path / "x.diff"))
    assert (repo / "bin/run.sh").stat().st_mode & 0o111


def test_recipe_edits_apply_on_changed_text_or_conflict() -> None:
    base = "a\nif (x == 'y') {}\nb\n"
    edit = Edit("f.js", 2, 2, ("if (x == 'y') {}",), ("if (x === 'y') {}",))
    assert apply_on_current(base, [edit]) == "a\nif (x === 'y') {}\nb\n"
    shifted = "new first line\na\nif (x == 'y') {}\nb\n"
    assert apply_on_current(shifted, [edit]) == "new first line\na\nif (x === 'y') {}\nb\n"
    with pytest.raises(PatchError):
        apply_on_current("a\nif (x === 'y') {}\nb\n", [edit])  # already changed by hand
    with pytest.raises(PatchError):
        apply_on_current("if (x == 'y') {}\nx\nif (x == 'y') {}\n", [edit])  # ambiguous


def _entry(path: str, sha: str | None, disposition: FileDisposition) -> ManifestEntry:
    return ManifestEntry(path, disposition, None, 10, sha, "javascript", "source", 1)


def test_derived_manifest_applies_revisions_over_the_base() -> None:
    base = [
        _entry("src/a.js", SHA_A, FileDisposition.ANALYZABLE),
        _entry("src/b.js", SHA_A, FileDisposition.ANALYZABLE),
        _entry("src/c.js", SHA_A, FileDisposition.ANALYZABLE),
    ]
    revisions = [
        RevisionInfo(FileChange("src/a.js", ChangeAction.MODIFY, SHA_A, SHA_B), 12, 2),
        RevisionInfo(FileChange("src/b.js", ChangeAction.DELETE, SHA_A, None), None, None),
        RevisionInfo(FileChange("src/new.ts", ChangeAction.ADD, None, SHA_B), 5, 1),
        RevisionInfo(FileChange(".env", ChangeAction.ADD, None, SHA_B), 5, 1),
    ]
    entries = {e.path: e for e in derive_entries(base, revisions)}
    assert sorted(entries) == [".env", "src/a.js", "src/c.js", "src/new.ts"]
    assert entries["src/a.js"].sha256 == SHA_B
    assert entries["src/a.js"].size_bytes == 12
    assert entries["src/c.js"] == base[2]
    assert entries["src/new.ts"].disposition is FileDisposition.ANALYZABLE
    assert entries["src/new.ts"].language == "typescript"
    # A secrets-like file added in the workspace is excluded exactly like at upload.
    assert entries[".env"].disposition is FileDisposition.EXCLUDED
    assert entries[".env"].sha256 is None
