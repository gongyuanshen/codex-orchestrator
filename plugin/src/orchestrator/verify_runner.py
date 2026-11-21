"""Local command evidence collection, not a host identity attestation."""
from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
import logging
import math
import os
from pathlib import Path
import sys
import threading
import time
import urllib.request

from orchestrator.artifacts import digest, recapture
from orchestrator.verify_process import ProcessFailure, run_owned

logger = logging.getLogger(__name__)

ENV_PROFILE_KEYS: dict[str, tuple[str, ...]] = {
    'none': (),
    'windows_minimal_py': (
        'SYSTEMROOT',
        'SystemRoot',
        'COMSPEC',
        'PATHEXT',
        'PATH',
        'TEMP',
        'TMP',
        'USERPROFILE',
        'PYTHONUTF8',
        'PYTHONIOENCODING',
    ),
    'posix_minimal_py': (
        'PATH',
        'HOME',
        'TMPDIR',
        'LANG',
        'LC_ALL',
        'PYTHONUTF8',
        'PYTHONIOENCODING',
    ),
}

_OPTIONAL_PROFILE_ENV_KEYS_UPPER = frozenset({
    'COMSPEC',
    'PATHEXT',
    'TEMP',
    'TMP',
    'USERPROFILE',
    'HOME',
    'TMPDIR',
    'LANG',
    'LC_ALL',
    'PYTHONUTF8',
    'PYTHONIOENCODING',
})
_POSIX_OPTIONAL_PROFILE_ENV_KEYS = frozenset({
    'HOME',
    'TMPDIR',
    'LANG',
    'LC_ALL',
    'PYTHONUTF8',
    'PYTHONIOENCODING',
})


def lookup_env_value(
    key: str,
    environ: Mapping[str, str] | None = None,
    *,
    case_insensitive: bool | None = None,
) -> tuple[bool, str | None]:
    """Look up an environment variable (exact match first; case-insensitive on Windows)."""
    source = os.environ if environ is None else environ
    ignore_case = (os.name == 'nt') if case_insensitive is None else bool(case_insensitive)
    if key in source:
        return True, source[key]
    if ignore_case:
        target = key.upper()
        for env_key, env_val in source.items():
            if env_key.upper() == target:
                return True, env_val
    return False, None


def build_isolated_env(
    env_keys: Sequence[str],
    environ: Mapping[str, str] | None = None,
    *,
    case_insensitive: bool | None = None,
    command_id: str | None = None,
) -> dict[str, str]:
    """Filter `environ` strictly by `env_keys` allowlist without leaking or serializing values."""
    source = os.environ if environ is None else environ
    ignore_case = (os.name == 'nt') if case_insensitive is None else bool(case_insensitive)
    folded: dict[str, str] = {}
    if ignore_case:
        for env_key, env_val in source.items():
            folded.setdefault(env_key.upper(), env_val)

    isolated: dict[str, str] = {}
    seen_upper: set[str] = set()
    missing: list[str] = []
    for key in env_keys:
        if key in source:
            found, value = True, source[key]
        elif ignore_case and key.upper() in folded:
            found, value = True, folded[key.upper()]
        else:
            found, value = False, None

        if found and value is not None:
            if ignore_case:
                upper_key = key.upper()
                if upper_key not in seen_upper:
                    seen_upper.add(upper_key)
                    isolated[key] = value
            else:
                isolated[key] = value
        else:
            is_optional = (
                key.upper() in _OPTIONAL_PROFILE_ENV_KEYS_UPPER
                if ignore_case
                else key in _POSIX_OPTIONAL_PROFILE_ENV_KEYS
            )
            if not is_optional:
                missing.append(key)

    if missing:
        prefix = f'{command_id}: ' if command_id else ''
        raise ValueError(f'{prefix}required environment keys missing: {missing}')
    return isolated


def _now():
    return datetime.now(timezone.utc).isoformat()


def _absolute(raw, label):
    if not isinstance(raw, str) or not Path(raw).is_absolute():
        raise ValueError(f'{label} must be an absolute path')
    return Path(raw).resolve()


