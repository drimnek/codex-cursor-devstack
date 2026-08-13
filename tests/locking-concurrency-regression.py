#!/usr/bin/python3
"""Deterministic multi-process regression checks for broker locking semantics."""
from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
AGENTD = ROOT / 'platform-src' / 'bin' / 'agentd'
PROJECT = 'locking-regression'


def load_agentd():
    loader = importlib.machinery.SourceFileLoader('agentd_locking_test', str(AGENTD))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    if spec is None:
        raise RuntimeError('cannot load agentd')
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ['git', '-C', str(repo), *args],
        check=check,
        text=True,
        capture_output=True,
    )


def git_text(repo: Path, *args: str) -> str:
    return git(repo, *args).stdout.strip()


def commit_file(repo: Path, rel: str, content: str, message: str) -> str:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    git(repo, 'add', rel)
    git(repo, 'commit', '-q', '-m', message)
    return git_text(repo, 'rev-parse', 'HEAD')


def create_project(root: Path) -> tuple[dict, Path]:
    project = root / 'projects' / PROJECT
    for rel in [
        'repo/agent', 'worktrees', 'tasks', 'reference', 'results', 'runtime',
        'exchange/inbound', 'exchange/outbound',
    ]:
        (project / rel).mkdir(parents=True, exist_ok=True)
    (project / 'project.json').write_text('{"project":"locking-regression"}\n')

    repo = project / 'repo' / 'agent'
    git(repo, 'init', '-q', '-b', 'agent/integration')
    git(repo, 'config', 'user.name', 'Locking Regression')
    git(repo, 'config', 'user.email', 'locking@example.invalid')
    (repo / 'README.md').write_text('base\n')
    git(repo, 'add', 'README.md')
    git(repo, 'commit', '-q', '-m', 'base')
    return {'root': str(root)}, repo


def start_parallel(agentd, cfg: dict, task: str) -> dict:
    return agentd.op_task_start(cfg, {
        'op': 'task-start', 'project': PROJECT, 'task': task,
        'parallel': True, 'dependencies': [],
    })


def complete(agentd, cfg: dict, task: str) -> dict:
    return agentd.op_task_complete(cfg, {'op': 'task-complete', 'project': PROJECT, 'task': task})


def task_record(root: Path, task: str) -> dict:
    path = root / 'projects' / PROJECT / 'tasks' / f'{task}.json'
    return json.loads(path.read_text())


def wait_for(path: Path, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return
        time.sleep(0.02)
    raise AssertionError(f'timed out waiting for {path.name}')


def assert_not_created(path: Path, duration: float, label: str) -> None:
    deadline = time.monotonic() + duration
    while time.monotonic() < deadline:
        if path.exists():
            raise AssertionError(f'{label}: unexpectedly completed while conflicting lock was held')
        time.sleep(0.02)


def worker_lock(args: list[str]) -> int:
    root, lock_name, readonly_raw, attempting_raw, acquired_raw, release_raw = args
    agentd = load_agentd()
    pp = agentd.project_paths({'root': root}, PROJECT)
    attempting, acquired, release = map(Path, (attempting_raw, acquired_raw, release_raw))
    attempting.touch()
    with agentd.lock_one(pp, lock_name, readonly_raw == '1'):
        acquired.touch()
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if release.exists():
                return 0
            time.sleep(0.02)
        raise RuntimeError('timed out waiting for lock release marker')


def worker_merge(args: list[str]) -> int:
    root, task, attempting_raw, finished_raw, result_raw = args
    agentd = load_agentd()
    attempting, finished, result = map(Path, (attempting_raw, finished_raw, result_raw))
    attempting.touch()
    started = time.monotonic()
    payload: dict
    try:
        rec = agentd.op_task_merge({'root': root}, {'op': 'task-merge', 'project': PROJECT, 'task': task})
        payload = {'status': 'ok', 'task': task, 'record': rec, 'elapsed': time.monotonic() - started}
    except Exception as exc:  # noqa: BLE001 - worker reports broker result to parent
        payload = {'status': 'error', 'task': task, 'error': str(exc), 'elapsed': time.monotonic() - started}
    result.write_text(json.dumps(payload) + '\n')
    finished.touch()
    return 0


def spawn(*args: str) -> subprocess.Popen[str]:
    return subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve()), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def finish(proc: subprocess.Popen[str], timeout: float = 5.0) -> None:
    stdout, stderr = proc.communicate(timeout=timeout)
    assert proc.returncode == 0, (proc.returncode, stdout, stderr)


