"""Conservative ownership normalization shared by workflow admission and hints."""
from pathlib import Path, PureWindowsPath
import os


def ownership_scopes(root: Path, paths: list[str]) -> list[str]:
    result = []
    root = root.resolve(strict=True)
    for raw in paths:
        path = PureWindowsPath(raw)
        if path.is_absolute() or path.drive or '..' in path.parts:
            raise ValueError('Ownership paths must remain relative to the project')
        parts = []
        for part in path.parts:
            if any(char in part for char in '*?['):
                break
            if part.endswith((' ', '.')) or ':' in part:
                raise ValueError('Ambiguous Windows ownership path alias: ' + raw)
            parts.append(part)
        target = root.joinpath(*parts).resolve(strict=False)
        if not target.is_relative_to(root):
            raise ValueError('Ownership path alias escapes the project: ' + raw)
        # Hard links are distinct pathnames for the same writable file identity.
        if target.is_file() and target.stat().st_nlink > 1:
            raise ValueError('Hard-linked ownership alias cannot be proven exclusive: ' + raw)
        if target.is_dir():
            from orchestrator.workspace_mgr import SNAPSHOT_EXCLUDE_DIRS
            for directory, dirs, files in os.walk(target, followlinks=False):
                dirs[:] = [name for name in dirs if name not in SNAPSHOT_EXCLUDE_DIRS]
                for name in files:
                    member = Path(directory) / name
                    if member.is_file() and member.stat().st_nlink > 1:
                        raise ValueError('Hard-linked ownership alias cannot be proven exclusive: ' + str(member))
        result.append(target.relative_to(root).as_posix().casefold())
    return sorted(set(result))


def overlap(left: list[str], right: list[str]) -> tuple[str, str] | None:
    for a in left:
        for b in right:
            if a == '.' or b == '.' or a == b or a.startswith(b + '/') or b.startswith(a + '/'):
                return a, b
    return None


def contains(scopes: list[str], path: str) -> bool:
    path = path.replace('\\', '/').casefold()
    return any(scope == '.' or path == scope or path.startswith(scope + '/') for scope in scopes)