def validate_evidence_path(path: Path, artifact: dict):
    root = Path(artifact['root']).resolve()
    scopes = [root / p for p in artifact['paths']] if artifact['paths'] is not None else [root]
    if any(path == scope or path.is_relative_to(scope) or scope.is_relative_to(path) for scope in scopes):
        raise ValueError(f'Evidence path overlaps captured artifact scope: {path}')


def validate_manifest(manifest):
    if not isinstance(manifest, dict):
        raise ValueError('manifest must be an object')
    required = ('run_id', 'step_id', 'artifact_id', 'scope', 'packet_version', 'attempt', 'contract_revision', 'verifier', 'verifier_agent_id', 'artifact', 'commands')
    if any(key not in manifest for key in required):
        raise ValueError('manifest missing frozen binding fields')
    if manifest.get('schema_version') != 1 or manifest.get('evidence_level') != 'local_collector_v1':
        raise ValueError('unsupported manifest schema/evidence_level')
    if manifest['scope'] not in ('candidate', 'integrated'):
        raise ValueError('manifest scope must be candidate or integrated')
    for key in ('run_id', 'step_id', 'artifact_id', 'verifier', 'verifier_agent_id'):
        if not isinstance(manifest[key], str) or not manifest[key].strip():
            raise ValueError(f'{key} must be a nonempty string')
    for key in ('packet_version', 'attempt', 'contract_revision'):
        if type(manifest[key]) is not int or manifest[key] < 0:
            raise ValueError(f'{key} must be a nonnegative integer')
    artifact = manifest['artifact']
    if not isinstance(artifact, dict) or any(key not in artifact for key in ('root', 'paths', 'mode', 'manifest', 'baseline_digest')):
        raise ValueError('manifest artifact is not a capture_content snapshot')
    root = _absolute(artifact['root'], 'artifact.root')
    if not root.is_dir():
        raise ValueError('artifact root does not exist')
    paths = artifact['paths']
    if paths is not None and (not isinstance(paths, list) or not paths or any(not isinstance(p, str) or not p or Path(p).is_absolute() or '..' in Path(p).parts or Path(p) == Path('.') for p in paths)):
        raise ValueError('invalid artifact paths')
    commands = manifest['commands']
    if not isinstance(commands, list) or not commands:
        raise ValueError('commands must be nonempty')
    if 'max_parallel' in manifest and manifest['max_parallel'] is not None:
        max_parallel = manifest['max_parallel']
        if type(max_parallel) is not int or max_parallel < 1:
            raise ValueError('max_parallel must be a positive integer')
    required_cmd_keys = {'id', 'argv', 'cwd', 'timeout_sec', 'env_keys', 'criteria'}
    allowed_cmd_keys = required_cmd_keys | {'parallel_group'}
    ids = set()
    for command in commands:
        if not isinstance(command, dict) or not required_cmd_keys.issubset(command) or not set(command).issubset(allowed_cmd_keys):
            raise ValueError('command fields differ from frozen schema')
        identity = command['id']
        if not isinstance(identity, str) or not identity.strip() or identity in ids:
            raise ValueError('command ids must be unique nonempty strings')
        ids.add(identity)
        if 'parallel_group' in command and command['parallel_group'] is not None:
            group = command['parallel_group']
            if not isinstance(group, str) or not group.strip() or '\0' in group:
                raise ValueError(f'{identity}: parallel_group must be a nonempty string or null')
        argv = command['argv']
        if not isinstance(argv, list) or not argv or not argv[0] or any(not isinstance(a, str) or '\0' in a for a in argv):
            raise ValueError(f'{identity}: argv must be nonempty string array without NUL')
        cwd = _absolute(command['cwd'], f'{identity}.cwd')
        if not cwd.is_dir():
            raise ValueError(f'{identity}: cwd does not exist')
        timeout = command['timeout_sec']
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError(f'{identity}: timeout_sec must be finite and positive')
        for field in ('env_keys', 'criteria'):
            values = command[field]
            if not isinstance(values, list) or any(not isinstance(v, str) or not v.strip() or '\0' in v for v in values) or len(set(values)) != len(values) or (field == 'criteria' and not values):
                raise ValueError(f'{identity}: invalid {field}')
        if any('=' in key for key in command['env_keys']):
            raise ValueError(f'{identity}: invalid environment key')


