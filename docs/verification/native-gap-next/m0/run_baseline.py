"""Deterministic M0 fixtures on a git-archive baseline, never a native-model benchmark."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
import zipfile

BASELINE = '6ccba9bbce41a32818fb6c8a1841b97578904ec7'
HERE = Path(__file__).resolve().parent
REPO = HERE.parents[3]


def load_cases():
    return json.loads((HERE / 'cases.json').read_text(encoding='utf-8'))


def unavailable_metrics():
    return dict(tokens=None, host_state=None, duplicate_native_side_effects=None,
                unhandled_native_messages=None, empty_model_turns=None,
                unplanned_human_rescues=None, native_rework_rounds=None,
                native_correct_completion=None)


def junit_counts(path):
    root = ET.parse(path).getroot()
    suites = [root] if root.tag == 'testsuite' else list(root.iter('testsuite'))
    return {key: sum(int(s.attrib[key]) for s in suites)
            for key in ('tests', 'failures', 'errors', 'skipped')}


def execute(command, cwd, log, env):
    start = time.monotonic()
    result = subprocess.run(command, cwd=cwd, env=env, capture_output=True,
                            text=True, encoding='utf-8', timeout=120)
    elapsed = time.monotonic() - start
    log.write_text(json.dumps(dict(command=command, cwd=str(cwd), exit_code=result.returncode,
                                   elapsed_seconds=elapsed), ensure_ascii=False) +
                   '\n--- stdout ---\n' + result.stdout + '\n--- stderr ---\n' + result.stderr,
                   encoding='utf-8')
    return result.returncode, elapsed


def run(output):
    # Exclusive creation prevents overwriting prior evidence or accidentally reusing mutable state.
    output.mkdir(parents=True, exist_ok=False)
    env = dict(os.environ, PYTHONUTF8='1', PYTHONIOENCODING='utf-8', PYTHONDONTWRITEBYTECODE='1')
    archive = output / 'baseline.zip'
    subprocess.run(['git', 'archive', '--format=zip', '--output', str(archive), BASELINE],
                   cwd=REPO, check=True, env=env)
    baseline = output / 'source'
    with zipfile.ZipFile(archive) as bundle:
        bundle.extractall(baseline)
    env['PYTHONPATH'] = str(baseline / 'plugin/src')
    source_hashes = {str(p.relative_to(baseline)): hashlib.sha256(p.read_bytes()).hexdigest()
                     for p in (baseline / 'plugin/src').rglob('*.py')}
    summary = dict(baseline=BASELINE, archive_sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),
                   source_hashes=source_hashes, measurement='deterministic_fixture_not_native_model',
                   model=None, python=sys.executable,
                   case_manifest_sha256=hashlib.sha256((HERE / 'cases.json').read_bytes()).hexdigest(),
                   extension_test_sha256=hashlib.sha256((REPO / 'tests/test_native_gap_baseline.py').read_bytes()).hexdigest(), cases=[])
    for case in load_cases():
        start = time.monotonic()
        work = output / case['id']
        work.mkdir()
        for fixture in case['fixtures']:
            (work / fixture['source']).write_text(fixture['broken'], encoding='utf-8')
            (work / fixture['test']).write_bytes(fixture['test_source'].encode('utf-8'))
            assert hashlib.sha256((work / fixture['test']).read_bytes()).hexdigest() == fixture['provenance']['sha256']
        phases = []
        def business(phase, fixture):
            name = phase + '-' + fixture['source'].removesuffix('.py')
            xml = work / (name + '.xml')
            command = [sys.executable, '-B', '-m', 'pytest', '-q', '-W', 'error', '-p', 'no:cacheprovider',
                       '--basetemp', str(work / (name + '-tmp')), '--junitxml', str(xml), fixture['test']]
            code, elapsed = execute(command, work, work / (name + '.log'), env)
            counts = junit_counts(xml)
            expected = (code == 1 and counts['failures'] > 0 and counts['errors'] == 0) if phase == 'red' else (
                code == 0 and counts['failures'] == counts['errors'] == counts['skipped'] == 0)
            assert expected, (case['id'], phase, fixture['source'], code, counts)
            return dict(phase=phase, fixture=fixture['source'], exit_code=code,
                        elapsed_seconds=elapsed, counts=counts, log=str(work / (name + '.log')))
        for phase in ('red', 'green'):
            if phase == 'green':
                for fixture in case['fixtures']:
                    (work / fixture['source']).write_text(fixture['repaired'], encoding='utf-8')
            if case['id'] == 'swarm-parallel-messages':
                with ThreadPoolExecutor(max_workers=2) as pool:
                    phases.extend(pool.map(lambda f: business(phase, f), case['fixtures']))
            else:
                phases.extend(business(phase, f) for f in case['fixtures'])
        contract_xml = work / 'contracts.xml'
        command = [sys.executable, '-B', '-m', 'pytest', '-q', '-W', 'error', '-p', 'no:cacheprovider',
                   '-s', '-c', str(baseline / 'pyproject.toml'), '--basetemp', str(work / 'contracts-tmp'), '--junitxml', str(contract_xml),
                   *[str(REPO / 'tests/test_native_gap_baseline.py') + '::' + node.removeprefix('@m0:') if node.startswith('@m0:') else node for node in case['contracts']]]
        code, elapsed = execute(command, baseline, work / 'contracts.log', env)
        counts = junit_counts(contract_xml)
        assert code == 0 and counts['tests'] == len(case['contracts']) and counts['failures'] == counts['errors'] == counts['skipped'] == 0, (case['id'], code, counts)
        row = dict(id=case['id'], correct_fixture_completion=True,
                   injected_fault=case['fault'], actual_failed_testcases=sum(p['counts']['failures'] for p in phases if p['phase'] == 'red'),
                   scripted_fixture_rework_rounds=1, business_phases=phases,
                   contract_counts=counts, contract_elapsed_seconds=elapsed,
                   total_elapsed_seconds=time.monotonic() - start, **unavailable_metrics())
        summary['cases'].append(row)
        (output / 'results.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
        print(f"{case['id']}: RED={row['actual_failed_testcases']} failed testcases; GREEN; contracts={counts['tests']}", flush=True)
    assert source_hashes == {str(p.relative_to(baseline)): hashlib.sha256(p.read_bytes()).hexdigest()
                             for p in (baseline / 'plugin/src').rglob('*.py')}, 'Frozen product source changed'
    return summary


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True, help='New external absolute evidence directory')
    args = parser.parse_args()
    if not args.output.is_absolute() or args.output.resolve().is_relative_to(REPO):
        parser.error('--output must be an absolute directory outside the repository')
    run(args.output.resolve())