def main() -> None:
    if os.name != 'posix':
        raise RuntimeError('locking regression requires POSIX flock semantics')

    agentd = load_agentd()

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        signals = root / 'signals'
        signals.mkdir()
        cfg, integration = create_project(root)
        pp = agentd.project_paths(cfg, PROJECT)

        # 1. Different task locks are independent.
        a_attempt, a_acquired, a_release = [signals / f'a-{name}' for name in ('attempt', 'acquired', 'release')]
        a = spawn('--lock-worker', str(root), 'TASK-A', '0', str(a_attempt), str(a_acquired), str(a_release))
        wait_for(a_attempt); wait_for(a_acquired)

        b_attempt, b_acquired, b_release = [signals / f'b-{name}' for name in ('attempt', 'acquired', 'release')]
        b = spawn('--lock-worker', str(root), 'TASK-B', '0', str(b_attempt), str(b_acquired), str(b_release))
        wait_for(b_attempt); wait_for(b_acquired)
        b_release.touch(); finish(b)
        a_release.touch(); finish(a)

        # 2. Same-task exclusive locks serialize.
        s1_attempt, s1_acquired, s1_release = [signals / f's1-{name}' for name in ('attempt', 'acquired', 'release')]
        s1 = spawn('--lock-worker', str(root), 'TASK-SERIAL', '0', str(s1_attempt), str(s1_acquired), str(s1_release))
        wait_for(s1_attempt); wait_for(s1_acquired)

        s2_attempt, s2_acquired, s2_release = [signals / f's2-{name}' for name in ('attempt', 'acquired', 'release')]
        s2 = spawn('--lock-worker', str(root), 'TASK-SERIAL', '0', str(s2_attempt), str(s2_acquired), str(s2_release))
        wait_for(s2_attempt)
        assert_not_created(s2_acquired, 0.3, 'same-task exclusive lock')
        s1_release.touch(); finish(s1)
        wait_for(s2_acquired)
        s2_release.touch(); finish(s2)

        # 3. Shared readonly locks coexist; an exclusive writer waits for all readers.
        r1_attempt, r1_acquired, r1_release = [signals / f'r1-{name}' for name in ('attempt', 'acquired', 'release')]
        r1 = spawn('--lock-worker', str(root), 'TASK-READ', '1', str(r1_attempt), str(r1_acquired), str(r1_release))
        wait_for(r1_attempt); wait_for(r1_acquired)

        r2_attempt, r2_acquired, r2_release = [signals / f'r2-{name}' for name in ('attempt', 'acquired', 'release')]
        r2 = spawn('--lock-worker', str(root), 'TASK-READ', '1', str(r2_attempt), str(r2_acquired), str(r2_release))
        wait_for(r2_attempt); wait_for(r2_acquired)

        w_attempt, w_acquired, w_release = [signals / f'w-{name}' for name in ('attempt', 'acquired', 'release')]
        writer = spawn('--lock-worker', str(root), 'TASK-READ', '0', str(w_attempt), str(w_acquired), str(w_release))
        wait_for(w_attempt)
        assert_not_created(w_acquired, 0.3, 'writer behind readonly locks')
        r1_release.touch(); finish(r1)
        assert_not_created(w_acquired, 0.2, 'writer while second readonly lock remains')
        r2_release.touch(); finish(r2)
        wait_for(w_acquired)
        w_release.touch(); finish(writer)

        # 4. task-merge waits for the task lock and cannot mutate integration while it is held.
        c = start_parallel(agentd, cfg, 'REQ-LOCK-C')
        wc = Path(c['workspace'])
        commit_file(wc, 'C.txt', 'C\n', 'C')
        complete(agentd, cfg, 'REQ-LOCK-C')
        before_c = git_text(integration, 'rev-parse', 'HEAD')

        with agentd.lock_one(pp, 'REQ-LOCK-C', False):
            c_attempt, c_finished, c_result = [signals / f'c-{name}' for name in ('attempt', 'finished', 'result')]
            cm = spawn('--merge-worker', str(root), 'REQ-LOCK-C', str(c_attempt), str(c_finished), str(c_result))
            wait_for(c_attempt)
            assert_not_created(c_finished, 0.3, 'merge behind task lock')
            assert git_text(integration, 'rev-parse', 'HEAD') == before_c
            assert task_record(root, 'REQ-LOCK-C')['status'] == 'completed'
            assert wc.exists()

        wait_for(c_finished); finish(cm)
        c_payload = json.loads(c_result.read_text())
        assert c_payload['status'] == 'ok', c_payload
        assert task_record(root, 'REQ-LOCK-C')['status'] == 'merged'
        assert (integration / 'C.txt').read_text() == 'C\n'

        # 5. Integration lock serializes independent merges. Both queue behind the same lock,
        # then complete one-at-a-time without losing either change.
        d = start_parallel(agentd, cfg, 'REQ-LOCK-D')
        e = start_parallel(agentd, cfg, 'REQ-LOCK-E')
        wd, we = Path(d['workspace']), Path(e['workspace'])
        commit_file(wd, 'D.txt', 'D\n', 'D')
        commit_file(we, 'E.txt', 'E\n', 'E')
        complete(agentd, cfg, 'REQ-LOCK-D')
        complete(agentd, cfg, 'REQ-LOCK-E')
        before_de = git_text(integration, 'rev-parse', 'HEAD')

        with agentd.lock_one(pp, 'integration', False):
            d_attempt, d_finished, d_result = [signals / f'd-{name}' for name in ('attempt', 'finished', 'result')]
            e_attempt, e_finished, e_result = [signals / f'e-{name}' for name in ('attempt', 'finished', 'result')]
            dm = spawn('--merge-worker', str(root), 'REQ-LOCK-D', str(d_attempt), str(d_finished), str(d_result))
            em = spawn('--merge-worker', str(root), 'REQ-LOCK-E', str(e_attempt), str(e_finished), str(e_result))
            wait_for(d_attempt); wait_for(e_attempt)
            assert_not_created(d_finished, 0.3, 'first merge behind integration lock')
            assert_not_created(e_finished, 0.3, 'second merge behind integration lock')
            assert git_text(integration, 'rev-parse', 'HEAD') == before_de

        wait_for(d_finished, 8); wait_for(e_finished, 8)
        finish(dm); finish(em)
        payloads = [json.loads(d_result.read_text()), json.loads(e_result.read_text())]
        assert all(item['status'] == 'ok' for item in payloads), payloads
        assert task_record(root, 'REQ-LOCK-D')['status'] == 'merged'
        assert task_record(root, 'REQ-LOCK-E')['status'] == 'merged'
        assert (integration / 'D.txt').read_text() == 'D\n'
        assert (integration / 'E.txt').read_text() == 'E\n'
        assert git_text(integration, 'status', '--porcelain') == ''

        # 6. Atomic metadata writes leave no temporary files after concurrent lifecycle operations.
        temp_files = list((root / 'projects' / PROJECT / 'tasks').glob('.*.tmp'))
        assert temp_files == [], temp_files

    print('locking and concurrency regression checks passed')


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == '--lock-worker':
        raise SystemExit(worker_lock(sys.argv[2:]))
    if len(sys.argv) > 1 and sys.argv[1] == '--merge-worker':
        raise SystemExit(worker_merge(sys.argv[2:]))
    main()