def _command_batches(commands: Sequence[dict]) -> list[tuple[str | None, list[tuple[int, dict]]]]:
    """Group consecutive commands sharing a non-empty parallel_group; ungrouped commands act as serial barriers."""
    batches: list[tuple[str | None, list[tuple[int, dict]]]] = []
    for index, command in enumerate(commands):
        group = command.get('parallel_group')
        if group is not None and batches and batches[-1][0] == group:
            batches[-1][1].append((index, command))
        else:
            batches.append((group, [(index, command)]))
    return batches


class _HeartbeatKeepalive:
    """Periodic daemon-side worker heartbeat sender active only while collector commands are running."""

    def __init__(self, url: str, interval_sec: float, active_provider) -> None:
        self._url = url
        self._interval_sec = float(interval_sec)
        self._active_provider = active_provider
        self._stop = threading.Event()
        self._started_monotonic = time.monotonic()
        self._thread = threading.Thread(target=self._loop, name='verify-heartbeat', daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2.0)

    def _send_once(self) -> None:
        active = list(self._active_provider())
        if not active:
            return
        elapsed = round(max(0.0, time.monotonic() - self._started_monotonic), 1)
        summary = f"verify running ({len(active)}): {', '.join(active[:4])}"
        if len(active) > 4:
            summary += f" +{len(active) - 4} more"
        payload = json.dumps(
            {
                'current_task': summary[:200],
                'active_commands': active,
                'elapsed_sec': elapsed,
            },
            ensure_ascii=False,
        ).encode('utf-8')
        req = urllib.request.Request(
            self._url,
            data=payload,
            headers={'Content-Type': 'application/json'},
            method='POST',
        )
        try:
            with urllib.request.urlopen(req, timeout=5.0) as resp:
                resp.read()
        except Exception as exc:
            logger.warning('verify keepalive heartbeat failed (%s): %s', self._url, exc)

    def _loop(self) -> None:
        while not self._stop.wait(self._interval_sec):
            self._send_once()


