#!/usr/bin/python3
"""Deterministic regression checks for parallel task/worktree lifecycle."""
from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import subprocess
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
AGENTD = ROOT / 'platform-src' / 'bin' / 'agentd'
PROJECT = 'parallel-regression'


def load_agentd():
    loader = importlib.machinery.SourceFileLoader('agentd_parallel_test', str(AGENTD))
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


def task_record(root: Path, task: str) -> dict:
    path = root / 'projects' / PROJECT / 'tasks' / f'{task}.json'
    return json.loads(path.read_text())


def assert_raises(message: str, func, *args, contains: str | None = None) -> Exception:
    try:
        func(*args)
    except Exception as exc:  # noqa: BLE001 - regression harness validates broker errors
        if contains is not None:
            assert contains in str(exc), (message, str(exc))
        return exc
    raise AssertionError(f'{message}: expected an exception')


def branch_exists(repo: Path, branch: str) -> bool:
    return git(repo, 'show-ref', '--verify', '--quiet', f'refs/heads/{branch}', check=False).returncode == 0


def create_project(root: Path) -> tuple[dict, Path]:
    project = root / 'projects' / PROJECT
    for rel in [
        'repo/agent', 'worktrees', 'tasks', 'reference', 'results', 'runtime',
        'exchange/inbound', 'exchange/outbound',
    ]:
        (project / rel).mkdir(parents=True, exist_ok=True)
    (project / 'project.json').write_text('{"project":"parallel-regression"}\n')

    repo = project / 'repo' / 'agent'
    git(repo, 'init', '-q', '-b', 'agent/integration')
    git(repo, 'config', 'user.name', 'Parallel Regression')
    git(repo, 'config', 'user.email', 'parallel@example.invalid')
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


def merge(agentd, cfg: dict, task: str) -> dict:
    return agentd.op_task_merge(cfg, {'op': 'task-merge', 'project': PROJECT, 'task': task})


def abort(agentd, cfg: dict, task: str) -> dict:
    return agentd.op_task_abort(cfg, {'op': 'task-abort', 'project': PROJECT, 'task': task})


