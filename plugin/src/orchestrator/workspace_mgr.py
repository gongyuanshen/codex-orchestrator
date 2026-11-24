"""Isolated workspace manager for git clone --local and non-git snapshot fallback."""

from __future__ import annotations

import difflib
import fnmatch
import hashlib
import json
import logging
import os
import re
import shutil
import stat
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from functools import cache
from pathlib import Path, PurePosixPath
from typing import Any

from orchestrator.config import Settings
from orchestrator.db import Database, NotFoundError, resolve_execution_root

logger = logging.getLogger(__name__)

try:
    import _winapi
except ImportError:  # pragma: no cover - non-Windows fallback
    _winapi = None  # type: ignore[assignment]

VALID_DEP_MODES = frozenset({"none", "link_ro_cache", "fresh_venv_uv", "copy"})
VALID_MERGE_STRATEGIES = frozenset({"merge", "cherry-pick"})
VALID_GIT_BACKENDS = frozenset({"auto", "clone", "worktree"})
WORKTREE_COPY_HINT = "Make the ignore rule available to the linked checkout (for example, commit it) or use git_backend='clone'."

WORKSPACES_DIRNAME = ".swarm-workspaces"
EXCLUDE_ENTRY = ".swarm-workspaces/"
TRIIGNORE_FILENAME = ".triignore"
SNAPSHOT_EXCLUDE_DIRS = frozenset(
    {
        WORKSPACES_DIRNAME,
        ".git",
        "node_modules",
        ".venv",
        "__pycache__",
        ".pytest_cache",
        ".ruff_cache",
        ".mypy_cache",
        "bin",
        "obj",
        "dist",
        "build",
        "out",
        "target",
        ".tox",
        ".nox",
        ".coverage",
        "htmlcov",
        ".gradle",
        ".next",
        ".nuxt",
        ".turbo",
        ".parcel-cache",
    }
)
ENV_FILENAMES = (".env", ".env.local")
DEP_DIRNAMES = (".venv", "node_modules")

_SAFE_SEGMENT_RE = re.compile(r"[^A-Za-z0-9._-]+")


class WorkspaceError(RuntimeError):
    """Base error for isolated workspace operations."""


class WorkspaceCleanupGuardError(WorkspaceError):
    """Raised when workspace_cleanup refuses deletion of an unmerged or dirty workspace."""


class WorkspaceConflictError(WorkspaceError):
    """Raised when a merge or state transition fails due to conflicts or dirty state."""


class WorkspaceGitError(WorkspaceError):
    """Raised when a git subprocess returns an unexpected failure."""