def _write(path, value):
    temporary = path.with_suffix('.tmp')
    with temporary.open('w', encoding='utf-8', newline='\n') as stream:
        json.dump(value, stream, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def _log(path):
    sha = hashlib.sha256()
    size = 0
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            size += len(chunk)
            sha.update(chunk)
    return dict(path=str(path), bytes=size, sha256=sha.hexdigest())


def collect(
    manifest_path: Path,
    output_dir: Path,
    *,
    heartbeat_url: str | None = None,
    heartbeat_interval_sec: float = 60.0,
):
    manifest_path, output_dir = Path(manifest_path), Path(output_dir)
    if not manifest_path.is_absolute() or not output_dir.is_absolute():
        raise ValueError('manifest and output-dir must be absolute')
    if heartbeat_url is not None:
        if not isinstance(heartbeat_url, str) or not heartbeat_url.startswith(('http://', 'https://')):
            raise ValueError('heartbeat_url must be an http:// or https:// URL')
        if isinstance(heartbeat_interval_sec, bool) or not math.isfinite(float(heartbeat_interval_sec)) or float(heartbeat_interval_sec) <= 0:
            raise ValueError('heartbeat_interval_sec must be finite and positive')
    manifest_path, output_dir = manifest_path.resolve(strict=True), output_dir.resolve()
    manifest = json.loads(manifest_path.read_text('utf-8'))
    validate_manifest(manifest)
    validate_evidence_path(manifest_path, manifest['artifact'])
    validate_evidence_path(output_dir, manifest['artifact'])
    command_envs = [
        build_isolated_env(command['env_keys'], os.environ, command_id=command['id'])
        for command in manifest['commands']
    ]
    max_parallel = int(manifest.get('max_parallel') or 1)
    output_dir.mkdir()  # Existing directories are never reused.
    receipt_path = output_dir / 'receipt.json'
    receipt = dict(schema_version=1, evidence_level='local_collector_v1', manifest_digest=digest(manifest), status='running', started_at=_now(), ended_at=None, artifact_before_digest=None, artifact_after_digest=None, commands=[], error=None)
    receipt_lock = threading.Lock()
    active_ids: set[str] = set()
    keepalive = _HeartbeatKeepalive(
        heartbeat_url,
        float(heartbeat_interval_sec),
        lambda: sorted(active_ids),
    ) if heartbeat_url else None

    def _persist() -> None:
        with receipt_lock:
            _write(receipt_path, receipt)

    def _run_one(index: int, command: dict, current: dict) -> None:
        out = output_dir / f'{index:04d}.stdout'
        err = output_dir / f'{index:04d}.stderr'
        command_error = None
        with receipt_lock:
            active_ids.add(command['id'])
        try:
            with out.open('xb') as stdout, err.open('xb') as stderr:
                exit_code = run_owned(command['argv'], command['cwd'], command_envs[index], command['timeout_sec'], stdout, stderr)
            with receipt_lock:
                current['exit_code'] = exit_code
                current['status'] = 'finished'
                current['ended_at'] = _now()
        except BaseException as exc:
            command_error = exc
            with receipt_lock:
                current['status'] = exc.status if isinstance(exc, ProcessFailure) else ('cancelled' if isinstance(exc, KeyboardInterrupt) else 'collection_failed')
                if isinstance(exc, ProcessFailure):
                    current['exit_code'] = exc.exit_code
            raise
        finally:
            # Partial bytes remain evidence even when launch, wait, or collection fails.
            with receipt_lock:
                active_ids.discard(command['id'])
            try:
                captured_logs = {}
                for name, path in (('stdout', out), ('stderr', err)):
                    if path.is_file():
                        captured_logs[name] = _log(path)
                with receipt_lock:
                    for name, info in captured_logs.items():
                        current[name] = info
                    _write(receipt_path, receipt)
            except BaseException as capture_error:
                raise ProcessFailure('collection_failed', f'primary={command_error!r}; capture={capture_error!r}', current['exit_code']) from capture_error

    _persist()
    if keepalive is not None:
        keepalive.start()
    try:
        receipt['artifact_before_digest'] = digest(recapture(manifest['artifact']))
        if receipt['artifact_before_digest'] != digest(manifest['artifact']):
            raise ProcessFailure('artifact_changed', 'source checkpoint differs before execution')
        _persist()
        for group, batch in _command_batches(manifest['commands']):
            if group is None or max_parallel <= 1 or len(batch) <= 1:
                for index, command in batch:
                    out = output_dir / f'{index:04d}.stdout'
                    err = output_dir / f'{index:04d}.stderr'
                    rec = dict(
                        command_id=command['id'],
                        status='running',
                        started_at=_now(),
                        ended_at=None,
                        exit_code=None,
                        stdout=dict(path=str(out), bytes=None, sha256=None),
                        stderr=dict(path=str(err), bytes=None, sha256=None),
                    )
                    with receipt_lock:
                        receipt['commands'].append(rec)
                        _write(receipt_path, receipt)
                    _run_one(index, command, rec)
            else:
                records: list[tuple[int, dict, dict]] = []
                with receipt_lock:
                    for index, command in batch:
                        out = output_dir / f'{index:04d}.stdout'
                        err = output_dir / f'{index:04d}.stderr'
                        rec = dict(
                            command_id=command['id'],
                            status='running',
                            started_at=_now(),
                            ended_at=None,
                            exit_code=None,
                            stdout=dict(path=str(out), bytes=None, sha256=None),
                            stderr=dict(path=str(err), bytes=None, sha256=None),
                        )
                        receipt['commands'].append(rec)
                        records.append((index, command, rec))
                    _write(receipt_path, receipt)
                workers = min(max_parallel, len(records))
                first_error: BaseException | None = None
                with ThreadPoolExecutor(max_workers=workers) as pool:
                    futures = [pool.submit(_run_one, index, command, rec) for index, command, rec in records]
                    for fut in futures:
                        try:
                            fut.result()
                        except BaseException as exc:
                            if first_error is None:
                                first_error = exc
                if first_error is not None:
                    raise first_error

        receipt['artifact_after_digest'] = digest(recapture(manifest['artifact']))
        if receipt['artifact_after_digest'] != digest(manifest['artifact']):
            raise ProcessFailure('artifact_changed', 'source checkpoint differs after execution')
        receipt['status'] = 'finished'
        receipt['ended_at'] = _now()
    except BaseException as exc:
        receipt['status'] = exc.status if isinstance(exc, ProcessFailure) else ('cancelled' if isinstance(exc, KeyboardInterrupt) else 'collection_failed')
        receipt['error'] = f'{type(exc).__name__}: {exc}'
        # Incomplete records have no ended_at; never claim collection finished.
    finally:
        if keepalive is not None:
            keepalive.stop()
    _persist()
    return receipt


def _timestamp(value, label):
    if not isinstance(value, str):
        raise ValueError(f'{label}: timestamp missing')
    try:
        result = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f'{label}: invalid timestamp') from exc
    if result.tzinfo is None:
        raise ValueError(f'{label}: timezone missing')
    return result


