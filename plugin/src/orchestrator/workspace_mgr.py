"""Isolated workspace manager for git clone --local and non-git snapshot fallback."""

from __future__ import annotations

import difflib
import hashlib
import os
import re
import shutil
import stat
import subprocess
import sys
import threading
import uuid
from pathlib import Path
from typing import Any

from orchestrator.config import Settings, get_settings
from orchestrator.db import Database, NotFoundError

try:
    import _winapi
except ImportError:  # pragma: no cover - non-Windows fallback
    _winapi = None  # type: ignore[assignment]

VALID_DEP_MODES = frozenset({"none", "link_ro_cache", "fresh_venv_uv", "copy"})
VALID_MERGE_STRATEGIES = frozenset({"merge", "cherry-pick"})

WORKSPACES_DIRNAME = ".swarm-workspaces"
EXCLUDE_ENTRY = ".swarm-workspaces/"
SNAPSHOT_EXCLUDE_DIRS = frozenset(
    {WORKSPACES_DIRNAME, ".git", "node_modules", ".venv", "__pycache__"}
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

    shutil.rmtree(root, onexc=_onexc_remove_readonly)


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
        return hasattr(path, "is_junction") and path.is_junction()
    except OSError:
        return False


def _scan_snapshot_files(
    root: Path,
    *,
    exclude_root_files: frozenset[str] = frozenset(),
) -> dict[str, str]:
    """Walk root and return {posix_rel_path: sha256_hex} excluding ignored directories/files."""
    manifest: dict[str, str] = {}
    if not root.exists():
        return manifest

    for dirpath, dirnames, filenames in os.walk(root):
        parent = Path(dirpath)
        dirnames[:] = sorted(
            d
            for d in dirnames
            if d not in SNAPSHOT_EXCLUDE_DIRS and not _is_reparse_or_symlink(parent / d)
        )
        rel_dir = parent.relative_to(root)
        is_root = rel_dir == Path(".")
        for fname in sorted(filenames):
            if is_root and fname in exclude_root_files:
                continue
            full_path = parent / fname
            if full_path.is_symlink():
                continue
            rel_path = (rel_dir / fname).as_posix()
            manifest[rel_path] = _sha256_file(full_path)
    return manifest


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

    def _resolve_project_root(
        self, run_id: str, project_root: str | Path | None
    ) -> tuple[dict[str, Any], Path]:
        run = self.db.get_run(run_id)
        if run is None:
            raise NotFoundError(f"Run not found: {run_id}")
        if project_root is not None:
            root = Path(project_root).resolve(strict=False)
        else:
            project = self.db.get_project(run["project_id"])
            if project is None:
                raise NotFoundError(f"Project not found: {run['project_id']}")
            root = Path(project["root_path"]).resolve(strict=False)
        if not root.exists() or not root.is_dir():
            raise FileNotFoundError(f"Project root directory does not exist: {root}")
        return run, root

    def _get_workspace_project_root(self, ws: dict[str, Any]) -> Path:
        manifest = ws.get("snapshot_manifest") or {}
        if isinstance(manifest, dict) and manifest.get("project_root"):
            return Path(manifest["project_root"]).resolve(strict=False)
        run = self.db.get_run(ws["run_id"])
        if run is None:
            raise NotFoundError(f"Run not found: {ws['run_id']}")
        project = self.db.get_project(run["project_id"])
        if project is None:
            raise NotFoundError(f"Project not found: {run['project_id']}")
        root = Path(project["root_path"]).resolve(strict=False)
        # If project_root is a subdirectory of a git repo, map to git toplevel for git operations.
        if ws["mode"] == "git_clone":
            top = _run_git(root, "rev-parse", "--show-toplevel")
            if top.returncode == 0 and top.stdout.strip():
                return Path(top.stdout.strip()).resolve(strict=False)
        return root

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

    def _copy_env_files(
        self,
        source_roots: list[Path],
        ws_dir: Path,
        *,
        is_git: bool,
    ) -> list[str]:
        copied: list[str] = []
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
            if is_git and self._is_git_tracked(ws_dir, fname):
                # Already checked out from tracked git history; do not overwrite or exclude.
                continue
            shutil.copy2(src_file, dst_file)
            copied.append(fname)
            if is_git and not self._is_git_ignored(ws_dir, fname):
                excludes_to_add.append(fname)
        if is_git and excludes_to_add:
            _append_git_exclude_entries(ws_dir / ".git", excludes_to_add)
        return copied

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
            _append_git_exclude_entries(ws_dir / ".git", excludes_to_add)
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

    def workspace_create(
        self,
        run_id: str,
        worker_name: str,
        project_root: str | Path | None = None,
        copy_env: bool = True,
        dep_mode: str = "none",
    ) -> dict[str, Any]:
        """Create an isolated git clone --local or non-git snapshot workspace and allocate a port."""
        if dep_mode not in VALID_DEP_MODES:
            raise ValueError(
                f"Invalid dep_mode {dep_mode!r}; expected one of {sorted(VALID_DEP_MODES)}"
            )
        if not worker_name or not worker_name.strip():
            raise ValueError("worker_name must be a non-empty string")

        with self._lock:
            _, resolved_root = self._resolve_project_root(run_id, project_root)
            is_git_worktree, repo_root, git_common_dir, has_head = self._detect_git_repo(
                resolved_root
            )

            if is_git_worktree and git_common_dir is not None:
                _append_git_exclude_entries(git_common_dir, [EXCLUDE_ENTRY])

            use_git_clone = is_git_worktree and has_head
            base_root = repo_root if use_git_clone else resolved_root
            ws_dir, branch_name = self._allocate_workspace_dir_and_branch(
                base_root, run_id, worker_name
            )

            source_roots = [resolved_root]
            if repo_root != resolved_root:
                source_roots.append(repo_root)

            try:
                if use_git_clone:
                    _run_cmd(
                        ["git", "clone", "--local", str(repo_root), str(ws_dir)],
                        check=True,
                    )
                    _run_git(ws_dir, "checkout", "-b", branch_name, check=True)
                    self._copy_git_identity(repo_root, ws_dir)
                    base_commit = _run_git(ws_dir, "rev-parse", "HEAD", check=True).stdout.strip()

                    if copy_env:
                        self._copy_env_files(source_roots, ws_dir, is_git=True)
                    self._apply_dep_mode(base_root, ws_dir, dep_mode, is_git=True)

                    ws_record = self.db.create_workspace(
                        run_id,
                        worker_name,
                        path=ws_dir,
                        mode="git_clone",
                        branch_name=branch_name,
                        base_commit=base_commit,
                        auto_allocate_port=True,
                        snapshot_manifest={"project_root": str(base_root)},
                    )
                else:
                    exclude_files = frozenset() if copy_env else frozenset(ENV_FILENAMES)
                    ws_dir.mkdir(parents=True, exist_ok=True)
                    baseline_files: dict[str, str] = {}

                    for dirpath, dirnames, filenames in os.walk(resolved_root):
                        parent = Path(dirpath)
                        dirnames[:] = sorted(
                            d
                            for d in dirnames
                            if d not in SNAPSHOT_EXCLUDE_DIRS and not _is_reparse_or_symlink(parent / d)
                        )
                        rel_dir = parent.relative_to(resolved_root)
                        is_root = rel_dir == Path(".")
                        (ws_dir / rel_dir).mkdir(parents=True, exist_ok=True)
                        for fname in sorted(filenames):
                            if is_root and fname in exclude_files:
                                continue
                            src_file = parent / fname
                            if src_file.is_symlink():
                                continue
                            dst_file = ws_dir / rel_dir / fname
                            shutil.copy2(src_file, dst_file)
                            if not (is_root and fname in ENV_FILENAMES):
                                rel_posix = (rel_dir / fname).as_posix()
                                baseline_files[rel_posix] = _sha256_file(src_file)

                    self._apply_dep_mode(resolved_root, ws_dir, dep_mode, is_git=False)

                    manifest = {
                        "project_root": str(resolved_root),
                        "copy_env": bool(copy_env),
                        "files": baseline_files,
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
            except BaseException:
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
        baseline: dict[str, str] = dict(manifest.get("files") or {})
        project_root = self._get_workspace_project_root(ws)
        exclude_env = frozenset(ENV_FILENAMES)
        ws_files = _scan_snapshot_files(ws_path, exclude_root_files=exclude_env)
        root_files = _scan_snapshot_files(project_root, exclude_root_files=exclude_env)

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
        baseline: dict[str, str] = dict(manifest.get("files") or {})
        project_root = self._get_workspace_project_root(ws)
        exclude_env = frozenset(ENV_FILENAMES)
        ws_files = _scan_snapshot_files(ws_path, exclude_root_files=exclude_env)
        root_files = _scan_snapshot_files(project_root, exclude_root_files=exclude_env)

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
                repo_root = self._get_workspace_project_root(ws)
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
                    _run_git(
                        repo_root,
                        "fetch",
                        "--no-tags",
                        "--force",
                        str(ws_path),
                        f"{ws_branch}:{preview_ref}",
                        check=True,
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
        baseline: dict[str, str] = dict(manifest.get("files") or {})
        project_root = self._get_workspace_project_root(ws)
        exclude_env = frozenset(ENV_FILENAMES)
        ws_files = _scan_snapshot_files(ws_path, exclude_root_files=exclude_env)
        root_files = _scan_snapshot_files(project_root, exclude_root_files=exclude_env)

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
        root = self._get_workspace_project_root(ws)
        key = os.path.normcase(str(root.resolve()))
        with self._project_locks_guard:
            lock = self._project_locks.setdefault(key, threading.RLock())
        # Shared across plugin managers; external editors are detected, not locked.
        with lock:
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
        baseline = ws["snapshot_manifest"]["files"]
        excluded = frozenset(ENV_FILENAMES)
        approved = _scan_snapshot_files(source, exclude_root_files=excluded)
        before = _scan_snapshot_files(root, exclude_root_files=excluded)
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
        staging = self.settings.data_dir / "merge-staging" / merge_id
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
        service.begin_merge(step["id"], checkpoint)
        try:
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
            if _scan_snapshot_files(root, exclude_root_files=excluded) != desired:
                raise WorkspaceConflictError("Snapshot target differs from approved merge result")
            proof = {"merge_id": merge_id, "mode": "snapshot", "project_root": str(root),
                     "source_manifest": approved, "target_manifest": desired}
            with self.db.transaction():
                self.db.update_workspace(ws["id"], status="merged", snapshot_manifest={
                    **ws["snapshot_manifest"], "files": approved})
                service.finish_merge(step["id"], proof)
            return {"workspace_id": ws["id"], "mode": "snapshot", "merged": True,
                    "has_conflicts": False, "status": "merged", "target_branch": None,
                    "strategy": strategy, "commits_merged": [], "files_changed": changes,
                    "conflict_files": [], "merge_id": merge_id,
                    "message": f"Applied and read back {len(changes)} approved snapshot changes"}
        except BaseException as exc:
            service.mark_merge_uncertain(step["id"], f"{type(exc).__name__}: {exc}")
            raise

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
            _run_git(root, "fetch", "--no-tags", "--force", str(source),
                     f"{ws['branch_name']}:{ref}", check=True)
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
                        "files_changed": _run_git(root, "diff", "--name-only", before, "HEAD", check=True).stdout.splitlines(),
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
            current = _scan_snapshot_files(root, exclude_root_files=frozenset(ENV_FILENAMES))
            if current != checkpoint["desired_manifest"]:
                state = "not_applied" if current == checkpoint["before_manifest"] else "partially_applied_or_changed"
                raise WorkspaceConflictError(f"Merge reconciliation needs a decision: {state}; no files replayed")
            proof.update(source_manifest=checkpoint["source_manifest"], target_manifest=current)
            updates = {"snapshot_manifest": {**ws["snapshot_manifest"], "files": checkpoint["source_manifest"]}}
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

                repo_root = self._get_workspace_project_root(ws)
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
                    _run_git(
                        repo_root,
                        "fetch",
                        "--no-tags",
                        "--force",
                        str(ws_path),
                        f"{ws_branch}:{merge_ref}",
                        check=True,
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
            baseline: dict[str, str] = dict(manifest.get("files") or {})
            project_root = self._get_workspace_project_root(ws)
            exclude_env = frozenset(ENV_FILENAMES)
            ws_files = _scan_snapshot_files(ws_path, exclude_root_files=exclude_env)
            root_files = _scan_snapshot_files(project_root, exclude_root_files=exclude_env)

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
                "project_root": str(project_root),
                "copy_env": bool(manifest.get("copy_env", True)),
                "files": ws_files,
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
        key = os.path.normcase(str(self._get_workspace_project_root(ws).resolve()))
        with self._project_locks_guard:
            lock = self._project_locks.setdefault(key, threading.RLock())
        with lock:
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
            if run is not None and run["workflow_version"] == 1:
                from orchestrator.workflow import WorkflowService
                WorkflowService(self.db).require_integrated(workspace_id)

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

            if ws_path.exists() or ws_path.is_symlink():
                parent = ws_path.parent
                _rmtree_safe(ws_path)
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