def main() -> None:
    agentd = load_agentd()

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        cfg, integration = create_project(root)
        base = git_text(integration, 'rev-parse', 'HEAD')

        # 1-2. Two parallel tasks share the same base but have isolated branches/worktrees.
        a = start_parallel(agentd, cfg, 'REQ-PAR-001')
        b = start_parallel(agentd, cfg, 'REQ-PAR-002')
        assert a['status'] == b['status'] == 'active'
        assert a['mode'] == b['mode'] == 'parallel'
        assert a['base_commit'] == b['base_commit'] == base
        assert a['branch'] != b['branch']
        assert a['workspace'] != b['workspace']
        wa, wb = Path(a['workspace']), Path(b['workspace'])
        assert wa.is_dir() and wb.is_dir()

        (wa / 'A.txt').write_text('A\n')
        assert not (wb / 'A.txt').exists()
        assert not (integration / 'A.txt').exists()
        (wb / 'B.txt').write_text('B\n')
        assert not (wa / 'B.txt').exists()
        assert not (integration / 'B.txt').exists()

        # Dirty task completion is rejected.
        assert_raises(
            'dirty parallel task completion', complete, agentd, cfg, 'REQ-PAR-001',
            contains='uncommitted or untracked changes',
        )

        git(wa, 'add', 'A.txt')
        git(wa, 'commit', '-q', '-m', 'parallel A')
        head_a = git_text(wa, 'rev-parse', 'HEAD')
        git(wb, 'add', 'B.txt')
        git(wb, 'commit', '-q', '-m', 'parallel B')
        head_b = git_text(wb, 'rev-parse', 'HEAD')

        ca = complete(agentd, cfg, 'REQ-PAR-001')
        cb = complete(agentd, cfg, 'REQ-PAR-002')
        assert ca['status'] == cb['status'] == 'completed'
        assert ca['head_commit'] == head_a
        assert cb['head_commit'] == head_b
        assert ca['completed_at'] and cb['completed_at']

        # 3. Successful merges integrate one completed task at a time and clean up worktrees/branches.
        ma = merge(agentd, cfg, 'REQ-PAR-001')
        assert ma['status'] == 'merged' and ma['merge_commit']
        assert (integration / 'A.txt').read_text() == 'A\n'
        assert not (integration / 'B.txt').exists()
        assert not wa.exists()
        assert not branch_exists(integration, a['branch'])

        mb = merge(agentd, cfg, 'REQ-PAR-002')
        assert mb['status'] == 'merged' and mb['merge_commit']
        assert (integration / 'A.txt').read_text() == 'A\n'
        assert (integration / 'B.txt').read_text() == 'B\n'
        assert not wb.exists()
        assert not branch_exists(integration, b['branch'])
        assert git_text(integration, 'status', '--porcelain') == ''

        # 4. Completed task HEAD is immutable: post-completion branch drift blocks merge.
        c = start_parallel(agentd, cfg, 'REQ-PAR-DRIFT')
        wc = Path(c['workspace'])
        recorded_c = commit_file(wc, 'C.txt', 'recorded\n', 'record C')
        cc = complete(agentd, cfg, 'REQ-PAR-DRIFT')
        assert cc['head_commit'] == recorded_c
        drift_c = commit_file(wc, 'C.txt', 'drifted\n', 'drift C')
        assert drift_c != recorded_c
        integration_before_drift_merge = git_text(integration, 'rev-parse', 'HEAD')
        assert_raises(
            'completed task branch drift', merge, agentd, cfg, 'REQ-PAR-DRIFT',
            contains='branch changed after completion',
        )
        assert git_text(integration, 'rev-parse', 'HEAD') == integration_before_drift_merge
        assert task_record(root, 'REQ-PAR-DRIFT')['status'] == 'completed'
        ac = abort(agentd, cfg, 'REQ-PAR-DRIFT')
        assert ac['status'] == 'aborted'
        assert not wc.exists()
        assert not branch_exists(integration, c['branch'])

        # 5. Active abort discards even a dirty worktree without touching integration.
        d = start_parallel(agentd, cfg, 'REQ-PAR-ABORT-ACTIVE')
        wd = Path(d['workspace'])
        (wd / 'discard-me.txt').write_text('dirty\n')
        integration_before_abort = git_text(integration, 'rev-parse', 'HEAD')
        ad = abort(agentd, cfg, 'REQ-PAR-ABORT-ACTIVE')
        assert ad['status'] == 'aborted'
        assert not wd.exists()
        assert not branch_exists(integration, d['branch'])
        assert git_text(integration, 'rev-parse', 'HEAD') == integration_before_abort
        assert_raises('repeat abort', abort, agentd, cfg, 'REQ-PAR-ABORT-ACTIVE', contains='task is aborted')
        assert_raises('merge aborted task', merge, agentd, cfg, 'REQ-PAR-ABORT-ACTIVE', contains='task is aborted')

        # 6. Merge conflict is rolled back: integration stays clean/unchanged; losing task remains completed.
        e = start_parallel(agentd, cfg, 'REQ-PAR-CONFLICT-A')
        f = start_parallel(agentd, cfg, 'REQ-PAR-CONFLICT-B')
        we, wf = Path(e['workspace']), Path(f['workspace'])
        commit_file(we, 'README.md', 'from A\n', 'conflict A')
        commit_file(wf, 'README.md', 'from B\n', 'conflict B')
        complete(agentd, cfg, 'REQ-PAR-CONFLICT-A')
        complete(agentd, cfg, 'REQ-PAR-CONFLICT-B')
        merge(agentd, cfg, 'REQ-PAR-CONFLICT-A')
        assert (integration / 'README.md').read_text() == 'from A\n'
        integration_before_conflict = git_text(integration, 'rev-parse', 'HEAD')
        assert_raises(
            'conflicting merge', merge, agentd, cfg, 'REQ-PAR-CONFLICT-B',
            contains='merge failed or conflicted',
        )
        assert git_text(integration, 'rev-parse', 'HEAD') == integration_before_conflict
        assert git_text(integration, 'status', '--porcelain') == ''
        assert git(integration, 'rev-parse', '-q', '--verify', 'MERGE_HEAD', check=False).returncode != 0
        assert task_record(root, 'REQ-PAR-CONFLICT-B')['status'] == 'completed'
        assert wf.is_dir()
        abort(agentd, cfg, 'REQ-PAR-CONFLICT-B')

        # Merged task metadata remains auditable; all temporary worktrees are gone.
        records = {rec['task']: rec for rec in agentd.op_task_list(cfg, {'op': 'task-list', 'project': PROJECT})}
        assert records['REQ-PAR-001']['status'] == 'merged'
        assert records['REQ-PAR-002']['status'] == 'merged'
        assert records['REQ-PAR-DRIFT']['status'] == 'aborted'
        assert records['REQ-PAR-ABORT-ACTIVE']['status'] == 'aborted'
        assert records['REQ-PAR-CONFLICT-A']['status'] == 'merged'
        assert records['REQ-PAR-CONFLICT-B']['status'] == 'aborted'
        assert list((root / 'projects' / PROJECT / 'worktrees').iterdir()) == []

    print('parallel lifecycle regression checks passed')


if __name__ == '__main__':
    main()