def validate_receipt(manifest: dict, receipt_path: Path, *, require_success: bool):
    validate_manifest(manifest)
    path = Path(receipt_path)
    if not path.is_absolute():
        raise ValueError('receipt path must be absolute')
    path = path.resolve(strict=True)
    validate_evidence_path(path, manifest['artifact'])
    try:
        receipt = json.loads(path.read_text('utf-8'))
    except (OSError, ValueError) as exc:
        raise ValueError(f'invalid receipt: {path}: {exc}') from exc
    if not isinstance(receipt, dict) or receipt.get('schema_version') != 1 or receipt.get('evidence_level') != 'local_collector_v1' or receipt.get('manifest_digest') != digest(manifest):
        raise ValueError('receipt manifest identity mismatch')
    start = _timestamp(receipt.get('started_at'), 'receipt.started_at')
    status = receipt.get('status')
    allowed = {'running', 'finished', 'startup_failed', 'timeout', 'cancelled', 'collection_failed', 'cleanup_failed', 'descendants_alive', 'artifact_changed'}
    if status not in allowed:
        raise ValueError('invalid receipt status')
    finished = status == 'finished'
    end = _timestamp(receipt.get('ended_at'), 'receipt.ended_at') if finished else None
    if finished:
        if end < start or receipt.get('error') is not None:
            raise ValueError('invalid finished receipt timing/error')
    elif receipt.get('ended_at') is not None:
        raise ValueError('incomplete receipt cannot have completion timestamp')
    commands = receipt.get('commands')
    if not isinstance(commands, list) or len(commands) > len(manifest['commands']):
        raise ValueError('receipt commands differ from manifest')
    evidence = [path]
    all_success = True
    previous_batch_end = start
    batches = _command_batches(manifest['commands'])
    batch_cursor = 0
    current_batch_index = -1
    current_batch_ends: list[datetime | None] = []
    batch_index_by_cmd: list[int] = []
    for b_idx, (_group, items) in enumerate(batches):
        for _cmd_idx, _cmd in items:
            batch_index_by_cmd.append(b_idx)

    for index, record in enumerate(commands):
        b_idx = batch_index_by_cmd[index]
        if b_idx != current_batch_index:
            if current_batch_index >= 0:
                if any(item is None for item in current_batch_ends):
                    previous_batch_end = None
                else:
                    previous_batch_end = max(current_batch_ends)  # type: ignore[type-var]
            current_batch_index = b_idx
            current_batch_ends = []
            batch_cursor = b_idx
        if not isinstance(record, dict) or record.get('command_id') != manifest['commands'][index]['id']:
            raise ValueError('receipt command order/identity mismatch')
        command_start = _timestamp(record.get('started_at'), 'command.started_at')
        if previous_batch_end is None or command_start < previous_batch_end or (end is not None and command_start > end) or record.get('status') not in allowed:
            raise ValueError('invalid command timing/status')
        command_finished = record['status'] == 'finished'
        code = record.get('exit_code')
        if code is not None and (not isinstance(code, int) or isinstance(code, bool)):
            raise ValueError('invalid command exit_code')
        if command_finished:
            command_end = _timestamp(record.get('ended_at'), 'command.ended_at')
            if code is None or command_end < command_start or (end is not None and command_end > end):
                raise ValueError('finished command lacks real exit/timestamps')
            current_batch_ends.append(command_end)
        elif record.get('ended_at') is not None:
            raise ValueError('incomplete command has completion timestamp')
        else:
            current_batch_ends.append(None)
        all_success = all_success and command_finished and code == 0
        for name in ('stdout', 'stderr'):
            log = record.get(name)
            if not isinstance(log, dict) or set(log) != {'path', 'bytes', 'sha256'}:
                raise ValueError('invalid output record')
            output = _absolute(log['path'], 'output.path')
            if output.parent != path.parent or output.name != f'{index:04d}.{name}':
                raise ValueError('output path escapes collector directory or differs from command')
            if output.exists() and not output.is_file():
                raise ValueError('output path is not a regular file')
            if output.is_file():
                if log['bytes'] is None and log['sha256'] is None and not command_finished:
                    pass  # Forced collector death can leave raw files before digest capture.
                elif log != _log(output):
                    raise ValueError('output digest/size mismatch')
                evidence.append(output)
            elif log['bytes'] is not None or log['sha256'] is not None or command_finished:
                raise ValueError('recorded output is missing')
    del batch_cursor
    if finished and (len(commands) != len(manifest['commands']) or any(c['status'] != 'finished' for c in commands)):
        raise ValueError('finished receipt has incomplete commands')
    expected = digest(manifest['artifact'])
    if finished and (receipt.get('artifact_before_digest') != expected or receipt.get('artifact_after_digest') != expected):
        raise ValueError('source checkpoint mismatch')
    if require_success:
        if not finished or not all_success:
            raise ValueError('receipt does not prove complete successful command collection')
        if digest(recapture(manifest['artifact'])) != expected:
            raise ValueError('current source checkpoint mismatch')
    return evidence