def _subprocess_flags() -> int:
    if os.name == "nt":
        return int(getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return 0


def _run_cmd(
    args: list[str],
    *,
    cwd: Path | None = None,
    check: bool = False,
) -> subprocess.CompletedProcess[str]:
    """Run a subprocess safely on Windows with UTF-8 output decoding."""
    proc = subprocess.run(
        args,
        cwd=str(cwd) if cwd is not None else None,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdin=subprocess.DEVNULL,
        creationflags=_subprocess_flags(),
    )
    if check and proc.returncode != 0:
        msg = (proc.stderr or proc.stdout or "").strip()
        raise WorkspaceGitError(f"Command {' '.join(args)} failed ({proc.returncode}): {msg}")
    return proc


def _run_git(
    repo_dir: Path,
    *args: str,
    check: bool = False,
) -> subprocess.CompletedProcess[str]:
    return _run_cmd(["git", "-C", str(repo_dir), *args], check=check)


def _sanitize_segment(raw: str, fallback: str) -> str:
    cleaned = _SAFE_SEGMENT_RE.sub("-", raw.strip()).strip("-.")
    # Prevent git ref rules issues such as '..' or '.lock' suffix.
    while ".." in cleaned:
        cleaned = cleaned.replace("..", "-")
    if cleaned.endswith(".lock"):
        cleaned = cleaned[:-5] + "-lock"
    return cleaned or fallback


def _short_run_id(run_id: str) -> str:
    s = _sanitize_segment(run_id, "run")
    if s.startswith("run_") and len(s) > 4:
        return s[:12]
    return s[:12]


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            chunk = fh.read(65536)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _hardlink_or_copy(src: str, dst: str) -> str:
    """Link file as hardlink when possible on same volume, falling back to copy2."""
    try:
        os.link(src, dst)
        return dst
    except OSError:
        return shutil.copy2(src, dst)


def _unlink_reparse_point(path: Path) -> bool:
    """Safely remove a directory junction or symlink without deleting target contents."""
    try:
        is_junction = hasattr(path, "is_junction") and path.is_junction()
    except OSError:
        is_junction = False
    if is_junction:
        os.rmdir(path)
        return True
    if path.is_symlink():
        try:
            path.unlink()
        except IsADirectoryError:
            os.rmdir(path)
        return True
    return False


def _onexc_remove_readonly(func: Any, path: str, exc: BaseException) -> None:
    """Clear Windows read-only attribute (e.g. .git/objects/*) before retrying deletion."""
    p = Path(path)
    try:
        os.chmod(p, stat.S_IWRITE | stat.S_IREAD)
    except OSError:
        pass
    try:
        if p.parent.exists():
            os.chmod(p.parent, stat.S_IWRITE | stat.S_IREAD | stat.S_IEXEC)
    except OSError:
        pass
    func(path)


def _rmtree_safe(root: Path) -> None:
    """Remove a directory tree cleanly on Windows without following junctions or failing on readonly files."""
    if not root.exists() and not root.is_symlink():
        return
    if _unlink_reparse_point(root):
        return

    # Pre-detach any dependency junctions/symlinks so rmtree never recurses into shared caches.
    for dep_name in DEP_DIRNAMES:
        dep_path = root / dep_name
        if dep_path.is_symlink() or (hasattr(dep_path, "is_junction") and dep_path.is_junction()):
            _unlink_reparse_point(dep_path)

    for attempt in range(6):
        if not root.exists() and not root.is_symlink():
            return
        try:
            shutil.rmtree(root, onexc=_onexc_remove_readonly)
            return
        except PermissionError:
            if attempt >= 5:
                raise
            time.sleep(0.05 * (attempt + 1))
        except OSError as exc:
            if getattr(exc, "winerror", None) in {5, 32, 145} and attempt < 5:
                time.sleep(0.05 * (attempt + 1))
                continue
            raise


def _append_git_exclude_entries(git_dir: Path, entries: list[str]) -> Path:
    """Idempotently append exclude rules to <git_dir>/info/exclude without touching .gitignore."""
    exclude_path = git_dir / "info" / "exclude"
    exclude_path.parent.mkdir(parents=True, exist_ok=True)
    if exclude_path.exists():
        existing_text = exclude_path.read_text(encoding="utf-8", errors="replace")
    else:
        existing_text = ""

    existing_lines = {line.strip().rstrip("/") for line in existing_text.splitlines() if line.strip()}
    missing = [e for e in entries if e.strip().rstrip("/") not in existing_lines]
    if missing:
        prefix = ""
        if existing_text and not existing_text.endswith(("\n", "\r")):
            prefix = "\n"
        new_text = existing_text + prefix + "\n".join(missing) + "\n"
        exclude_path.write_text(new_text, encoding="utf-8")
    return exclude_path


def _read_text_lines_or_binary(path: Path) -> tuple[list[str] | None, bool]:
    """Return (lines, is_binary) for unified diff generation."""
    # Scan with bounded memory before decoding; APK/build artifacts can be huge.
    with path.open("rb") as stream:
        while chunk := stream.read(64 * 1024):
            if b"\x00" in chunk:
                # None denotes an absent file, not an existing binary file.
                return [], True
        stream.seek(0)
        raw = stream.read()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("utf-8", errors="replace")
    return text.splitlines(keepends=True), False


def _synthesize_unified_diff(
    rel_path: str,
    old_lines: list[str] | None,
    new_lines: list[str] | None,
    *,
    is_binary: bool = False,
) -> str:
    """Build git-style unified diff text for snapshot or untracked files."""
    if old_lines is None and new_lines is None:
        return ""
    if old_lines is None:
        header = f"diff --git a/{rel_path} b/{rel_path}\nnew file mode 100644\n"
        fromfile = "/dev/null"
        tofile = f"b/{rel_path}"
        old_seq: list[str] = []
        new_seq = new_lines or []
    elif new_lines is None:
        header = f"diff --git a/{rel_path} b/{rel_path}\ndeleted file mode 100644\n"
        fromfile = f"a/{rel_path}"
        tofile = "/dev/null"
        old_seq = old_lines
        new_seq = []
    else:
        header = f"diff --git a/{rel_path} b/{rel_path}\n"
        fromfile = f"a/{rel_path}"
        tofile = f"b/{rel_path}"
        old_seq = old_lines
        new_seq = new_lines

    if is_binary:
        return header + f"Binary files {fromfile} and {tofile} differ\n"

    diff_lines = list(
        difflib.unified_diff(
            old_seq,
            new_seq,
            fromfile=fromfile,
            tofile=tofile,
            lineterm="\n",
        )
    )
    if not diff_lines:
        return ""
    # difflib may omit trailing newline on lines without EOL; normalize cleanly.
    normalized = [line if line.endswith("\n") else f"{line}\n" for line in diff_lines]
    return header + "".join(normalized)


def _is_reparse_or_symlink(path: Path) -> bool:
    if path.is_symlink():
        return True
    try:
        if hasattr(path, "is_junction") and path.is_junction():
            return True
        reparse_attr = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        if reparse_attr:
            st = path.lstat()
            if getattr(st, "st_file_attributes", 0) & reparse_attr:
                return True
    except OSError:
        return False
    return False


def _seg_glob_to_regex(segment: str) -> str:
    """Convert a single path-segment glob pattern into a regex fragment."""
    out: list[str] = []
    i = 0
    n = len(segment)
    while i < n:
        ch = segment[i]
        if ch == "*":
            out.append("[^/]*")
            i += 1
        elif ch == "?":
            out.append("[^/]")
            i += 1
        elif ch == "[":
            j = i + 1
            if j < n and segment[j] in ("!", "^"):
                j += 1
            if j < n and segment[j] == "]":
                j += 1
            while j < n and segment[j] != "]":
                j += 1
            if j >= n:
                out.append(re.escape(ch))
                i += 1
            else:
                inner = segment[i + 1 : j]
                if inner.startswith("!"):
                    inner = "^" + inner[1:]
                out.append(f"[{inner}]")
                i = j + 1
        else:
            out.append(re.escape(ch))
            i += 1
    return "".join(out)


def _compile_path_glob(pattern: str) -> re.Pattern[str]:
    raw_segments = [s for s in pattern.split("/") if s]
    segments: list[str] = []
    for seg in raw_segments:
        if seg == "**" and segments and segments[-1] == "**":
            continue
        segments.append(seg)

    parts_re: list[str] = ["^"]
    n = len(segments)
    for idx, seg in enumerate(segments):
        if seg == "**":
            if n == 1:
                parts_re.append(".+")
            elif idx == 0:
                parts_re.append("(?:.+/)?")
            elif idx == n - 1:
                parts_re.append("/.+")
            else:
                parts_re.append("/(?:.+/)?")
        else:
            if idx > 0 and segments[idx - 1] != "**":
                parts_re.append("/")
            parts_re.append(_seg_glob_to_regex(seg))
    parts_re.append("$")
    flags = re.IGNORECASE if os.name == "nt" else 0
    return re.compile("".join(parts_re), flags)


@dataclass(frozen=True, slots=True)
class _TriIgnoreRule:
    pattern: str
    dir_only: bool
    anchored: bool
    has_slash: bool
    regex: re.Pattern[str] | None = None

    def matches(self, rel_posix: str, name: str, *, is_dir: bool) -> bool:
        if self.dir_only and not is_dir:
            return False
        if not self.has_slash and not self.anchored:
            return fnmatch.fnmatch(name, self.pattern)
        if self.anchored and not self.has_slash:
            return "/" not in rel_posix and fnmatch.fnmatch(name, self.pattern)
        return self.regex is not None and bool(self.regex.match(rel_posix))


@dataclass(frozen=True, slots=True)
class _TriIgnoreMatcher:
    rules: tuple[_TriIgnoreRule, ...] = ()
    text: str = ""

    def matches(self, rel_posix: str, *, is_dir: bool) -> bool:
        if not self.rules:
            return False
        name = rel_posix.rsplit("/", 1)[-1]
        return any(rule.matches(rel_posix, name, is_dir=is_dir) for rule in self.rules)

    def is_rel_path_excluded(self, rel_posix: str) -> bool:
        parts = PurePosixPath(rel_posix).parts
        if any(part in SNAPSHOT_EXCLUDE_DIRS for part in parts[:-1]):
            return True
        if not self.rules:
            return False
        for idx in range(1, len(parts)):
            anc = "/".join(parts[:idx])
            if self.matches(anc, is_dir=True):
                return True
        return self.matches(rel_posix, is_dir=False)


def _read_triignore(project_root: Path) -> str:
    ignore_path = project_root / TRIIGNORE_FILENAME
    if not ignore_path.is_file() or _is_reparse_or_symlink(ignore_path):
        return ""
    return ignore_path.read_text(encoding="utf-8-sig", errors="replace")


def _parse_triignore(text: str) -> _TriIgnoreMatcher:
    rules: list[_TriIgnoreRule] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        pat = line.replace("\\", "/")
        dir_only = pat.endswith("/")
        pat = pat.rstrip("/")
        anchored = pat.startswith("/")
        pat = pat.lstrip("/")
        if not pat:
            continue
        has_slash = "/" in pat
        regex = _compile_path_glob(pat) if has_slash else None
        rules.append(
            _TriIgnoreRule(
                pattern=pat,
                dir_only=dir_only,
                anchored=anchored,
                has_slash=has_slash,
                regex=regex,
            )
        )
    return _TriIgnoreMatcher(tuple(rules), text)


def _snapshot_triignore(ws: dict[str, Any], project_root: Path) -> _TriIgnoreMatcher:
    manifest = ws.get("snapshot_manifest") or {}
    if "triignore" in manifest:
        return _parse_triignore(manifest["triignore"])
    # Older snapshots stored the ignore file's hash, not its text. Never trust changed rules.
    expected = (manifest.get("files") or {}).get(TRIIGNORE_FILENAME)
    if expected is None:
        return _TriIgnoreMatcher()
    for root in (Path(ws["path"]), project_root):
        path = root / TRIIGNORE_FILENAME
        if path.is_file() and not _is_reparse_or_symlink(path):
            data = path.read_bytes()
            if hashlib.sha256(data).hexdigest() == expected:
                return _parse_triignore(data.decode("utf-8-sig", errors="replace"))
    raise WorkspaceError(f"Cannot recover the original .triignore for snapshot {ws['id']} (SHA-256 {expected})")


def _filter_snapshot_baseline(
    baseline: dict[str, str],
    *,
    triignore: _TriIgnoreMatcher,
) -> dict[str, str]:
    """Drop historical baseline entries that now fall under snapshot exclusion rules."""
    if not baseline:
        return {}
    return {
        rel_path: file_hash
        for rel_path, file_hash in baseline.items()
        if not triignore.is_rel_path_excluded(rel_path)
    }


def _scan_snapshot_files(
    root: Path,
    *,
    exclude_root_files: Iterable[str] = frozenset(),
    triignore: _TriIgnoreMatcher | None = None,
) -> dict[str, str]:
    """Walk root and return {posix_rel_path: sha256_hex} excluding ignored directories/files."""
    manifest: dict[str, str] = {}
    if not root.exists():
        return manifest
    excluded_root = frozenset(exclude_root_files)
    matcher = triignore if triignore is not None else _parse_triignore(_read_triignore(root))

    for dirpath, dirnames, filenames in os.walk(root):
        parent = Path(dirpath)
        rel_dir = parent.relative_to(root)
        is_root = rel_dir == Path(".")
        kept_dirs: list[str] = []
        for d in sorted(dirnames):
            if d in SNAPSHOT_EXCLUDE_DIRS or _is_reparse_or_symlink(parent / d):
                continue
            rel_d = d if is_root else f"{rel_dir.as_posix()}/{d}"
            if matcher.matches(rel_d, is_dir=True):
                continue
            kept_dirs.append(d)
        dirnames[:] = kept_dirs

        for fname in sorted(filenames):
            if is_root and fname in excluded_root:
                continue
            full_path = parent / fname
            if _is_reparse_or_symlink(full_path):
                continue
            rel_posix = fname if is_root else f"{rel_dir.as_posix()}/{fname}"
            if matcher.matches(rel_posix, is_dir=False):
                continue
            manifest[rel_posix] = _sha256_file(full_path)
    return manifest


def _estimate_snapshot_size(
    root: Path,
    *,
    copy_env: bool,
    triignore: _TriIgnoreMatcher,
) -> tuple[int, int]:
    """Estimate total bytes and file count that snapshot mode would copy from root."""
    total_bytes = 0
    file_count = 0

    for dirpath, dirnames, filenames in os.walk(root):
        parent = Path(dirpath)
        rel_dir = parent.relative_to(root)
        is_root = rel_dir == Path(".")
        kept_dirs: list[str] = []
        for d in sorted(dirnames):
            if d in SNAPSHOT_EXCLUDE_DIRS or _is_reparse_or_symlink(parent / d):
                continue
            rel_d = d if is_root else f"{rel_dir.as_posix()}/{d}"
            if triignore.matches(rel_d, is_dir=True):
                continue
            kept_dirs.append(d)
        dirnames[:] = kept_dirs

        for fname in sorted(filenames):
            if not copy_env and is_root and fname in ENV_FILENAMES:
                continue
            src_file = parent / fname
            if _is_reparse_or_symlink(src_file):
                continue
            rel_posix = fname if is_root else f"{rel_dir.as_posix()}/{fname}"
            if triignore.matches(rel_posix, is_dir=False):
                continue
            try:
                total_bytes += src_file.lstat().st_size
                file_count += 1
            except OSError:
                continue

    return total_bytes, file_count


@dataclass(frozen=True, slots=True)
class _WorkspaceSource:
    root: Path
    placement_root: Path
    env_roots: tuple[Path, ...]
    git_common_dir: Path | None
    use_git_clone: bool


class WorkspaceManager:
    """Manages isolated worker workspaces (git clone --local or non-git snapshot) and safe merges."""

    _project_locks_guard = threading.Lock()
    _project_locks: dict[str, threading.RLock] = {}

    def __init__(self, db: Database, settings: Settings | None = None) -> None:
        self.db = db
        self.settings = settings or db.settings
        self._lock = threading.RLock()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _require_workspace(self, workspace_id: str) -> dict[str, Any]:
        ws = self.db.get_workspace(workspace_id)
        if ws is None:
            raise NotFoundError(f"Workspace not found: {workspace_id}")
        return ws

    def _run_project(self, run_id: str) -> tuple[dict[str, Any], Path]:
        run = self.db.get_run(run_id)
        if run is None:
            raise NotFoundError(f"Run not found: {run_id}")
        project = self.db.get_project(run["project_id"])
        if project is None:
            raise NotFoundError(f"Project not found: {run['project_id']}")
        return run, Path(project["root_path"]).resolve(strict=False)

    def registered_project_root(self, run_id: str) -> Path:
        return self._run_project(run_id)[1]

    def _resolve_workspace_source(
        self, run_id: str, project_root: str | Path | None = None
    ) -> _WorkspaceSource:
        run, registered = self._run_project(run_id)
        override = Path(project_root).resolve(strict=False) if project_root is not None else None
        execution = Path(run["execution_root"]).resolve(strict=False) if run["execution_root"] else None
        if execution and (override is None or override == execution):
            execution = Path(resolve_execution_root(str(execution), registered))
        else:
            execution = None
        # A committed Git workspace keeps repository coordinates; otherwise copy only the slice.
        git_probe_root = registered if override is None or override == execution else override
        is_git, repo_root, git_common_dir, has_head = self._detect_git_repo(git_probe_root)
        use_git_clone = is_git and has_head
        root = repo_root if use_git_clone else execution or override or registered
        return _WorkspaceSource(
            root=root,
            placement_root=execution or root,
            env_roots=tuple(dict.fromkeys((execution or git_probe_root, git_probe_root, repo_root))),
            git_common_dir=git_common_dir,
            use_git_clone=use_git_clone,
        )

    def run_source_root(self, run_id: str) -> Path:
        """Return the source/merge coordinates for a run's workflow, before any scan."""
        return self._resolve_workspace_source(run_id).root

    def workspace_source_root(self, ws: dict[str, Any]) -> Path:
        manifest = ws.get("snapshot_manifest") or {}
        if isinstance(manifest, dict) and manifest.get("project_root"):
            return Path(manifest["project_root"]).resolve(strict=False)
        root = self.registered_project_root(ws["run_id"])
        # If project_root is a subdirectory of a git repo, map to git toplevel for git operations.
        if ws["mode"] == "git_clone":
            top = _run_git(root, "rev-parse", "--show-toplevel")
            if top.returncode == 0 and top.stdout.strip():
                return Path(top.stdout.strip()).resolve(strict=False)
        return root

    def _workspace_mutation_lock(self, ws: dict[str, Any], root: Path) -> threading.RLock:
        if ws["mode"] == "snapshot":
            project = self.registered_project_root(ws["run_id"])
            if root.is_relative_to(project):
                root = project  # A parent snapshot and a slice can write the same physical file.
        key = os.path.normcase(str(root.resolve()))
        with self._project_locks_guard:
            return self._project_locks.setdefault(key, threading.RLock())

    @staticmethod
    def _detect_git_repo(project_root: Path) -> tuple[bool, Path, Path | None, bool]:
        """Return (is_git_worktree, repo_root, git_common_dir, has_head_commit)."""
        probe = _run_git(project_root, "rev-parse", "--is-inside-work-tree")
        if probe.returncode != 0 or probe.stdout.strip().lower() != "true":
            return False, project_root, None, False

        top_proc = _run_git(project_root, "rev-parse", "--show-toplevel")
        repo_root = (
            Path(top_proc.stdout.strip()).resolve(strict=False)
            if top_proc.returncode == 0 and top_proc.stdout.strip()
            else project_root
        )
        git_dir_proc = _run_git(repo_root, "rev-parse", "--git-common-dir")
        if git_dir_proc.returncode == 0 and git_dir_proc.stdout.strip():
            raw_git_dir = Path(git_dir_proc.stdout.strip())
            git_dir = (
                raw_git_dir.resolve(strict=False)
                if raw_git_dir.is_absolute()
                else (repo_root / raw_git_dir).resolve(strict=False)
            )
        else:
            git_dir = repo_root / ".git"

        head_proc = _run_git(repo_root, "rev-parse", "--verify", "HEAD")
        has_head = head_proc.returncode == 0 and bool(head_proc.stdout.strip())
        return True, repo_root, git_dir, has_head

    @staticmethod
    def _current_branch_or_head(repo_root: Path) -> str:
        proc = _run_git(repo_root, "symbolic-ref", "--short", "HEAD")
        if proc.returncode == 0 and proc.stdout.strip():
            return proc.stdout.strip()
        return "HEAD"

    @staticmethod
    def _git_identity_args(repo_root: Path) -> list[str]:
        """Return -c user.name/email overrides only when repo has no git identity configured."""
        args: list[str] = []
        name_proc = _run_git(repo_root, "config", "user.name")
        email_proc = _run_git(repo_root, "config", "user.email")
        if name_proc.returncode != 0 or not name_proc.stdout.strip():
            args.extend(["-c", "user.name=tri-orchestrator"])
        if email_proc.returncode != 0 or not email_proc.stdout.strip():
            args.extend(["-c", "user.email=orchestrator@localhost"])
        return args

    @staticmethod
    def _copy_git_identity(src_repo: Path, dst_repo: Path) -> None:
        for key in ("user.name", "user.email"):
            proc = _run_git(src_repo, "config", "--get", key)
            if proc.returncode == 0 and proc.stdout.strip():
                _run_git(dst_repo, "config", key, proc.stdout.strip())

    @staticmethod
    def _is_git_tracked(repo_dir: Path, rel_path: str) -> bool:
        proc = _run_git(repo_dir, "ls-files", "--error-unmatch", rel_path)
        return proc.returncode == 0

    @staticmethod
    def _is_git_ignored(repo_dir: Path, rel_path: str) -> bool:
        proc = _run_git(repo_dir, "check-ignore", "-q", rel_path)
        return proc.returncode == 0

    @staticmethod
    def _validate_worktree_copy_rules(repo_dir: Path, paths: list[str]) -> None:
        for path in paths:
            tracked = _run_git(repo_dir, "ls-tree", "--name-only", "HEAD", "--", path.rstrip("/"), check=True)
            if tracked.stdout.strip():
                continue
            ignored = _run_git(repo_dir, "check-ignore", "--no-index", "-q", "--", path)
            if ignored.returncode == 1:
                raise WorkspaceError(f"Git worktree cannot copy unignored local path {path!r}. {WORKTREE_COPY_HINT}")
            if ignored.returncode != 0:
                raise WorkspaceGitError(f"Git could not check ignore rules for {path!r}: {ignored.stderr.strip()}")

    @staticmethod
    def _append_workspace_excludes(ws_dir: Path, entries: list[str]) -> None:
        git_dir = ws_dir / ".git"
        if git_dir.is_file():
            raise WorkspaceError(f"Git worktree cannot copy unignored local paths {entries!r}. {WORKTREE_COPY_HINT}")
        _append_git_exclude_entries(git_dir, entries)

    def _copy_git_env_files(
        self,
        source_roots: tuple[Path, ...],
        ws_dir: Path,
    ) -> None:
        excludes_to_add: list[str] = []
        for fname in ENV_FILENAMES:
            src_file: Path | None = None
            for root in source_roots:
                candidate = root / fname
                if candidate.is_file():
                    src_file = candidate
                    break
            if src_file is None:
                continue
            dst_file = ws_dir / fname
            if self._is_git_tracked(ws_dir, fname):
                # Already checked out from tracked git history; do not overwrite or exclude.
                continue
            shutil.copy2(src_file, dst_file)
            if not self._is_git_ignored(ws_dir, fname):
                excludes_to_add.append(fname)
        if excludes_to_add:
            self._append_workspace_excludes(ws_dir, excludes_to_add)

    def _apply_dep_mode(
        self,
        source_root: Path,
        ws_dir: Path,
        dep_mode: str,
        *,
        is_git: bool,
    ) -> list[str]:
        if dep_mode == "none":
            return []

        created_deps: list[str] = []
        excludes_to_add: list[str] = []

        if dep_mode == "fresh_venv_uv":
            dst_venv = ws_dir / ".venv"
            if not dst_venv.exists():
                uv_bin = shutil.which("uv")
                if uv_bin:
                    proc = _run_cmd([uv_bin, "venv", str(dst_venv)], cwd=ws_dir)
                    if proc.returncode != 0:
                        _run_cmd([sys.executable, "-m", "venv", str(dst_venv)], cwd=ws_dir, check=True)
                else:
                    _run_cmd([sys.executable, "-m", "venv", str(dst_venv)], cwd=ws_dir, check=True)
                created_deps.append(".venv")
                if is_git and not self._is_git_tracked(ws_dir, ".venv") and not self._is_git_ignored(ws_dir, ".venv"):
                    excludes_to_add.append(".venv/")

        elif dep_mode in {"link_ro_cache", "copy"}:
            for dep_name in DEP_DIRNAMES:
                src_dep = source_root / dep_name
                if not src_dep.is_dir():
                    continue
                dst_dep = ws_dir / dep_name
                if dst_dep.exists() or dst_dep.is_symlink():
                    continue

                if dep_mode == "copy":
                    shutil.copytree(src_dep, dst_dep, symlinks=True)
                else:
                    # Windows-safe link strategy: Junction -> Directory Symlink -> Hardlink/Copy fallback.
                    linked = False
                    if os.name == "nt" and _winapi is not None and hasattr(_winapi, "CreateJunction"):
                        try:
                            _winapi.CreateJunction(str(src_dep), str(dst_dep))
                            linked = True
                        except OSError:
                            linked = False
                    if not linked:
                        try:
                            os.symlink(str(src_dep), str(dst_dep), target_is_directory=True)
                            linked = True
                        except OSError:
                            linked = False
                    if not linked:
                        shutil.copytree(
                            src_dep,
                            dst_dep,
                            copy_function=_hardlink_or_copy,
                            symlinks=True,
                        )

                created_deps.append(dep_name)
                if (
                    is_git
                    and not self._is_git_tracked(ws_dir, dep_name)
                    and not self._is_git_ignored(ws_dir, dep_name)
                ):
                    excludes_to_add.append(f"{dep_name}/")

        if is_git and excludes_to_add:
            self._append_workspace_excludes(ws_dir, excludes_to_add)
        return created_deps

    def _allocate_workspace_dir_and_branch(
        self,
        base_root: Path,
        run_id: str,
        worker_name: str,
    ) -> tuple[Path, str]:
        run_short = _short_run_id(run_id)
        safe_worker = _sanitize_segment(worker_name, "worker")
        workspaces_root = base_root / WORKSPACES_DIRNAME
        workspaces_root.mkdir(parents=True, exist_ok=True)

        candidate_name = f"{run_short}-{safe_worker}"
        candidate_dir = workspaces_root / candidate_name
        branch_name = f"swarm/{run_short}/{safe_worker}"
        suffix = 2
        while candidate_dir.exists() or candidate_dir.is_symlink():
            candidate_name = f"{run_short}-{safe_worker}-{suffix}"
            candidate_dir = workspaces_root / candidate_name
            branch_name = f"swarm/{run_short}/{safe_worker}-{suffix}"
            suffix += 1
        return candidate_dir, branch_name

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @staticmethod
    def _uses_git_worktree(ws: dict[str, Any]) -> bool:
        if ws["mode"] != "git_clone":
            return False
        manifest = ws["snapshot_manifest"]
        if isinstance(manifest, dict) and manifest.get("git_backend") == "worktree":
            return True
        if (Path(ws["path"]) / ".git").is_file():
            raise WorkspaceCleanupGuardError(f"Linked worktree at {ws['path']} has missing or invalid backend metadata")
        return False

    def _require_preserved_clone_refs(self, ws: dict[str, Any]) -> None:
        if self._uses_git_worktree(ws):
            return  # Its branches and stash already live in the source repository.
        path, root = Path(ws["path"]), self.workspace_source_root(ws)
        branches = _run_git(path, "for-each-ref", "--format=%(refname)%09%(objectname)",
                            "refs/heads", check=True).stdout.splitlines()
        stashes = _run_git(path, "stash", "list", "--format=%gd%x09%H", check=True).stdout.splitlines()
        source_stashes = (set(_run_git(root, "stash", "list", "--format=%H", check=True).stdout.splitlines())
                          if stashes else set())
        for entry in [*stashes, *branches]:
            ref, commit = entry.split("\t", 1)
            if ref == f"refs/heads/{ws['branch_name']}" or commit in source_stashes:
                continue  # HEAD has a separate base/merge proof (including cherry-picks).
            saved = _run_git(root, "for-each-ref", "--count=1", "--format=%(refname)", f"--contains={commit}")
            if saved.returncode or not saved.stdout.strip():
                detail = saved.stderr.strip() or "no source ref contains this commit"
                raise WorkspaceCleanupGuardError(f"Cannot prove clone {ref} ({commit}) is preserved: {detail}")

    def _fetch_workspace_branch_to_ref(
        self,
        repo_root: Path,
        ws: dict[str, Any],
        ws_path: Path,
        ws_branch: str,
        target_ref: str,
    ) -> None:
        if self._uses_git_worktree(ws):
            _run_git(repo_root, "update-ref", target_ref, f"refs/heads/{ws_branch}", check=True)
        else:
            _run_git(
                repo_root,
                "fetch",
                "--no-tags",
                "--force",
                str(ws_path),
                f"{ws_branch}:{target_ref}",
                check=True,
            )

    @staticmethod
    def _registered_worktree(repo_root: Path, ws_path: Path) -> list[str] | None:
        proc = _run_git(repo_root, "worktree", "list", "--porcelain", "-z", check=True)
        expected_path = os.path.normcase(os.path.normpath(ws_path))
        for record in proc.stdout.split("\0\0"):
            first, *fields = record.split("\0")
            if first.startswith("worktree ") and os.path.normcase(os.path.normpath(first[9:])) == expected_path:
                return fields
        return None

    @staticmethod
    def _check_worktree_branch_tip(repo_root: Path, branch_name: str, expected_commit: str) -> bool:
        tip = _run_git(repo_root, "branch", "--format=%(objectname)", "--list", branch_name, check=True).stdout.strip()
        if tip and tip != expected_commit:
            raise WorkspaceCleanupGuardError(
                f"Refusing to remove worktree branch {branch_name!r}: tip {tip} differs from expected commit {expected_commit}"
            )
        return bool(tip)

    def _worktree_cleanup_preflight(self, repo_root: Path, ws_path: Path, branch_name: str, expected_commit: str) -> bool:
        registration = self._registered_worktree(repo_root, ws_path)
        if registration is not None:
            if f"branch refs/heads/{branch_name}" not in registration:
                raise WorkspaceCleanupGuardError(f"Worktree at {ws_path} is not on expected branch {branch_name!r}")
            if any(field == "locked" or field.startswith("locked ") for field in registration):
                raise WorkspaceCleanupGuardError(f"Worktree at {ws_path} is locked")
        elif ws_path.exists() or _is_reparse_or_symlink(ws_path):
            raise WorkspaceCleanupGuardError(f"Directory at {ws_path} is not a registered worktree")
        self._check_worktree_branch_tip(repo_root, branch_name, expected_commit)
        return registration is not None

    def _detach_git_worktree_safe(self, repo_root: Path, ws_path: Path, branch_name: str, expected_commit: str) -> None:
        if self._worktree_cleanup_preflight(repo_root, ws_path, branch_name, expected_commit):
            _run_git(repo_root, "worktree", "remove", "--force", str(ws_path), check=True)
        if self._check_worktree_branch_tip(repo_root, branch_name, expected_commit):
            # Keep Git's protections for checked-out branches and in-progress worktree operations.
            _run_git(repo_root, "branch", "-D", branch_name, check=True)

    def remove_workspace_checkout(self, ws: dict[str, Any]) -> None:
        """Remove the physical checkout, including linked Git resources; caller owns authorization and DB state."""
        ws_path = Path(ws["path"])
        if self._uses_git_worktree(ws):
            self._detach_git_worktree_safe(
                self.workspace_source_root(ws), ws_path, ws["branch_name"], ws["merged_commit"] or ws["base_commit"]
            )
        else:
            _rmtree_safe(ws_path)

    def workspace_create(
        self,
        run_id: str,
        worker_name: str,
        project_root: str | Path | None = None,
        copy_env: bool = True,
        dep_mode: str = "none",
        git_backend: str = "auto",
    ) -> dict[str, Any]:
        """Create an isolated git clone --local (or git worktree / non-git snapshot) workspace and allocate a port."""
        if dep_mode not in VALID_DEP_MODES:
            raise ValueError(
                f"Invalid dep_mode {dep_mode!r}; expected one of {sorted(VALID_DEP_MODES)}"
            )
        if git_backend not in VALID_GIT_BACKENDS:
            raise ValueError(
                f"Invalid git_backend {git_backend!r}; expected one of {sorted(VALID_GIT_BACKENDS)}"
            )
        effective_git_backend = (
            (os.environ.get("TRI_ORCH_GIT_BACKEND", "clone").strip().lower() or "clone")
            if git_backend == "auto"
            else git_backend
        )
        if effective_git_backend not in {"clone", "worktree"}:
            raise ValueError(
                f"Invalid effective git_backend {effective_git_backend!r}; expected 'clone' or 'worktree'"
            )
        if not worker_name or not worker_name.strip():
            raise ValueError("worker_name must be a non-empty string")

        with self._lock:
            source = self._resolve_workspace_source(run_id, project_root)
            base_root = source.root
            if not base_root.is_dir():
                raise FileNotFoundError(f"Source root directory does not exist: {base_root}")
            if not source.placement_root.is_dir():
                raise FileNotFoundError(f"Execution root directory does not exist: {source.placement_root}")
            use_git_clone = source.use_git_clone
            use_linked_worktree = use_git_clone and effective_git_backend == "worktree"
            copy_paths: list[str] = []
            if use_linked_worktree:
                if copy_env:
                    copy_paths.extend(name for name in ENV_FILENAMES if any((root / name).is_file() for root in source.env_roots))
                if dep_mode == "fresh_venv_uv":
                    copy_paths.append(".venv/")
                elif dep_mode in {"link_ro_cache", "copy"}:
                    copy_paths.extend(f"{name}/" for name in DEP_DIRNAMES if (base_root / name).is_dir())
                self._validate_worktree_copy_rules(base_root, copy_paths)

            if source.git_common_dir is not None:
                _append_git_exclude_entries(source.git_common_dir, [EXCLUDE_ENTRY])

            if not use_git_clone:
                triignore_text = _read_triignore(base_root)
                triignore = _parse_triignore(triignore_text)
                estimated_bytes, estimated_files = _estimate_snapshot_size(
                    base_root,
                    copy_env=copy_env,
                    triignore=triignore,
                )
                max_snapshot_mb = int(self.settings.max_snapshot_mb)
                if max_snapshot_mb > 0:
                    limit_bytes = max_snapshot_mb * 1024 * 1024
                    if estimated_bytes > limit_bytes:
                        estimated_mb = estimated_bytes / (1024 * 1024)
                        raise WorkspaceError(
                            f"Snapshot workspace size {estimated_mb:.2f} MB ({estimated_files} files) "
                            f"exceeds max_snapshot_mb={max_snapshot_mb} MB ({limit_bytes} bytes) "
                            f"for source root {base_root}. Suggestions: initialize a Git repository (git init), "
                            "narrow execution_root, configure .triignore exclusions, or adjust "
                            "TRI_ORCH_MAX_SNAPSHOT_MB (0 = unlimited)."
                        )

            ws_dir, branch_name = self._allocate_workspace_dir_and_branch(
                source.placement_root, run_id, worker_name
            )
            created_worktree_commit = None
            try:
                if use_git_clone:
                    if use_linked_worktree:
                        if self._registered_worktree(base_root, ws_dir) is not None:
                            raise WorkspaceGitError(f"Git already has a registered worktree at {ws_dir}")
                        start_commit = _run_git(base_root, "rev-parse", "HEAD", check=True).stdout.strip()
                        _run_git(base_root, "branch", "--no-track", branch_name, start_commit, check=True)
                        created_worktree_commit = start_commit
                        _run_git(base_root, "worktree", "add", str(ws_dir), branch_name, check=True)
                        self._validate_worktree_copy_rules(ws_dir, copy_paths)
                        git_manifest: dict[str, Any] = {
                            "project_root": str(base_root),
                            "git_backend": "worktree",
                        }
                    else:
                        _run_cmd(
                            ["git", "clone", "--local", str(base_root), str(ws_dir)],
                            check=True,
                        )
                        _run_git(ws_dir, "checkout", "-b", branch_name, check=True)
                        self._copy_git_identity(base_root, ws_dir)
                        git_manifest = {"project_root": str(base_root)}
                    base_commit = _run_git(ws_dir, "rev-parse", "HEAD", check=True).stdout.strip()

                    if copy_env:
                        self._copy_git_env_files(source.env_roots, ws_dir)
                    self._apply_dep_mode(base_root, ws_dir, dep_mode, is_git=True)

                    ws_record = self.db.create_workspace(
                        run_id,
                        worker_name,
                        path=ws_dir,
                        mode="git_clone",
                        branch_name=branch_name,
                        base_commit=base_commit,
                        auto_allocate_port=True,
                        snapshot_manifest=git_manifest,
                    )
                else:
                    ws_dir.mkdir(parents=True, exist_ok=True)
                    baseline_files: dict[str, str] = {}
                    copied_bytes = 0
                    file_count = 0

                    for dirpath, dirnames, filenames in os.walk(base_root):
                        parent = Path(dirpath)
                        rel_dir = parent.relative_to(base_root)
                        is_root = rel_dir == Path(".")
                        kept_dirs: list[str] = []
                        for d in sorted(dirnames):
                            if d in SNAPSHOT_EXCLUDE_DIRS or _is_reparse_or_symlink(parent / d):
                                continue
                            rel_d = d if is_root else f"{rel_dir.as_posix()}/{d}"
                            if triignore.matches(rel_d, is_dir=True):
                                continue
                            kept_dirs.append(d)
                        dirnames[:] = kept_dirs
                        (ws_dir / rel_dir).mkdir(parents=True, exist_ok=True)
                        for fname in sorted(filenames):
                            if not copy_env and is_root and fname in ENV_FILENAMES:
                                continue
                            src_file = parent / fname
                            if _is_reparse_or_symlink(src_file):
                                continue
                            rel_posix = fname if is_root else f"{rel_dir.as_posix()}/{fname}"
                            if triignore.matches(rel_posix, is_dir=False):
                                continue
                            dst_file = ws_dir / rel_dir / fname
                            src_size = src_file.lstat().st_size
                            shutil.copy2(src_file, dst_file)
                            copied_bytes += src_size
                            file_count += 1
                            if not (is_root and fname in ENV_FILENAMES):
                                baseline_files[rel_posix] = _sha256_file(src_file)

                    self._apply_dep_mode(base_root, ws_dir, dep_mode, is_git=False)

                    manifest = {
                        "project_root": str(base_root),
                        "copy_env": bool(copy_env),
                        "files": baseline_files,
                        "triignore": triignore_text,
                        "copied_bytes": copied_bytes,
                        "file_count": file_count,
                    }
                    ws_record = self.db.create_workspace(
                        run_id,
                        worker_name,
                        path=ws_dir,
                        mode="snapshot",
                        branch_name=None,
                        base_commit=None,
                        auto_allocate_port=True,
                        snapshot_manifest=manifest,
                    )
                    ws_record["copied_bytes"] = copied_bytes
                    ws_record["file_count"] = file_count
            except BaseException:
                if created_worktree_commit is not None:
                    self._detach_git_worktree_safe(base_root, ws_dir, branch_name, created_worktree_commit)
                elif not use_linked_worktree:
                    _rmtree_safe(ws_dir)
                raise

            return ws_record

    def workspace_status(self, workspace_id: str) -> dict[str, Any]:
        """Inspect current git or snapshot status of a workspace."""
        ws = self._require_workspace(workspace_id)
        ws_path = Path(ws["path"])
        exists = ws_path.exists() and ws_path.is_dir()

        if ws["status"] == "cleaned" or not exists:
            return {
                "workspace_id": workspace_id,
                "run_id": ws["run_id"],
                "worker_name": ws["worker_name"],
                "mode": ws["mode"],
                "status": ws["status"],
                "path": ws["path"],
                "branch_name": ws["branch_name"],
                "base_commit": ws["base_commit"],
                "assigned_port": ws["assigned_port"],
                "exists": exists,
                "dirty": False,
                "has_uncommitted_changes": False,
                "commits_ahead": 0,
                "uncommitted_files": [],
                "committed_files": [],
                "changed_files": [],
                "porcelain": "",
                "porcelain_lines": [],
            }

        if ws["mode"] == "git_clone":
            porcelain_proc = _run_git(
                ws_path, "-c", "core.quotepath=false", "status", "--porcelain", "-uall", check=True
            )
            # Never strip the entire stdout or leading ' M ' status flag on line 1 will break.
            raw_lines = [ln for ln in porcelain_proc.stdout.splitlines() if ln]
            uncommitted_files: list[str] = []
            for line in raw_lines:
                entry = line[3:] if len(line) > 3 else line.strip()
                if " -> " in entry:
                    entry = entry.split(" -> ", 1)[1]
                uncommitted_files.append(entry)

            base_commit = ws["base_commit"]
            commits_ahead = 0
            committed_files: list[str] = []
            if base_commit:
                cnt_proc = _run_git(ws_path, "rev-list", "--count", f"{base_commit}..HEAD")
                if cnt_proc.returncode == 0 and cnt_proc.stdout.strip().isdigit():
                    commits_ahead = int(cnt_proc.stdout.strip())
                cf_proc = _run_git(
                    ws_path,
                    "-c",
                    "core.quotepath=false",
                    "diff",
                    "--name-only",
                    f"{base_commit}..HEAD",
                )
                if cf_proc.returncode == 0:
                    committed_files = [ln for ln in cf_proc.stdout.splitlines() if ln]

            changed_files = sorted(set(committed_files) | set(uncommitted_files))
            dirty = len(uncommitted_files) > 0
            return {
                "workspace_id": workspace_id,
                "run_id": ws["run_id"],
                "worker_name": ws["worker_name"],
                "mode": ws["mode"],
                "status": ws["status"],
                "path": ws["path"],
                "branch_name": ws["branch_name"],
                "base_commit": base_commit,
                "assigned_port": ws["assigned_port"],
                "exists": True,
                "dirty": dirty,
                "has_uncommitted_changes": dirty,
                "commits_ahead": commits_ahead,
                "uncommitted_files": uncommitted_files,
                "committed_files": committed_files,
                "changed_files": changed_files,
                "porcelain": porcelain_proc.stdout,
                "porcelain_lines": raw_lines,
            }

        # Snapshot mode
        manifest = ws.get("snapshot_manifest") or {}
        project_root = self.workspace_source_root(ws)
        triignore = _snapshot_triignore(ws, project_root)
        baseline: dict[str, str] = _filter_snapshot_baseline(
            dict(manifest.get("files") or {}),
            triignore=triignore,
        )
        exclude_env = frozenset(ENV_FILENAMES)
        ws_files = _scan_snapshot_files(ws_path, exclude_root_files=exclude_env, triignore=triignore)
        root_files = _scan_snapshot_files(project_root, exclude_root_files=exclude_env, triignore=triignore)

        added = sorted(p for p in ws_files if p not in baseline)
        deleted = sorted(p for p in baseline if p not in ws_files)
        modified = sorted(p for p in ws_files if p in baseline and ws_files[p] != baseline[p])
        root_changed = sorted(
            p
            for p in (set(baseline) | set(root_files))
            if root_files.get(p) != baseline.get(p)
        )
        changed_files = sorted(set(added) | set(modified) | set(deleted))
        dirty = len(changed_files) > 0
        porcelain_lines = (
            [f"A  {p}" for p in added]
            + [f"M  {p}" for p in modified]
            + [f"D  {p}" for p in deleted]
        )

        return {
            "workspace_id": workspace_id,
            "run_id": ws["run_id"],
            "worker_name": ws["worker_name"],
            "mode": ws["mode"],
            "status": ws["status"],
            "path": ws["path"],
            "branch_name": None,
            "base_commit": None,
            "assigned_port": ws["assigned_port"],
            "exists": True,
            "dirty": dirty,
            "has_uncommitted_changes": dirty,
            "commits_ahead": 1 if (dirty and ws["status"] != "merged") else 0,
            "uncommitted_files": changed_files,
            "committed_files": [],
            "changed_files": changed_files,
            "added_files": added,
            "modified_files": modified,
            "deleted_files": deleted,
            "root_changed_files": root_changed,
            "porcelain": "\n".join(porcelain_lines),
            "porcelain_lines": porcelain_lines,
        }

    def workspace_diff(self, workspace_id: str) -> dict[str, Any]:
        """Return unified diff text and changed file list for a workspace."""
        ws = self._require_workspace(workspace_id)
        ws_path = Path(ws["path"])
        if not ws_path.exists():
            raise FileNotFoundError(f"Workspace directory missing: {ws_path}")

        status_info = self.workspace_status(workspace_id)

        if ws["mode"] == "git_clone":
            base_ref = ws["base_commit"] or "HEAD"
            diff_proc = _run_git(
                ws_path, "-c", "core.quotepath=false", "diff", "--no-color", base_ref, check=True
            )
            ns_proc = _run_git(
                ws_path, "-c", "core.quotepath=false", "diff", "--name-status", base_ref, check=True
            )
            untracked_proc = _run_git(
                ws_path,
                "-c",
                "core.quotepath=false",
                "ls-files",
                "--others",
                "--exclude-standard",
                check=True,
            )

            file_statuses: list[dict[str, str]] = []
            files: list[str] = []
            for line in ns_proc.stdout.splitlines():
                if not line.strip():
                    continue
                parts = line.split("\t")
                code = parts[0].strip()
                fpath = parts[-1].strip()
                files.append(fpath)
                file_statuses.append({"path": fpath, "status": code})

            chunks = [diff_proc.stdout] if diff_proc.stdout else []
            for rel_path in untracked_proc.stdout.splitlines():
                if not rel_path:
                    continue
                full_file = ws_path / rel_path
                if full_file.is_file():
                    if rel_path not in files:
                        files.append(rel_path)
                        file_statuses.append({"path": rel_path, "status": "A"})
                    lines, is_bin = _read_text_lines_or_binary(full_file)
                    chunk = _synthesize_unified_diff(rel_path, None, lines, is_binary=is_bin)
                    if chunk:
                        chunks.append(chunk)

            diff_text = "".join(chunks)
            return {
                "workspace_id": workspace_id,
                "mode": ws["mode"],
                "branch_name": ws["branch_name"],
                "base_commit": ws["base_commit"],
                "files": files,
                "file_statuses": file_statuses,
                "diff": diff_text,
                "commits_ahead": status_info["commits_ahead"],
                "dirty": status_info["dirty"],
                "summary": f"{len(files)} file(s) changed, {status_info['commits_ahead']} commit(s) ahead",
            }

        # Snapshot mode diff
        manifest = ws.get("snapshot_manifest") or {}
        project_root = self.workspace_source_root(ws)
        triignore = _snapshot_triignore(ws, project_root)
        baseline: dict[str, str] = _filter_snapshot_baseline(
            dict(manifest.get("files") or {}),
            triignore=triignore,
        )
        exclude_env = frozenset(ENV_FILENAMES)
        ws_files = _scan_snapshot_files(ws_path, exclude_root_files=exclude_env, triignore=triignore)
        root_files = _scan_snapshot_files(project_root, exclude_root_files=exclude_env, triignore=triignore)

        all_paths = sorted(set(baseline) | set(ws_files))
        files = []
        file_statuses = []
        diff_chunks: list[str] = []

        for rel_path in all_paths:
            b_hash = baseline.get(rel_path)
            w_hash = ws_files.get(rel_path)
            if b_hash == w_hash:
                continue

            if b_hash is None:
                code = "A"
                w_lines, is_bin = _read_text_lines_or_binary(ws_path / rel_path)
                chunk = _synthesize_unified_diff(rel_path, None, w_lines, is_binary=is_bin)
            elif w_hash is None:
                code = "D"
                root_file = project_root / rel_path
                if root_file.is_file():
                    r_lines, is_bin = _read_text_lines_or_binary(root_file)
                else:
                    r_lines, is_bin = [], False
                chunk = _synthesize_unified_diff(rel_path, r_lines, None, is_binary=is_bin)
            else:
                code = "M"
                w_lines, w_bin = _read_text_lines_or_binary(ws_path / rel_path)
                root_file = project_root / rel_path
                if root_file.is_file():
                    r_lines, r_bin = _read_text_lines_or_binary(root_file)
                else:
                    r_lines, r_bin = [], False
                is_bin = w_bin or r_bin
                chunk = _synthesize_unified_diff(rel_path, r_lines, w_lines, is_binary=is_bin)
                if root_files.get(rel_path) != b_hash:
                    chunk = f"# Note: {rel_path} also modified in project root since snapshot\n" + chunk

            files.append(rel_path)
            file_statuses.append({"path": rel_path, "status": code})
            if chunk:
                diff_chunks.append(chunk)

        return {
            "workspace_id": workspace_id,
            "mode": ws["mode"],
            "branch_name": None,
            "base_commit": None,
            "files": files,
            "file_statuses": file_statuses,
            "diff": "".join(diff_chunks),
            "commits_ahead": status_info["commits_ahead"],
            "dirty": status_info["dirty"],
            "summary": f"{len(files)} file(s) changed in snapshot",
        }

    def workspace_merge_preview(
        self,
        workspace_id: str,
        target_branch: str | None = None,
    ) -> dict[str, Any]:
        """Perform non-destructive conflict preview without mutating main repo working tree."""
        ws = self._require_workspace(workspace_id)
        ws_path = Path(ws["path"])
        if not ws_path.exists():
            raise FileNotFoundError(f"Workspace directory missing: {ws_path}")

        if ws["mode"] == "git_clone":
            with self._lock:
                repo_root = self.workspace_source_root(ws)
                resolved_target = target_branch or self._current_branch_or_head(repo_root)
                ws_branch = ws["branch_name"]
                if not ws_branch:
                    raise WorkspaceError(f"Workspace {workspace_id} has no branch_name")

                ws_status = self.workspace_status(workspace_id)
                preview_ref = f"refs/swarm-preview/{_sanitize_segment(workspace_id, uuid.uuid4().hex[:8])}"
                conflict_files: list[str] = []
                files_changed: list[str] = []
                info_messages: list[str] = []

                try:
                    self._fetch_workspace_branch_to_ref(
                        repo_root, ws, ws_path, ws_branch, preview_ref
                    )

                    diff_proc = _run_git(
                        repo_root,
                        "-c",
                        "core.quotepath=false",
                        "diff",
                        "--name-only",
                        f"{resolved_target}...{preview_ref}",
                    )
                    if diff_proc.returncode == 0:
                        files_changed = [ln for ln in diff_proc.stdout.splitlines() if ln]

                    mt_proc = _run_git(
                        repo_root,
                        "merge-tree",
                        "--write-tree",
                        "--name-only",
                        "-z",
                        resolved_target,
                        preview_ref,
                    )
                    if mt_proc.returncode in (0, 1):
                        if mt_proc.returncode == 1:
                            raw = mt_proc.stdout
                            if "\x00\x00" in raw:
                                header_part, msg_part = raw.split("\x00\x00", 1)
                            else:
                                header_part, msg_part = raw, ""
                            tokens = [t for t in header_part.split("\x00") if t]
                            # First token is resulting tree OID; remaining tokens are conflicting paths.
                            if len(tokens) > 1:
                                conflict_files.extend(tokens[1:])
                            if msg_part:
                                info_messages = [
                                    m.strip() for m in msg_part.split("\x00") if m.strip()
                                ]
                    else:
                        # Fallback for older git versions without --write-tree.
                        mb_proc = _run_git(repo_root, "merge-base", resolved_target, preview_ref)
                        if mb_proc.returncode == 0 and mb_proc.stdout.strip():
                            merge_base = mb_proc.stdout.strip()
                            legacy_proc = _run_git(
                                repo_root, "merge-tree", merge_base, resolved_target, preview_ref
                            )
                            if "changed in both" in legacy_proc.stdout or "<<<" in legacy_proc.stdout:
                                for line in legacy_proc.stdout.splitlines():
                                    if line.startswith("  our ") or line.startswith("  their "):
                                        parts = line.split(maxsplit=3)
                                        if len(parts) == 4:
                                            conflict_files.append(parts[3].strip())
                                info_messages.append("Detected conflict via legacy git merge-tree")
                        else:
                            raise WorkspaceGitError(
                                f"git merge-tree failed: {(mt_proc.stderr or mt_proc.stdout).strip()}"
                            )

                    # Also detect uncommitted files in main repo overlapping with files_changed.
                    main_porcelain = _run_git(
                        repo_root, "-c", "core.quotepath=false", "status", "--porcelain", "-uall"
                    )
                    main_dirty_files: set[str] = set()
                    for line in main_porcelain.stdout.splitlines():
                        if not line:
                            continue
                        entry = line[3:] if len(line) > 3 else line.strip()
                        if " -> " in entry:
                            entry = entry.split(" -> ", 1)[1]
                        main_dirty_files.add(entry)
                    dirty_overlaps = sorted(main_dirty_files & set(files_changed))
                    if dirty_overlaps:
                        conflict_files.extend(dirty_overlaps)
                        info_messages.append(
                            f"Main repo has uncommitted edits overlapping: {', '.join(dirty_overlaps)}"
                        )
                finally:
                    _run_git(repo_root, "update-ref", "-d", preview_ref)

                dedup_conflicts = sorted(set(conflict_files))
                has_conflicts = len(dedup_conflicts) > 0
                summary_parts: list[str] = []
                if has_conflicts:
                    summary_parts.append(
                        f"CONFLICT: {len(dedup_conflicts)} conflicting file(s): {', '.join(dedup_conflicts)}"
                    )
                else:
                    summary_parts.append(
                        f"Clean merge preview onto {resolved_target} ({len(files_changed)} file(s) changed)"
                    )
                if ws_status["dirty"]:
                    summary_parts.append(
                        f"Warning: workspace has {len(ws_status['uncommitted_files'])} uncommitted file(s) not on branch"
                    )

                return {
                    "workspace_id": workspace_id,
                    "mode": ws["mode"],
                    "target_branch": resolved_target,
                    "has_conflicts": has_conflicts,
                    "conflict_files": dedup_conflicts,
                    "files_changed": files_changed,
                    "dirty_workspace": ws_status["dirty"],
                    "dirty_main_repo": bool(main_dirty_files),
                    "messages": info_messages,
                    "summary": "; ".join(summary_parts),
                }

        # Snapshot mode 3-way preview
        manifest = ws.get("snapshot_manifest") or {}
        project_root = self.workspace_source_root(ws)
        triignore = _snapshot_triignore(ws, project_root)
        baseline: dict[str, str] = _filter_snapshot_baseline(
            dict(manifest.get("files") or {}),
            triignore=triignore,
        )
        exclude_env = frozenset(ENV_FILENAMES)
        ws_files = _scan_snapshot_files(ws_path, exclude_root_files=exclude_env, triignore=triignore)
        root_files = _scan_snapshot_files(project_root, exclude_root_files=exclude_env, triignore=triignore)

        all_paths = sorted(set(baseline) | set(ws_files) | set(root_files))
        files_changed: list[str] = []
        conflict_files: list[str] = []

        for rel_path in all_paths:
            b = baseline.get(rel_path)
            w = ws_files.get(rel_path)
            r = root_files.get(rel_path)
            if w == b:
                continue
            files_changed.append(rel_path)
            if r != b and w != r:
                conflict_files.append(rel_path)

        has_conflicts = len(conflict_files) > 0
        summary = (
            f"CONFLICT: {len(conflict_files)} overlapping file(s): {', '.join(conflict_files)}"
            if has_conflicts
            else f"Clean snapshot merge preview ({len(files_changed)} file(s) changed)"
        )
        return {
            "workspace_id": workspace_id,
            "mode": ws["mode"],
            "target_branch": None,
            "has_conflicts": has_conflicts,
            "conflict_files": conflict_files,
            "files_changed": files_changed,
            "dirty_workspace": bool(files_changed),
            "dirty_main_repo": False,
            "messages": [],
            "summary": summary,
        }

    def workspace_merge(
        self, workspace_id: str, target_branch: str | None = None,
        strategy: str = "merge", reconcile: bool = False,
    ) -> dict[str, Any]:
        """Gate every new-workflow merge at the shared mutation boundary."""
        if type(reconcile) is not bool:
            raise ValueError("reconcile must be a boolean")
        if strategy not in VALID_MERGE_STRATEGIES:
            raise ValueError(f"Invalid merge strategy: {strategy!r}")
        ws = self._require_workspace(workspace_id)
        root = self.workspace_source_root(ws)
        # Shared across plugin managers; external editors are detected, not locked.
        with self._workspace_mutation_lock(ws, root):
            run = self.db.get_run(ws["run_id"])
            if run is None:
                raise NotFoundError(f"Run not found: {ws['run_id']}")
            if run["workflow_version"] == 0:
                if reconcile:
                    raise WorkspaceConflictError("Legacy workspace has no merge checkpoint")
                return self._workspace_merge_legacy(workspace_id, target_branch, strategy)
            from orchestrator.workflow import WorkflowService
            service = WorkflowService(self.db)
            step = service.find_step_for_workspace(workspace_id)
            if reconcile:
                return self._reconcile_merge(service, step, ws, root)
            if step["state"] in {"integrating", "reconciliation_required"}:
                raise WorkspaceConflictError("Uncertain merge requires explicit reconcile=True")
            if not Path(ws["path"]).is_dir():
                raise FileNotFoundError(f"Workspace directory missing: {ws['path']}")
            if ws["mode"] == "git_clone":
                return self._merge_git_gated(service, ws, root, target_branch, strategy)
            return self._merge_snapshot_gated(service, ws, root, strategy)

    @staticmethod
    def _merge_file(root: Path, relative: str) -> Path:
        path = root / relative
        if Path(relative).is_absolute() or ".." in Path(relative).parts:
            raise WorkspaceConflictError(f"Invalid merge path: {relative!r}")
        current = path
        while current != root:
            if _is_reparse_or_symlink(current):
                raise WorkspaceConflictError(f"Merge path became a filesystem alias: {current}")
            current = current.parent
        if not path.resolve().is_relative_to(root.resolve()):
            raise WorkspaceConflictError(f"Merge path escapes project: {relative}")
        return path

    @staticmethod
    def _replace_merge_file(source: Path, target: Path) -> None:
        """Atomic per-file replacement; a persistent checkpoint covers the whole apply."""
        target.parent.mkdir(parents=True, exist_ok=True)
        temp = target.with_name(f".{target.name}.tri-merge-{uuid.uuid4().hex}.tmp")
        try:
            with source.open("rb") as src, temp.open("xb") as dst:
                shutil.copyfileobj(src, dst)
                dst.flush()
                os.fsync(dst.fileno())
            shutil.copystat(source, temp)
            os.replace(temp, target)
        finally:
            if temp.exists():
                temp.unlink()

    def _merge_snapshot_gated(self, service: Any, ws: dict[str, Any], root: Path,
                              strategy: str) -> dict[str, Any]:
        source = Path(ws["path"])
        manifest = ws.get("snapshot_manifest") or {}
        triignore = _snapshot_triignore(ws, root)
        baseline = _filter_snapshot_baseline(dict(manifest.get("files") or {}), triignore=triignore)
        excluded = frozenset(ENV_FILENAMES)
        approved = _scan_snapshot_files(source, exclude_root_files=excluded, triignore=triignore)
        before = _scan_snapshot_files(root, exclude_root_files=excluded, triignore=triignore)
        step = service.require_candidate(ws["id"], manifest=approved)
        changes = sorted(p for p in set(baseline) | set(approved)
                         if baseline.get(p) != approved.get(p))
        conflicts = [p for p in changes if before.get(p) != baseline.get(p)
                     and before.get(p) != approved.get(p)]
        if conflicts:
            raise WorkspaceConflictError(f"Snapshot merge conflicts: {conflicts}")
        desired = dict(before)
        for rel in changes:
            if rel in approved:
                desired[rel] = approved[rel]
            else:
                desired.pop(rel, None)
        merge_id = "merge_" + uuid.uuid4().hex
        staging_parent = self.settings.data_dir / "merge-staging"
        staging = staging_parent / merge_id
        begun_merge = False
        merge_finished = False
        try:
            (staging / "source").mkdir(parents=True, exist_ok=False)
            (staging / "before").mkdir()
            for rel in changes:
                if rel in approved:
                    staged = staging / "source" / rel
                    staged.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(self._merge_file(source, rel), staged)
                    if _sha256_file(staged) != approved[rel]:
                        raise WorkspaceConflictError(f"Candidate changed while staging: {rel}")
                if rel in before:
                    backup = staging / "before" / rel
                    backup.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(self._merge_file(root, rel), backup)
                    if _sha256_file(backup) != before[rel]:
                        raise WorkspaceConflictError(f"Target changed while staging: {rel}")
            checkpoint = {"merge_id": merge_id, "workspace_id": ws["id"], "mode": "snapshot",
                          "project_root": str(root), "source_manifest": approved,
                          "before_manifest": before, "desired_manifest": desired,
                          "operations": changes, "staging_dir": str(staging), "strategy": strategy}
            with self.db.transaction():
                if "triignore" not in manifest:
                    # The merge may overwrite the last copy of the original rules in a legacy snapshot.
                    self.db.update_workspace(ws["id"], snapshot_manifest={**manifest, "triignore": triignore.text})
                service.begin_merge(step["id"], checkpoint)
            begun_merge = True
            for rel in changes:
                target = self._merge_file(root, rel)
                observed = _sha256_file(target) if target.is_file() else None
                if observed != before.get(rel):
                    raise WorkspaceConflictError(f"Target changed before apply: {rel}")
                if rel in approved:
                    staged = staging / "source" / rel
                    if _sha256_file(staged) != approved[rel]:
                        raise WorkspaceConflictError(f"Staged candidate changed: {rel}")
                    self._replace_merge_file(staged, target)
                elif target.exists():
                    target.unlink()
            if _scan_snapshot_files(root, exclude_root_files=excluded, triignore=triignore) != desired:
                raise WorkspaceConflictError("Snapshot target differs from approved merge result")
            proof = {"merge_id": merge_id, "mode": "snapshot", "project_root": str(root),
                     "source_manifest": approved, "target_manifest": desired}
            with self.db.transaction():
                self.db.update_workspace(ws["id"], status="merged", snapshot_manifest={
                    **ws["snapshot_manifest"], "files": approved, "triignore": triignore.text})
                service.finish_merge(step["id"], proof)
            merge_finished = True
            return {"workspace_id": ws["id"], "mode": "snapshot", "merged": True,
                    "has_conflicts": False, "status": "merged", "target_branch": None,
                    "strategy": strategy, "commits_merged": [], "files_changed": changes,
                    "conflict_files": [], "merge_id": merge_id,
                    "message": f"Applied and read back {len(changes)} approved snapshot changes"}
        except BaseException as exc:
            if begun_merge:
                service.mark_merge_uncertain(step["id"], f"{type(exc).__name__}: {exc}")
            raise
        finally:
            if merge_finished or not begun_merge:
                _rmtree_safe(staging)
                if staging_parent.is_dir():
                    try:
                        staging_parent.rmdir()
                    except OSError:
                        pass

    def _merge_git_gated(self, service: Any, ws: dict[str, Any], root: Path,
                         target_branch: str | None, strategy: str) -> dict[str, Any]:
        source = Path(ws["path"])
        if self.workspace_status(ws["id"])["dirty"]:
            raise WorkspaceConflictError("Candidate workspace has uncommitted changes")
        if self._current_branch_or_head(source) != ws["branch_name"]:
            raise WorkspaceConflictError("Candidate workspace branch changed")
        if _run_git(root, "status", "--porcelain", check=True).stdout.strip():
            raise WorkspaceConflictError("Target repository has uncommitted changes")
        branch = self._current_branch_or_head(root)
        target = target_branch or branch
        ref = "refs/swarm-merge/" + uuid.uuid4().hex
        try:
            self._fetch_workspace_branch_to_ref(root, ws, source, ws["branch_name"], ref)
            commit = _run_git(root, "rev-parse", ref, check=True).stdout.strip()
            step = service.require_candidate(ws["id"], source_commit=commit)
            before = _run_git(root, "rev-parse", target, check=True).stdout.strip()
            preview = _run_git(root, "merge-tree", "--write-tree", before, ref)
            if preview.returncode != 0:
                raise WorkspaceConflictError(f"Git merge preview failed: {preview.stdout} {preview.stderr}")
            desired_tree = preview.stdout.splitlines()[0].strip()
            commits = _run_git(root, "rev-list", "--reverse", f"{before}..{ref}", check=True).stdout.splitlines()
            if strategy == "cherry-pick" and _run_git(root, "rev-list", "--merges", f"{before}..{ref}", check=True).stdout.strip():
                raise WorkspaceConflictError("Cherry-pick requires no source-only merge commits")
            merge_id = "merge_" + uuid.uuid4().hex
            checkpoint = {"merge_id": merge_id, "workspace_id": ws["id"], "mode": "git_clone",
                          "project_root": str(root), "source_commit": commit,
                          "target_before_commit": before, "target_branch": target,
                          "desired_tree": desired_tree, "strategy": strategy}
            service.begin_merge(step["id"], checkpoint)
            try:
                if self._current_branch_or_head(root) != branch or _run_git(root, "rev-parse", target, check=True).stdout.strip() != before:
                    raise WorkspaceConflictError("Git target moved after merge preparation")
                if target != branch and target != "HEAD":
                    _run_git(root, "checkout", target, check=True)
                if commits:
                    args = ("merge", "-m", f"Merge approved workspace {ws['worker_name']}", ref) if strategy == "merge" else ("cherry-pick", *commits)
                    proc = _run_git(root, *self._git_identity_args(root), *args)
                    if proc.returncode:
                        abort = _run_git(root, "merge" if strategy == "merge" else "cherry-pick", "--abort")
                        raise WorkspaceConflictError(f"Git {strategy} failed: {proc.stderr or proc.stdout}; abort={abort.returncode}")
                actual_tree = _run_git(root, "rev-parse", "HEAD^{tree}", check=True).stdout.strip()
                if actual_tree != desired_tree or _run_git(root, "status", "--porcelain", check=True).stdout.strip():
                    raise WorkspaceConflictError("Git target differs from approved merge tree")
                target_commit = _run_git(root, "rev-parse", "HEAD", check=True).stdout.strip()
                proof = {"merge_id": merge_id, "mode": "git_clone", "project_root": str(root),
                         "source_commit": commit, "target_commit": target_commit, "target_tree": actual_tree}
                with self.db.transaction():
                    self.db.update_workspace(ws["id"], status="merged", merged_commit=commit,
                                             merged_target_commit=target_commit)
                    service.finish_merge(step["id"], proof)
                return {"workspace_id": ws["id"], "mode": "git_clone", "merged": True,
                        "has_conflicts": False, "status": "merged", "target_branch": target,
                        "strategy": strategy, "commits_merged": commits,
                        "files_changed": _run_git(
                            root, "-c", "core.quotepath=false", "diff", "--name-only", before, "HEAD", check=True
                        ).stdout.splitlines(),
                        "conflict_files": [], "merge_id": merge_id,
                        "message": "Merged and read back the approved candidate"}
            except BaseException as exc:
                service.mark_merge_uncertain(step["id"], f"{type(exc).__name__}: {exc}")
                raise
        finally:
            _run_git(root, "update-ref", "-d", ref)

    def _reconcile_merge(self, service: Any, step: dict[str, Any], ws: dict[str, Any],
                         root: Path) -> dict[str, Any]:
        if step["state"] not in {"integrating", "reconciliation_required"}:
            raise WorkspaceConflictError("No uncertain merge checkpoint to reconcile")
        checkpoint = step["checkpoint"].get("merge")
        if not checkpoint or checkpoint["workspace_id"] != ws["id"] or Path(checkpoint["project_root"]).resolve() != root.resolve():
            raise WorkspaceConflictError("Merge checkpoint identity mismatch")
        proof = {"merge_id": checkpoint["merge_id"], "mode": ws["mode"], "project_root": str(root)}
        if ws["mode"] == "snapshot":
            triignore = _snapshot_triignore(ws, root)
            current = _scan_snapshot_files(root, exclude_root_files=ENV_FILENAMES, triignore=triignore)
            if current != checkpoint["desired_manifest"]:
                state = "not_applied" if current == checkpoint["before_manifest"] else "partially_applied_or_changed"
                raise WorkspaceConflictError(f"Merge reconciliation needs a decision: {state}; no files replayed")
            proof.update(source_manifest=checkpoint["source_manifest"], target_manifest=current)
            updates = {"snapshot_manifest": {**ws["snapshot_manifest"], "files": checkpoint["source_manifest"],
                                             "triignore": triignore.text}}
        else:
            if self._current_branch_or_head(root) != checkpoint["target_branch"] and checkpoint["target_branch"] != "HEAD":
                raise WorkspaceConflictError("Git target branch differs from merge checkpoint")
            if _run_git(root, "status", "--porcelain", check=True).stdout.strip():
                raise WorkspaceConflictError("Git target is dirty; reconciliation cannot certify merge")
            tree = _run_git(root, "rev-parse", "HEAD^{tree}", check=True).stdout.strip()
            if tree != checkpoint["desired_tree"]:
                raise WorkspaceConflictError("Git target differs from desired merge tree; no commits replayed")
            head = _run_git(root, "rev-parse", "HEAD", check=True).stdout.strip()
            if _run_git(root, "merge-base", "--is-ancestor", checkpoint["target_before_commit"], head).returncode:
                raise WorkspaceConflictError("Git target no longer descends from merge checkpoint")
            proof.update(source_commit=checkpoint["source_commit"], target_commit=head, target_tree=tree)
            updates = {"merged_commit": checkpoint["source_commit"], "merged_target_commit": head}
        with self.db.transaction():
            self.db.update_workspace(ws["id"], status="merged", **updates)
            service.finish_merge(step["id"], proof)
        if ws["mode"] == "snapshot" and checkpoint.get("staging_dir"):
            reconciled_staging = Path(checkpoint["staging_dir"])
            reconciled_parent = reconciled_staging.parent
            _rmtree_safe(reconciled_staging)
            if reconciled_parent.is_dir():
                try:
                    reconciled_parent.rmdir()
                except OSError:
                    pass
        return {"workspace_id": ws["id"], "mode": ws["mode"], "merged": True,
                "has_conflicts": False, "status": "merged", "reconciled": True,
                "merge_id": checkpoint["merge_id"], "message": "Existing target verified; no files or commits replayed"}

    def _workspace_merge_legacy(
        self,
        workspace_id: str,
        target_branch: str | None = None,
        strategy: str = "merge",
    ) -> dict[str, Any]:
        """Merge or cherry-pick a workspace cleanly back into the main repo, aborting on conflict."""
        if strategy not in VALID_MERGE_STRATEGIES:
            raise ValueError(
                f"Invalid merge strategy {strategy!r}; expected one of {sorted(VALID_MERGE_STRATEGIES)}"
            )

        ws = self._require_workspace(workspace_id)
        ws_path = Path(ws["path"])
        if not ws_path.exists():
            raise FileNotFoundError(f"Workspace directory missing: {ws_path}")

        if ws["mode"] == "git_clone":
            with self._lock:
                ws_status = self.workspace_status(workspace_id)
                if ws_status["dirty"]:
                    raise WorkspaceConflictError(
                        f"Workspace {workspace_id} has uncommitted changes; commit or discard before merging"
                    )

                repo_root = self.workspace_source_root(ws)
                ws_branch = ws["branch_name"]
                if not ws_branch:
                    raise WorkspaceError(f"Workspace {workspace_id} has no branch_name")

                actual_branch = self._current_branch_or_head(ws_path)
                if actual_branch != ws_branch:
                    raise WorkspaceConflictError(
                        f"Workspace {workspace_id} is on {actual_branch!r}; expected branch {ws_branch!r}"
                    )

                current_branch = self._current_branch_or_head(repo_root)
                resolved_target = target_branch or current_branch

                if resolved_target != current_branch and resolved_target != "HEAD":
                    dirty_main = _run_git(repo_root, "status", "--porcelain")
                    if dirty_main.stdout.strip():
                        raise WorkspaceConflictError(
                            f"Main repository has uncommitted changes; cannot switch to {resolved_target}"
                        )
                    _run_git(repo_root, "checkout", resolved_target, check=True)

                merge_ref = f"refs/swarm-merge/{_sanitize_segment(workspace_id, uuid.uuid4().hex[:8])}"
                identity_args = self._git_identity_args(repo_root)
                commits_to_apply: list[str] = []
                files_changed: list[str] = []

                try:
                    self._fetch_workspace_branch_to_ref(
                        repo_root, ws, ws_path, ws_branch, merge_ref
                    )
                    # Proof refers to the fetched source, never a later mutable workspace HEAD.
                    source_commit = _run_git(repo_root, "rev-parse", merge_ref, check=True).stdout.strip()
                    diff_proc = _run_git(
                        repo_root,
                        "-c",
                        "core.quotepath=false",
                        "diff",
                        "--name-only",
                        f"{resolved_target}...{merge_ref}",
                    )
                    if diff_proc.returncode == 0:
                        files_changed = [ln for ln in diff_proc.stdout.splitlines() if ln]

                    rev_args = ["--reverse", f"{resolved_target}..{merge_ref}"]
                    if strategy == "cherry-pick":
                        merges = _run_git(
                            repo_root, "rev-list", "--merges", f"{resolved_target}..{merge_ref}", check=True
                        )
                        if merges.stdout.strip():
                            raise WorkspaceConflictError(
                                "Cherry-pick cannot preserve source-only merge commits; use strategy='merge'"
                            )
                        if ws["merged_commit"] and ws["merged_target_commit"]:
                            # Only skip history certified by a prior merge into this target lineage.
                            source_diverged = _run_git(
                                repo_root, "rev-list", "-1", ws["merged_commit"], "--not", merge_ref, check=True
                            ).stdout.strip()
                            target_diverged = _run_git(
                                repo_root, "rev-list", "-1", ws["merged_target_commit"], "--not", resolved_target, check=True
                            ).stdout.strip()
                            if not source_diverged and not target_diverged:
                                rev_args.append(f"^{ws['merged_commit']}")
                    rev_proc = _run_git(repo_root, "rev-list", *rev_args, check=True)
                    commits_to_apply = [ln.strip() for ln in rev_proc.stdout.splitlines() if ln.strip()]

                    if not commits_to_apply:
                        self.db.update_workspace(
                            workspace_id, status="merged", merged_commit=source_commit,
                            merged_target_commit=_run_git(repo_root, "rev-parse", "HEAD", check=True).stdout.strip(),
                        )
                        return {
                            "workspace_id": workspace_id,
                            "mode": ws["mode"],
                            "merged": True,
                            "has_conflicts": False,
                            "status": "merged",
                            "target_branch": resolved_target,
                            "strategy": strategy,
                            "commits_merged": [],
                            "files_changed": files_changed,
                            "conflict_files": [],
                            "message": f"Already up to date on {resolved_target}",
                        }

                    if strategy == "cherry-pick" and len(commits_to_apply) > 1:
                        preflight = _run_git(
                            repo_root,
                            "merge-tree",
                            "--write-tree",
                            "--name-only",
                            "-z",
                            resolved_target,
                            merge_ref,
                        )
                        if preflight.returncode == 1:
                            header_part = preflight.stdout.split("\x00\x00", 1)[0]
                            tokens = [t for t in header_part.split("\x00") if t]
                            conflict_files = sorted(set(tokens[1:])) if len(tokens) > 1 else files_changed
                            self.db.update_workspace(workspace_id, status="conflict")
                            return {
                                "workspace_id": workspace_id,
                                "mode": ws["mode"],
                                "merged": False,
                                "has_conflicts": True,
                                "status": "conflict",
                                "target_branch": resolved_target,
                                "strategy": strategy,
                                "commits_merged": [],
                                "files_changed": files_changed,
                                "conflict_files": conflict_files,
                                "message": "cherry-pick aborted before applying commits due to conflict",
                            }

                    if strategy == "merge":
                        op_proc = _run_git(
                            repo_root,
                            *identity_args,
                            "merge",
                            "-m",
                            f"Merge workspace {ws['worker_name']} ({ws_branch})",
                            merge_ref,
                        )
                    else:
                        op_proc = _run_git(
                            repo_root,
                            *identity_args,
                            "cherry-pick",
                            *commits_to_apply,
                        )

                    if op_proc.returncode != 0:
                        unmerged_proc = _run_git(
                            repo_root,
                            "-c",
                            "core.quotepath=false",
                            "diff",
                            "--name-only",
                            "--diff-filter=U",
                        )
                        conflict_files = [
                            ln for ln in unmerged_proc.stdout.splitlines() if ln
                        ]
                        abort_cmd = "merge" if strategy == "merge" else "cherry-pick"
                        _run_git(repo_root, abort_cmd, "--abort")
                        if not conflict_files:
                            # Fallback if aborted due to overlapping untracked/dirty local file.
                            conflict_files = files_changed
                        self.db.update_workspace(workspace_id, status="conflict")
                        err_detail = (op_proc.stderr or op_proc.stdout or "").strip()
                        return {
                            "workspace_id": workspace_id,
                            "mode": ws["mode"],
                            "merged": False,
                            "has_conflicts": True,
                            "status": "conflict",
                            "target_branch": resolved_target,
                            "strategy": strategy,
                            "commits_merged": [],
                            "files_changed": files_changed,
                            "conflict_files": sorted(set(conflict_files)),
                            "message": f"{strategy} aborted due to conflict: {err_detail}",
                        }

                    self.db.update_workspace(
                        workspace_id, status="merged", merged_commit=source_commit,
                        merged_target_commit=_run_git(repo_root, "rev-parse", "HEAD", check=True).stdout.strip(),
                    )
                    return {
                        "workspace_id": workspace_id,
                        "mode": ws["mode"],
                        "merged": True,
                        "has_conflicts": False,
                        "status": "merged",
                        "target_branch": resolved_target,
                        "strategy": strategy,
                        "commits_merged": commits_to_apply,
                        "files_changed": files_changed,
                        "conflict_files": [],
                        "message": f"Merged {len(commits_to_apply)} commit(s) into {resolved_target}",
                    }
                finally:
                    _run_git(repo_root, "update-ref", "-d", merge_ref)

        # Snapshot mode 3-way merge
        with self._lock:
            manifest = ws.get("snapshot_manifest") or {}
            project_root = self.workspace_source_root(ws)
            triignore = _snapshot_triignore(ws, project_root)
            baseline: dict[str, str] = _filter_snapshot_baseline(
                dict(manifest.get("files") or {}),
                triignore=triignore,
            )
            exclude_env = frozenset(ENV_FILENAMES)
            ws_files = _scan_snapshot_files(ws_path, exclude_root_files=exclude_env, triignore=triignore)
            root_files = _scan_snapshot_files(project_root, exclude_root_files=exclude_env, triignore=triignore)

            all_paths = sorted(set(baseline) | set(ws_files) | set(root_files))
            files_changed: list[str] = []
            conflict_files: list[str] = []
            ops: list[tuple[str, str]] = []

            for rel_path in all_paths:
                b = baseline.get(rel_path)
                w = ws_files.get(rel_path)
                r = root_files.get(rel_path)
                if w == b:
                    continue
                files_changed.append(rel_path)
                if r != b and w != r:
                    conflict_files.append(rel_path)
                elif w is None:
                    ops.append(("delete", rel_path))
                else:
                    ops.append(("write", rel_path))

            if conflict_files:
                self.db.update_workspace(workspace_id, status="conflict")
                return {
                    "workspace_id": workspace_id,
                    "mode": ws["mode"],
                    "merged": False,
                    "has_conflicts": True,
                    "status": "conflict",
                    "target_branch": None,
                    "strategy": strategy,
                    "commits_merged": [],
                    "files_changed": files_changed,
                    "conflict_files": conflict_files,
                    "message": f"Snapshot merge aborted due to conflict on: {', '.join(conflict_files)}",
                }

            for kind, rel_path in ops:
                target_file = project_root / rel_path
                if kind == "delete":
                    if target_file.exists():
                        target_file.unlink()
                else:
                    target_file.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(ws_path / rel_path, target_file)

            new_manifest = {
                **manifest,
                "project_root": str(project_root),
                "copy_env": bool(manifest.get("copy_env", True)),
                "files": ws_files,
                "triignore": triignore.text,
            }
            self.db.update_workspace(
                workspace_id,
                status="merged",
                snapshot_manifest=new_manifest,
            )
            return {
                "workspace_id": workspace_id,
                "mode": ws["mode"],
                "merged": True,
                "has_conflicts": False,
                "status": "merged",
                "target_branch": None,
                "strategy": strategy,
                "commits_merged": [],
                "files_changed": files_changed,
                "conflict_files": [],
                "message": f"Applied {len(ops)} snapshot file change(s) cleanly",
            }

    def workspace_cleanup(
        self, workspace_id: str, force: bool = False,
    ) -> dict[str, Any]:
        ws = self._require_workspace(workspace_id)
        with self._workspace_mutation_lock(ws, self.workspace_source_root(ws)):
            return self._workspace_cleanup_locked(workspace_id, force)

    def _workspace_cleanup_locked(
        self,
        workspace_id: str,
        force: bool = False,
    ) -> dict[str, Any]:
        """Remove only clean merged workspaces, with persisted source-commit proof for Git clones."""
        del force  # Strict safety guard: never allow bypassing unmerged or dirty checks.
        with self._lock:
            ws = self._require_workspace(workspace_id)
            ws_path = Path(ws["path"])

            if ws["status"] == "cleaned" and not ws_path.exists():
                return {
                    "workspace_id": workspace_id,
                    "cleaned": True,
                    "already_cleaned": True,
                    "status": "cleaned",
                    "path": ws["path"],
                }

            if ws["status"] != "merged":
                raise WorkspaceCleanupGuardError(
                    f"Refusing cleanup for workspace {workspace_id}: status is {ws['status']!r}, must be 'merged'"
                )

            run = self.db.get_run(ws["run_id"])
            if run is None:
                raise NotFoundError(f"Run not found: {ws['run_id']}")
            if run["workflow_version"] != 0:
                from orchestrator.workflow import WorkflowService
                WorkflowService(self.db).require_integrated(workspace_id)

            is_empty_git_shell = (
                ws["mode"] == "git_clone"
                and ws_path.is_dir()
                and not (ws_path / ".git").exists()
                and not any(ws_path.iterdir())
            )
            if not is_empty_git_shell:
                status_info = self.workspace_status(workspace_id)
                if status_info["dirty"] or status_info["has_uncommitted_changes"]:
                    raise WorkspaceCleanupGuardError(
                        f"Refusing cleanup for workspace {workspace_id}: workspace has uncommitted changes ({status_info['uncommitted_files']})"
                    )

            if ws["mode"] == "git_clone":
                if not ws["merged_commit"] or not ws["merged_target_commit"]:
                    raise WorkspaceCleanupGuardError(
                        f"Refusing cleanup for workspace {workspace_id}: missing merged commit proof; merge again"
                    )
                if ws_path.exists():
                    if (ws_path / ".git").exists():
                        actual_branch = self._current_branch_or_head(ws_path)
                        if actual_branch != ws["branch_name"]:
                            raise WorkspaceCleanupGuardError(
                                f"Refusing cleanup for workspace {workspace_id}: on {actual_branch!r}, "
                                f"expected branch {ws['branch_name']!r}"
                            )
                        current_head = _run_git(ws_path, "rev-parse", "HEAD", check=True).stdout.strip()
                        if current_head != ws["merged_commit"]:
                            raise WorkspaceCleanupGuardError(
                                f"Refusing cleanup for workspace {workspace_id}: HEAD {current_head} "
                                f"differs from merged commit {ws['merged_commit']}; merge again"
                            )
                        self._require_preserved_clone_refs(ws)
                    elif any(ws_path.iterdir()):
                        raise WorkspaceCleanupGuardError(
                            f"Refusing cleanup for workspace {workspace_id}: missing .git metadata in non-empty directory"
                        )

            self.remove_workspace_checkout(ws)
            parent = ws_path.parent
            if parent.name == WORKSPACES_DIRNAME and parent.exists():
                try:
                    parent.rmdir()
                except OSError:
                    pass

            self.db.update_workspace(workspace_id, status="cleaned")
            return {
                "workspace_id": workspace_id,
                "cleaned": True,
                "already_cleaned": False,
                "status": "cleaned",
                "path": ws["path"],
            }

    def workspace_gc_safety(self, workspace_id: str) -> dict[str, Any]:
        """Statically inspect whether a non-active workspace is safe for GC reclamation.

        Enforces the same static safety guards as ``workspace_cleanup`` (merged status,
        +A:0 / ~M:0 / -D:0, and Git merged-commit proof + branch/HEAD invariants)
        without bypassing ``WorkspaceCleanupGuardError`` boundaries.
        """
        return self._workspace_gc_safety(self._require_workspace(workspace_id), self.workspace_status)

    def workspace_storage_checks(
        self, ws: dict[str, Any], *, gc_details: bool = False,
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        """Read-only verdicts for one storage inventory row, never used as removal authority."""
        if ws["status"] == "merged":
            return self._workspace_gc_safety(ws, self.workspace_status), None
        # GC and explicit discard have different guard reasons but inspect the same checkout.
        read_status = cache(self.workspace_status)
        discard = self._workspace_discard_check(ws, read_status)
        if ws["mode"] == "snapshot" and "added_count" in discard and not gc_details:
            return None, discard
        return self._workspace_gc_safety(ws, read_status), discard

    def _workspace_gc_safety(
        self, ws: dict[str, Any], read_status: Callable[[str], dict[str, Any]],
    ) -> dict[str, Any]:
        workspace_id = ws["id"]
        ws_path = Path(ws["path"])
        is_reparse = _is_reparse_or_symlink(ws_path)
        exists = ws_path.exists() or is_reparse

        if ws["status"] == "cleaned":
            if not exists:
                return {
                    "workspace_id": workspace_id,
                    "mode": ws["mode"],
                    "status": ws["status"],
                    "exists": False,
                    "safe_to_clean": True,
                    "reason": "Already cleaned",
                    "added_count": 0,
                    "modified_count": 0,
                    "deleted_count": 0,
                    "added_files": [],
                    "modified_files": [],
                    "deleted_files": [],
                }
            return {
                "workspace_id": workspace_id,
                "mode": ws["mode"],
                "status": ws["status"],
                "exists": True,
                "safe_to_clean": False,
                "reason": "Workspace status is 'cleaned' in DB, but directory still exists on disk",
                "added_count": 0,
                "modified_count": 0,
                "deleted_count": 0,
                "added_files": [],
                "modified_files": [],
                "deleted_files": [],
            }

        unsafe_reason = None
        if is_reparse:
            unsafe_reason = "Workspace path is a symlink or junction; refusing automatic cleanup"
        elif ws["status"] == "merged" and ws["merged_commit"]:
            try:
                if self._uses_git_worktree(ws):
                    self._worktree_cleanup_preflight(
                        self.workspace_source_root(ws), ws_path, ws["branch_name"], ws["merged_commit"]
                    )
            except WorkspaceCleanupGuardError as exc:
                unsafe_reason = str(exc)

        if unsafe_reason is not None:
            return {
                "workspace_id": workspace_id,
                "mode": ws["mode"],
                "status": ws["status"],
                "exists": exists,
                "safe_to_clean": False,
                "reason": unsafe_reason,
                "added_count": 0,
                "modified_count": 0,
                "deleted_count": 0,
                "added_files": [],
                "modified_files": [],
                "deleted_files": [],
            }

        if not exists:
            if ws["status"] != "merged":
                return {
                    "workspace_id": workspace_id,
                    "mode": ws["mode"],
                    "status": ws["status"],
                    "exists": False,
                    "safe_to_clean": False,
                    "reason": f"Workspace directory missing on disk and status is {ws['status']!r}, not 'merged'",
                    "added_count": 0,
                    "modified_count": 0,
                    "deleted_count": 0,
                    "added_files": [],
                    "modified_files": [],
                    "deleted_files": [],
                }
            if ws["mode"] == "git_clone" and (not ws["merged_commit"] or not ws["merged_target_commit"]):
                return {
                    "workspace_id": workspace_id,
                    "mode": ws["mode"],
                    "status": ws["status"],
                    "exists": False,
                    "safe_to_clean": False,
                    "reason": "Missing merged commit proof; merge again",
                    "added_count": 0,
                    "modified_count": 0,
                    "deleted_count": 0,
                    "added_files": [],
                    "modified_files": [],
                    "deleted_files": [],
                }
            return {
                "workspace_id": workspace_id,
                "mode": ws["mode"],
                "status": ws["status"],
                "exists": False,
                "safe_to_clean": True,
                "reason": "Merged workspace directory already absent on disk",
                "added_count": 0,
                "modified_count": 0,
                "deleted_count": 0,
                "added_files": [],
                "modified_files": [],
                "deleted_files": [],
            }

        if ws["mode"] == "snapshot":
            try:
                status_info = read_status(workspace_id)
            except (WorkspaceError, OSError, NotFoundError) as exc:
                logger.warning("Snapshot GC inspection failed for %s: %s", workspace_id, exc)
                return {
                    "workspace_id": workspace_id,
                    "mode": ws["mode"],
                    "status": ws["status"],
                    "exists": True,
                    "safe_to_clean": False,
                    "reason": f"Snapshot inspection error: {exc}",
                    "added_count": 0,
                    "modified_count": 0,
                    "deleted_count": 0,
                    "added_files": [],
                    "modified_files": [],
                    "deleted_files": [],
                }
            added_files = list(status_info.get("added_files") or [])
            modified_files = list(status_info.get("modified_files") or [])
            deleted_files = list(status_info.get("deleted_files") or [])
            if ws["status"] != "merged":
                safe = False
                reason = (
                    f"Workspace status is {ws['status']!r}, not 'merged' "
                    f"(+A:{len(added_files)}, ~M:{len(modified_files)})"
                )
            elif added_files or modified_files or deleted_files or status_info.get("dirty"):
                safe = False
                reason = (
                    f"Unmerged snapshot changes (+A:{len(added_files)}, "
                    f"~M:{len(modified_files)}, -D:{len(deleted_files)})"
                )
            else:
                safe = True
                reason = "Merged snapshot workspace is clean (+A:0, ~M:0)"
            return {
                "workspace_id": workspace_id,
                "mode": ws["mode"],
                "status": ws["status"],
                "exists": True,
                "safe_to_clean": safe,
                "reason": reason,
                "added_count": len(added_files),
                "modified_count": len(modified_files),
                "deleted_count": len(deleted_files),
                "added_files": added_files,
                "modified_files": modified_files,
                "deleted_files": deleted_files,
            }

        # git_clone mode
        is_empty_git_shell = (
            ws_path.is_dir()
            and not (ws_path / ".git").exists()
            and not any(ws_path.iterdir())
        )
        if is_empty_git_shell:
            if ws["status"] != "merged":
                safe = False
                reason = f"Workspace status is {ws['status']!r}, not 'merged'"
            elif not ws["merged_commit"] or not ws["merged_target_commit"]:
                safe = False
                reason = "Missing merged commit proof; merge again"
            else:
                safe = True
                reason = "Merged empty git_clone shell is clean (+A:0, ~M:0)"
            return {
                "workspace_id": workspace_id,
                "mode": ws["mode"],
                "status": ws["status"],
                "exists": True,
                "safe_to_clean": safe,
                "reason": reason,
                "added_count": 0,
                "modified_count": 0,
                "deleted_count": 0,
                "added_files": [],
                "modified_files": [],
                "deleted_files": [],
            }

        if not (ws_path / ".git").exists():
            return {
                "workspace_id": workspace_id,
                "mode": ws["mode"],
                "status": ws["status"],
                "exists": True,
                "safe_to_clean": False,
                "reason": "Missing .git metadata in non-empty directory",
                "added_count": 0,
                "modified_count": 0,
                "deleted_count": 0,
                "added_files": [],
                "modified_files": [],
                "deleted_files": [],
            }

        try:
            status_info = read_status(workspace_id)
            added_set: set[str] = set()
            modified_set: set[str] = set()
            deleted_set: set[str] = set()

            for line in status_info.get("porcelain_lines") or []:
                code = line[:2]
                entry = line[3:] if len(line) > 3 else line.strip()
                if " -> " in entry:
                    entry = entry.split(" -> ", 1)[1]
                if code == "??" or ("A" in code and "D" not in code):
                    added_set.add(entry)
                elif "D" in code:
                    deleted_set.add(entry)
                else:
                    modified_set.add(entry)

            actual_branch = self._current_branch_or_head(ws_path)
            current_head = _run_git(ws_path, "rev-parse", "HEAD", check=True).stdout.strip()
            ref_commit = ws["merged_commit"] or ws["base_commit"]
            if ref_commit and current_head != ref_commit:
                diff_ns = _run_git(
                    ws_path,
                    "-c",
                    "core.quotepath=false",
                    "diff",
                    "--name-status",
                    f"{ref_commit}..HEAD",
                )
                if diff_ns.returncode == 0:
                    for ln in diff_ns.stdout.splitlines():
                        if not ln.strip():
                            continue
                        parts = ln.split("\t")
                        st_code = parts[0].strip()
                        fpath = parts[-1].strip()
                        if st_code.startswith("A"):
                            added_set.add(fpath)
                        elif st_code.startswith("D"):
                            deleted_set.add(fpath)
                        else:
                            modified_set.add(fpath)

            added_files = sorted(added_set)
            modified_files = sorted(modified_set)
            deleted_files = sorted(deleted_set)

            if ws["status"] != "merged":
                safe = False
                reason = (
                    f"Workspace status is {ws['status']!r}, not 'merged' "
                    f"(+A:{len(added_files)}, ~M:{len(modified_files)})"
                )
            elif not ws["merged_commit"] or not ws["merged_target_commit"]:
                safe = False
                reason = "Missing merged commit proof; merge again"
            elif actual_branch != ws["branch_name"]:
                safe = False
                reason = f"Workspace is on {actual_branch!r}, expected branch {ws['branch_name']!r}"
            elif current_head != ws["merged_commit"]:
                safe = False
                reason = (
                    f"HEAD {current_head} differs from merged commit {ws['merged_commit']}; "
                    "merge again"
                )
            elif (
                status_info.get("dirty")
                or status_info.get("has_uncommitted_changes")
                or added_files
                or modified_files
                or deleted_files
            ):
                safe = False
                reason = (
                    f"Workspace has unmerged/uncommitted changes (+A:{len(added_files)}, "
                    f"~M:{len(modified_files)}, -D:{len(deleted_files)})"
                )
            else:
                self._require_preserved_clone_refs(ws)
                safe = True
                reason = "Merged git_clone workspace is clean (+A:0, ~M:0)"

            return {
                "workspace_id": workspace_id,
                "mode": ws["mode"],
                "status": ws["status"],
                "exists": True,
                "safe_to_clean": safe,
                "reason": reason,
                "added_count": len(added_files),
                "modified_count": len(modified_files),
                "deleted_count": len(deleted_files),
                "added_files": added_files,
                "modified_files": modified_files,
                "deleted_files": deleted_files,
            }
        except (WorkspaceError, OSError) as exc:
            logger.warning("Git GC inspection failed for %s: %s", workspace_id, exc)
            return {
                "workspace_id": workspace_id,
                "mode": ws["mode"],
                "status": ws["status"],
                "exists": True,
                "safe_to_clean": False,
                "reason": f"Git inspection error: {exc}",
                "added_count": 0,
                "modified_count": 0,
                "deleted_count": 0,
                "added_files": [],
                "modified_files": [],
                "deleted_files": [],
            }

    def workspace_gc_cleanup(self, workspace_id: str) -> dict[str, Any]:
        """Reclaim a non-active merged workspace after verifying all static cleanup guards."""
        ws = self._require_workspace(workspace_id)
        with self._workspace_mutation_lock(ws, self.workspace_source_root(ws)):
            with self._lock:
                ws = self._require_workspace(workspace_id)
                ws_path = Path(ws["path"])
                if ws["status"] == "cleaned" and not ws_path.exists() and not _is_reparse_or_symlink(ws_path):
                    return {
                        "workspace_id": workspace_id,
                        "cleaned": True,
                        "already_cleaned": True,
                        "status": "cleaned",
                        "path": ws["path"],
                    }

                run = self.db.get_run(ws["run_id"])
                if run is None:
                    raise NotFoundError(f"Run not found: {ws['run_id']}")
                if run["status"] not in {"completed", "archived"}:
                    raise WorkspaceCleanupGuardError(
                        f"Refusing GC cleanup for workspace {workspace_id}: run status is {run['status']!r}"
                    )

                safety = self.workspace_gc_safety(workspace_id)
                if not safety["safe_to_clean"]:
                    raise WorkspaceCleanupGuardError(
                        f"Refusing GC cleanup for workspace {workspace_id}: {safety['reason']}"
                    )

                self.remove_workspace_checkout(ws)
                parent = ws_path.parent
                if parent.name == WORKSPACES_DIRNAME and parent.exists():
                    try:
                        parent.rmdir()
                    except OSError:
                        pass

                self.db.update_workspace(workspace_id, status="cleaned")
                return {
                    "workspace_id": workspace_id,
                    "cleaned": True,
                    "already_cleaned": False,
                    "status": "cleaned",
                    "path": ws["path"],
                }

    # ------------------------------------------------------------------
    # v0.6.11: discard clean, never-merged workspaces of finished runs.
    #
    # ``workspace_cleanup`` / GC only reclaim *merged* workspaces, so a workspace whose author never
    # started (cancelled dispatch) or never wrote anything stays on disk forever once its run fails
    # or is archived.  Discard is the narrow complement: the run must be finished, the checkout must
    # be byte-identical to its baseline (+A:0 / ~M:0 / -D:0, Git HEAD at the base commit, branch
    # unchanged), no native wake for its author may be unresolved, and a bound author must not be
    # recorded as still working.  Any change at all keeps the "unmerged work is never deleted" rule.
    # ------------------------------------------------------------------
    _DISCARD_RUN_STATUSES = frozenset({"failed", "archived", "completed"})
    _DISCARDABLE_WS_STATUSES = frozenset({"active", "verified", "conflict"})

    def _unresolved_actions_for(self, ws: dict[str, Any]) -> list[dict[str, Any]]:
        with self.db.read_connection() as conn:
            rows = conn.execute(
                "SELECT action_id, recipient, action_type, status, snapshot_json FROM native_actions "
                "WHERE run_id=? AND status IN ('claimed','invoking','needs_reconciliation')",
                (ws["run_id"],),
            ).fetchall()
        result = []
        for row in rows:
            try:
                snapshot = json.loads(row["snapshot_json"] or "{}")
            except (TypeError, ValueError):
                snapshot = {}
            if row["recipient"] == ws["worker_name"] or snapshot.get("dispatch_workspace_id") == ws["id"]:
                result.append({
                    "action_id": row["action_id"],
                    "recipient": row["recipient"],
                    "action_type": row["action_type"],
                    "status": row["status"],
                })
        return result

    def workspace_discard_check(self, workspace_id: str) -> dict[str, Any]:
        """Read-only: may this workspace be discarded?  Never mutates disk or state.db."""
        return self._workspace_discard_check(self._require_workspace(workspace_id), self.workspace_status)

    def _workspace_discard_check(
        self, ws: dict[str, Any], read_status: Callable[[str], dict[str, Any]],
    ) -> dict[str, Any]:
        workspace_id = ws["id"]
        ws_path = Path(ws["path"])
        is_reparse = _is_reparse_or_symlink(ws_path)
        exists = ws_path.exists() or is_reparse
        base = {
            "workspace_id": workspace_id,
            "run_id": ws["run_id"],
            "worker_name": ws["worker_name"],
            "mode": ws["mode"],
            "status": ws["status"],
            "path": ws["path"],
            "exists": bool(exists),
            "unresolved_actions": [],
        }

        def verdict(ok: bool, reason: str, **extra: Any) -> dict[str, Any]:
            return {**base, **extra, "discardable": ok, "reason": reason}

        if ws["status"] == "cleaned":
            if exists:
                return verdict(False, "Status is 'cleaned' but the directory still exists; inspect it manually")
            return verdict(True, "Already discarded or cleaned", already_cleaned=True)
        if ws["status"] == "merged":
            return verdict(False, "Workspace is merged; use workspace_cleanup / GC instead")
        if ws["status"] not in self._DISCARDABLE_WS_STATUSES:
            return verdict(False, f"Workspace status {ws['status']!r} cannot be discarded")
        run = self.db.get_run(ws["run_id"])
        if run is None:
            return verdict(False, f"Run not found: {ws['run_id']}")
        if run["status"] not in self._DISCARD_RUN_STATUSES:
            return verdict(False, f"Run status is {run['status']!r}; discard only applies to failed, archived or completed runs")
        if is_reparse:
            return verdict(False, "Workspace path is a symlink or junction; refusing automatic removal")
        unresolved = self._unresolved_actions_for(ws)
        if unresolved:
            return verdict(
                False,
                f"{len(unresolved)} native action(s) for this workspace are unresolved; reconcile them first "
                "(list_agents observation, then native_action_update(action='reconcile'))",
                unresolved_actions=unresolved,
            )
        worker = self.db.get_worker_by_name(ws["run_id"], ws["worker_name"])
        if worker is not None and worker.get("agent_id") and worker.get("status") == "working":
            return verdict(
                False,
                "The bound author is still recorded as working; confirm it ended (list_agents) and record "
                "worker_status_update(status='stopped') before discarding",
            )

        try:
            if self._uses_git_worktree(ws):
                self._worktree_cleanup_preflight(self.workspace_source_root(ws), ws_path, ws["branch_name"], ws["base_commit"])
        except (WorkspaceError, WorkspaceCleanupGuardError, OSError, subprocess.CalledProcessError) as exc:
            return verdict(False, f"Worktree cleanup preflight: {exc}")

        if not exists:
            return verdict(True, "Directory already absent; only the record remains",
                           added_count=0, modified_count=0, deleted_count=0)

        try:
            status_info = read_status(workspace_id)
        except (WorkspaceError, WorkspaceCleanupGuardError, OSError, NotFoundError) as exc:
            return verdict(False, f"Inspection error: {exc}")
        if ws["mode"] == "snapshot":
            added = list(status_info.get("added_files") or [])
            modified = list(status_info.get("modified_files") or [])
            deleted = list(status_info.get("deleted_files") or [])
            counts = {"added_count": len(added), "modified_count": len(modified), "deleted_count": len(deleted)}
            if added or modified or deleted or status_info.get("dirty"):
                return verdict(
                    False,
                    f"Snapshot has changes (+A:{len(added)}, ~M:{len(modified)}, -D:{len(deleted)}); "
                    "never discard unmerged work",
                    **counts,
                )
            return verdict(True, "Snapshot is identical to its baseline (+A:0, ~M:0, -D:0)", **counts)

        # git_clone (clone or linked worktree)
        if not (ws_path / ".git").exists():
            if any(ws_path.iterdir()):
                return verdict(False, "Missing .git metadata in a non-empty directory")
            return verdict(True, "Empty git_clone shell", added_count=0, modified_count=0, deleted_count=0)
        try:
            actual_branch = self._current_branch_or_head(ws_path)
            head = _run_git(ws_path, "rev-parse", "HEAD", check=True).stdout.strip()
        except (WorkspaceError, OSError, subprocess.CalledProcessError) as exc:
            return verdict(False, f"Git inspection error: {exc}")
        changed = list(status_info.get("uncommitted_files") or [])
        counts = {"added_count": 0, "modified_count": len(changed), "deleted_count": 0,
                  "commits_ahead": int(status_info.get("commits_ahead") or 0)}
        if not ws["base_commit"]:
            return verdict(False, "Missing base commit; cannot prove the clone is unchanged", **counts)
        if actual_branch != ws["branch_name"]:
            return verdict(False, f"Workspace is on {actual_branch!r}, expected {ws['branch_name']!r}", **counts)
        if head != ws["base_commit"] or counts["commits_ahead"]:
            return verdict(
                False,
                f"HEAD {head} has commits beyond base {ws['base_commit']}; never discard unmerged work",
                **counts,
            )
        if changed or status_info.get("dirty"):
            return verdict(
                False,
                f"Working tree has {len(changed)} uncommitted change(s); never discard unmerged work",
                **counts,
            )
        try:
            self._require_preserved_clone_refs(ws)
        except (WorkspaceError, OSError) as exc:
            return verdict(False, f"Git inspection error: {exc}", **counts)
        return verdict(True, "Git checkout is at its base commit with a clean tree", **counts)

    def workspace_discard(self, workspace_id: str, reason: str) -> dict[str, Any]:
        """Remove a clean, never-merged workspace of a finished run and mark it ``cleaned``."""
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("workspace_discard requires a nonblank reason")
        ws = self._require_workspace(workspace_id)
        with self._workspace_mutation_lock(ws, self.workspace_source_root(ws)):
            with self._lock:
                check = self.workspace_discard_check(workspace_id)
                if check.get("already_cleaned"):
                    return {**check, "discarded": True, "already_cleaned": True}
                if not check["discardable"]:
                    raise WorkspaceCleanupGuardError(
                        f"Refusing discard for workspace {workspace_id}: {check['reason']}"
                    )
                ws = self._require_workspace(workspace_id)
                ws_path = Path(ws["path"])
                self.remove_workspace_checkout(ws)
                parent = ws_path.parent
                if parent.name == WORKSPACES_DIRNAME and parent.exists():
                    try:
                        parent.rmdir()
                    except OSError:
                        pass
                self.db.update_workspace(workspace_id, status="cleaned")
                self.db.insert_board_message(
                    ws["run_id"],
                    sender="orchestrator",
                    channel="main",
                    msg_type="info",
                    summary=f"workspace_discard {workspace_id}",
                    body_md=(
                        f"Discarded clean, never-merged workspace `{workspace_id}` "
                        f"(@{ws['worker_name']}, {ws['mode']}).\n\n"
                        f"- Basis: {check['reason']}\n- Reason: {reason.strip()}\n- Path: `{ws['path']}`"
                    ),
                )
                return {**check, "discarded": True, "already_cleaned": False, "status": "cleaned",
                        "discard_reason": reason.strip()}
