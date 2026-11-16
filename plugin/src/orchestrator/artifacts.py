"""Backend-owned content captures, using the existing workspace scan boundary."""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(',', ':')).encode('utf-8')).hexdigest()


def git(root: Path, *args: str) -> str:
    p = subprocess.run(['git', '-C', str(root), *args], capture_output=True, check=True)
    return p.stdout.decode('utf-8').strip()


def capture_content(root: Path, *, paths: list[str] | None = None,
                    mode: str = 'snapshot', baseline: dict | None = None) -> dict[str, Any]:
    from orchestrator.workspace_mgr import (_scan_snapshot_files, _is_reparse_or_symlink,
                                            _sha256_file, ENV_FILENAMES, SNAPSHOT_EXCLUDE_DIRS)
    root = root.resolve(strict=True)
    if not root.is_dir():
        raise ValueError('Artifact scope root must be a directory')
    manifest = {}
    normalized = None
    if paths is not None:
        if not isinstance(paths, list) or not paths or any(not isinstance(p, str) or not p.strip() for p in paths):
            raise ValueError('Artifact scope paths must be a nonempty list of relative paths')
        normalized = []
        selected = {}
        for raw in paths:
            relative = Path(raw)
            if relative.is_absolute() or '..' in relative.parts or relative == Path('.'):
                raise ValueError(f'Artifact scope escapes project: {raw}')
            target = root / relative
            if any(part in SNAPSHOT_EXCLUDE_DIRS for part in relative.parts) or (len(relative.parts) == 1 and relative.name in ENV_FILENAMES):
                raise ValueError(f'Artifact scope is excluded from source scanning: {raw}')
            if not target.resolve(strict=False).is_relative_to(root):
                raise ValueError(f'Artifact scope escapes project: {raw}')
            if any(_is_reparse_or_symlink(part) for part in (target, *target.parents) if part != root and part.is_relative_to(root)):
                raise ValueError(f'Artifact scope contains a symbolic link: {raw}')
            name = relative.as_posix()
            normalized.append(name)
            if target.is_dir():
                matches = {name + '/' + k: v for k, v in _scan_snapshot_files(target).items()}
            elif target.is_file():
                matches = {name: _sha256_file(target)}
            elif target.exists():
                raise ValueError(f'Artifact scope is not a regular file or directory: {raw}')
            else:
                matches = {}
            selected.update(matches or {name: None})
        manifest = selected
        normalized = sorted(set(normalized))
    else:
        manifest = _scan_snapshot_files(root, exclude_root_files=ENV_FILENAMES)
    result = {'root': str(root), 'mode': mode, 'paths': normalized,
              'manifest': dict(sorted(manifest.items())), 'baseline_digest': digest(baseline or {})}
    if mode == 'git_clone':
        if git(root, 'status', '--porcelain'):
            raise ValueError('Git candidate must have a clean working tree')
        result['source_commit'] = git(root, 'rev-parse', 'HEAD')
        result['branch'] = git(root, 'rev-parse', '--abbrev-ref', 'HEAD')
    return result


def recapture(descriptor: dict[str, Any]) -> dict[str, Any]:
    result = capture_content(Path(descriptor['root']), paths=descriptor['paths'], mode=descriptor['mode'])
    # The baseline is immutable metadata, not the WorkspaceManager's later merge baseline.
    result['baseline_digest'] = descriptor['baseline_digest']
    return result


def capture_evidence(paths: list[str]) -> list[dict[str, str]]:
    if not isinstance(paths, list) or not paths:
        raise ValueError('Verification requires actual evidence files')
    result = []
    for raw in paths:
        path = Path(raw).resolve(strict=True)
        if not path.is_file():
            raise ValueError(f'Evidence must be a file: {path}')
        result.append({'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
    return result


def evidence_current(manifest: list[dict[str, str]]) -> bool:
    for item in manifest:
        path = Path(item['path'])
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != item['sha256']:
            return False
    return True