def main():
    import signal
    if os.name != 'nt':
        def cancel_signal(signum, frame):
            raise KeyboardInterrupt(f'collector received signal {signum}')
        signal.signal(signal.SIGTERM, cancel_signal)
    parser = argparse.ArgumentParser()
    parser.add_argument('--manifest', required=True, type=Path)
    parser.add_argument('--output-dir', required=True, type=Path)
    parser.add_argument('--heartbeat-url', default=None, type=str)
    parser.add_argument('--heartbeat-interval-sec', default=60.0, type=float)
    args = parser.parse_args()
    try:
        receipt = collect(
            args.manifest,
            args.output_dir,
            heartbeat_url=args.heartbeat_url,
            heartbeat_interval_sec=args.heartbeat_interval_sec,
        )
    except (OSError, ValueError) as exc:
        print(json.dumps(dict(status='collection_failed', error=str(exc)[:1000]), ensure_ascii=True))
        return 1
    print(json.dumps(dict(status=receipt['status'], receipt_path=str(args.output_dir.resolve() / 'receipt.json'), manifest_digest=receipt['manifest_digest']), ensure_ascii=True))
    return 0 if receipt['status'] == 'finished' and all(c['exit_code'] == 0 for c in receipt['commands']) else 1


if __name__ == '__main__':
    sys.exit(main())
