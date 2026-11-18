"""Offline tests for status reporting and --apply in bin/agent_update.py.

Every external command goes through a fake ``sh``; nothing touches the network
or a live agent config.

Run with: uv run --with pytest pytest bin/tests/test_agent_update_engine.py
"""

from __future__ import annotations

import importlib.util
import sys
import tomllib
from pathlib import Path

import pytest
from agentsync.common import build_context


BIN = Path(__file__).resolve().parents[1]
REPO = BIN.parent


def of[T](updates: tuple[object, ...], kind: type[T]) -> tuple[T, ...]:
    """The updates of one kind, in check order."""
    return tuple(u for u in updates if isinstance(u, kind))


def load():
    spec = importlib.util.spec_from_file_location('agent_update', BIN / 'agent_update.py')
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules['agent_update'] = module  # @dataclass resolves its module by name
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def up(monkeypatch):
    module = load()

    def no_real_run(cmd, **kw):
        raise AssertionError(f'test reached a real subprocess: {cmd}')

    # A test that needs subprocess.run (resync) patches it again itself.
    monkeypatch.setattr(module.subprocess, 'run', no_real_run)
    return module


def record_resync(up, monkeypatch, ok: bool = True) -> list:
    """Patch ``resync``; the returned list grows by one per re-sync."""
    runs: list = []
    monkeypatch.setattr(up, 'resync', lambda: runs.append(1) or ok)
    return runs


def fake_sh(up, monkeypatch, responses: dict[str, tuple[int, str, str]]) -> list:
    """Patch ``sh``: the first key found in the joined argv picks the result."""
    calls: list = []

    def sh(cmd, cwd=None):
        calls.append((list(cmd), cwd))
        line = ' '.join(cmd)
        for key, (rc, out, err) in responses.items():
            if key in line:
                return up.subprocess.CompletedProcess(cmd, rc, out, err)
        return up.subprocess.CompletedProcess(cmd, 1, '', '')

    monkeypatch.setattr(up, 'sh', sh)
    return calls


def repo_responses(**over: tuple[int, str, str]) -> dict[str, tuple[int, str, str]]:
    """A clean checkout of main tracking origin/main, 0 behind."""
    base = {
        'is-inside-work-tree': (0, 'true\n', ''),
        'fetch --quiet': (0, '', ''),
        'symbolic-ref': (0, 'origin/main\n', ''),
        '--abbrev-ref HEAD': (0, 'main\n', ''),
        '@{u}': (0, 'origin/main\n', ''),
        'rev-list --count': (0, '0\n', ''),
    }
    return {**over, **{k: v for k, v in base.items() if k not in over}}


def git_pkg(path: Path) -> dict:
    return {'manager': 'git', 'repo': 'o/x', 'path': str(path)}


def server(up, pkg: dict, ctx: dict | None = None, name: str = 'x'):
    """The catalog ``Server`` for ``pkg``, resolved through ``ctx``."""
    return up.Server.from_catalog(name, Path(f'{name}.toml'), pkg, {} if ctx is None else ctx)


def at(up, path: Path, build: list[list[str]] | None = None, name: str = 'x'):
    """A ``Server`` checked out at ``path`` with ``build`` steps (apply-side records)."""
    return up.Server(name, Path(f'{name}.toml'), {}, str(path), tuple(map(tuple, build or [])))


# --------------------------------------------------------------------------- #
# M4: re-sync goes through bash and reports failure
# --------------------------------------------------------------------------- #
def test_resync_runs_agent_sync_through_bash(up, monkeypatch):
    seen = []
    monkeypatch.setattr(
        up.subprocess, 'run', lambda cmd, **kw: seen.append(cmd) or up.subprocess.CompletedProcess(cmd, 0)
    )
    assert up.resync() is True
    cmd = seen[0]
    assert Path(cmd[0]).stem.lower() == 'bash'
    assert Path(cmd[1]) == REPO / 'bin' / 'agent-sync'


def test_resync_failure_is_an_apply_failure(up, monkeypatch, tmp_path):
    cat = tmp_path / 'x.toml'
    cat.write_text('[package]\nref = "1.0"\n')
    monkeypatch.setattr(up.subprocess, 'run', lambda cmd, **kw: up.subprocess.CompletedProcess(cmd, 2))
    plan = up.Plan(updates=(up.CatalogBump(up.Server('x', cat, {}), '1.0', '1.1'),))
    failures = up.apply_updates(plan)
    assert any('agent-sync' in f for f in failures)


# --------------------------------------------------------------------------- #
# M5: footer counts uv-project bumps
# --------------------------------------------------------------------------- #
def test_footer_counts_uv_project_bumps(up, tmp_path):
    plan = up.Plan(updates=(up.ProjectBump(up.Server('x', tmp_path / 'x.toml', {}, str(tmp_path)), 'v1', 'v2'),))
    assert 'apply' in up.report_footer(plan, fetched=True)
    assert up.report_footer(up.Plan(), fetched=True) == 'everything is current'


def test_footer_with_no_fetch_does_not_claim_current(up):
    footer = up.report_footer(up.Plan(), fetched=False)
    assert footer == 'nothing pending (remotes not checked: --no-fetch)'
    assert 'current' not in footer


# --------------------------------------------------------------------------- #
# --apply re-syncs whenever something was applied
# --------------------------------------------------------------------------- #
def test_successful_pull_triggers_resync(up, monkeypatch, tmp_path):
    fake_sh(up, monkeypatch, {'pull': (0, 'Fast-forward\n', '')})
    runs = record_resync(up, monkeypatch)
    assert up.apply_updates(up.Plan(updates=(up.Pull(at(up, tmp_path), 'main'),))) == []
    assert runs == [1]


def test_uv_tool_upgrade_triggers_resync(up, monkeypatch):
    fake_sh(up, monkeypatch, {'upgrade': (0, 'Updated t\n', '')})
    runs = record_resync(up, monkeypatch)
    assert up.apply_updates(up.Plan(updates=(up.UvUpgrade('t'),))) == []
    assert runs == [1]


def test_nothing_applied_skips_resync(up, monkeypatch):
    runs = record_resync(up, monkeypatch)
    assert up.apply_updates(up.Plan()) == []
    assert runs == []


def test_only_failed_steps_skip_resync(up, monkeypatch):
    fake_sh(up, monkeypatch, {'upgrade': (1, '', 'boom\n')})
    runs = record_resync(up, monkeypatch)
    assert up.apply_updates(up.Plan(updates=(up.UvUpgrade('t'),))) == ['uv tool upgrade t: boom']
    assert runs == []


def test_resync_failure_after_pull_is_a_failure(up, monkeypatch, tmp_path):
    fake_sh(up, monkeypatch, {'pull': (0, 'Fast-forward\n', '')})
    record_resync(up, monkeypatch, ok=False)
    failures = up.apply_updates(up.Plan(updates=(up.Pull(at(up, tmp_path), 'main'),)))
    assert failures == ['re-sync failed: bin/agent-sync exited nonzero']


# --------------------------------------------------------------------------- #
# M6: a failed fetch is reported, never "up to date"
# --------------------------------------------------------------------------- #
def test_failed_fetch_is_reported(up, monkeypatch, tmp_path):
    fake_sh(
        up, monkeypatch, repo_responses(**{'fetch --quiet': (128, '', 'fatal: could not read from remote\nmore\n')})
    )
    status = up.git_status(server(up, git_pkg(tmp_path)), up.Check(fetch=True))
    assert status.text.startswith('fetch failed: fatal: could not read from remote')
    assert 'up to date' not in status.text
    assert status.updates == ()


# --------------------------------------------------------------------------- #
# L4: apply failures are collected
# --------------------------------------------------------------------------- #
def test_clone_and_pull_failures_are_collected(up, monkeypatch, tmp_path):
    fake_sh(up, monkeypatch, {'clone': (128, '', 'Permission denied\n'), 'pull': (1, '', 'not ff\n')})
    clone = up.Clone(up.Server('a', Path('a.toml'), {'repo': 'o/a'}, str(tmp_path / 'a')))
    plan = up.Plan(updates=(clone, up.Pull(at(up, tmp_path, name='b'), 'main')))
    failures = up.apply_updates(plan)
    assert len(failures) == 2
    assert any('Permission denied' in f for f in failures)


def test_uv_lock_failure_is_collected(up, monkeypatch, tmp_path):
    fake_sh(up, monkeypatch, {'lock': (2, '', 'resolution failed\n')})
    proj = tmp_path / 'proj'
    proj.mkdir()
    (proj / 'pyproject.toml').write_text('dep @ git+https://x@v1#egg\n')
    cat = tmp_path / 'x.toml'
    cat.write_text('[package]\nref = "v1"\n')
    plan = up.Plan(updates=(up.ProjectBump(up.Server('x', cat, {}, str(proj)), 'v1', 'v2'),))
    failures = up.apply_updates(plan)
    assert any('resolution failed' in f for f in failures)


def test_uv_tool_upgrade_failure_is_collected(up, monkeypatch):
    fake_sh(up, monkeypatch, {'upgrade': (1, '', 'boom\n')})
    assert up.apply_updates(up.Plan(updates=(up.UvUpgrade('t'),))) == ['uv tool upgrade t: boom']


# --------------------------------------------------------------------------- #
# L5: build steps run after a clone or pull
# --------------------------------------------------------------------------- #
def test_build_steps_accept_argv_lists_and_strings(up):
    ctx = {'commands': {'npm': ['cmd', '/c', 'npm']}}
    pkg = {'build': [['npm', 'ci'], 'npm run build']}
    assert up.build_steps(pkg, ctx) == (('cmd', '/c', 'npm', 'ci'), ('cmd', '/c', 'npm', 'run', 'build'))
    assert up.build_steps({}, ctx) == ()


def test_build_npm_honours_local_commands_mapping(up):
    ctx = build_context({'commands': {'npm': ['C:/node/npm.cmd', '--no-fund']}})
    steps = up.build_steps({'build': [['npm', 'ci']]}, ctx)
    assert steps == (('C:/node/npm.cmd', '--no-fund', 'ci'),)


def test_unmapped_npm_resolves_through_which(up, monkeypatch, tmp_path):
    shim = r'C:\Program Files\nodejs\npm.cmd'
    monkeypatch.setattr(up.shutil, 'which', lambda name, path=None: shim if name == 'npm' else None)
    seen = []
    monkeypatch.setattr(
        up.subprocess, 'run', lambda argv, **kw: seen.append(argv) or up.subprocess.CompletedProcess(argv, 0)
    )
    ctx = {'commands': {}}  # nothing maps npm
    for argv in up.build_steps({'build': [['npm', 'ci']]}, ctx):
        up.sh(argv, cwd=str(tmp_path))
    assert seen == [[shim, 'ci']]


def test_build_steps_reject_bad_shape(up):
    with pytest.raises(ValueError, match='build'):
        up.build_steps({'build': [3]}, {'commands': {}})


def test_clone_then_build_in_package_path(up, monkeypatch, tmp_path):
    marker = tmp_path / 'marker'
    calls = fake_sh(
        up,
        monkeypatch,
        {  # the tmp path contains "clone", so the rev-parse keys come first
            '--git-path': (0, f'{marker}\n', ''),
            'rev-parse HEAD': (0, 'abc123\n', ''),
            'clone': (0, '', ''),
            'npm': (0, 'ok\n', ''),
        },
    )
    record_resync(up, monkeypatch)
    dest = tmp_path / 'vendor' / 'x'
    pkg = {**git_pkg(dest), 'build': [['npm', 'ci']]}
    status = up.git_status(server(up, pkg, {'commands': {}}), up.Check(fetch=False))
    assert up.apply_updates(up.Plan(updates=status.updates)) == []
    assert (['npm', 'ci'], str(dest)) in calls
    assert marker.read_text().strip() == 'abc123'


def test_build_after_pull_and_failure_counts(up, monkeypatch, tmp_path):
    calls = fake_sh(up, monkeypatch, {'pull': (0, 'Fast-forward\n', ''), 'npm': (1, '', 'ERR! missing\n')})
    record_resync(up, monkeypatch)
    plan = up.Plan(updates=(up.Pull(at(up, tmp_path, [['npm', 'ci'], ['npm', 'run', 'build']]), 'main'),))
    failures = up.apply_updates(plan)
    assert failures == ['x: build `npm ci` failed: ERR! missing']
    assert (['npm', 'run', 'build'], str(tmp_path)) not in calls  # stops at the first failing step


def test_no_build_when_clone_fails(up, monkeypatch, tmp_path):
    calls = fake_sh(up, monkeypatch, {'clone': (128, '', 'denied\n')})
    dest = up.Server('x', Path('x.toml'), {'repo': 'o/x'}, str(tmp_path / 'x'), (('npm', 'ci'),))
    plan = up.Plan(updates=(up.Clone(dest),))
    up.apply_updates(plan)
    assert not any(cmd[0] == 'npm' for cmd, _ in calls)


def test_spotify_catalog_declares_build():
    pkg = tomllib.loads((REPO / 'catalog' / 'mcp' / 'spotify.toml').read_text())['package']
    assert pkg['build'] == [['npm', 'ci'], ['npm', 'run', 'build']]


# --------------------------------------------------------------------------- #
# L6: an up-to-date tag pin is not manual work
# --------------------------------------------------------------------------- #
def test_current_tag_pin_is_not_queued(up, monkeypatch, tmp_path):
    fake_sh(
        up,
        monkeypatch,
        {
            'is-inside-work-tree': (0, 'true\n', ''),
            '--abbrev-ref HEAD': (0, 'HEAD\n', ''),
            'fetch --quiet': (0, '', ''),
            'describe': (0, 'v1.2\n', ''),
            'ls-remote': (0, 'abc\trefs/tags/v1.2\nabd\trefs/tags/v1.1\n', ''),
        },
    )
    status = up.git_status(server(up, git_pkg(tmp_path)), up.Check(fetch=True))
    assert status.text == 'tag v1.2 (up to date)'
    assert of(status.updates, up.Manual) == ()


def test_stale_tag_pin_is_queued(up, monkeypatch, tmp_path):
    fake_sh(
        up,
        monkeypatch,
        {
            'is-inside-work-tree': (0, 'true\n', ''),
            '--abbrev-ref HEAD': (0, 'HEAD\n', ''),
            'fetch --quiet': (0, '', ''),
            'describe': (0, 'v1.1\n', ''),
            'ls-remote': (0, 'abc\trefs/tags/v1.2\nabd\trefs/tags/v1.1\n', ''),
        },
    )
    status = up.git_status(server(up, git_pkg(tmp_path)), up.Check(fetch=True))
    assert status.updates == (up.Manual(server(up, git_pkg(tmp_path))),)
    assert status.updates[0].server.checkout == str(tmp_path)


def test_no_fetch_tag_pin_skips_ls_remote(up, monkeypatch, tmp_path):
    calls = fake_sh(
        up,
        monkeypatch,
        {
            'is-inside-work-tree': (0, 'true\n', ''),
            '--abbrev-ref HEAD': (0, 'HEAD\n', ''),
            'describe': (0, 'v1.1\n', ''),
            'ls-remote': (0, 'abc\trefs/tags/v1.2\n', ''),
        },
    )
    status = up.git_status(server(up, git_pkg(tmp_path)), up.Check(fetch=False))
    assert status.text == 'tag v1.1 (not checked: --no-fetch)'
    assert not any('ls-remote' in cmd for cmd, _ in calls)
    assert not up.Plan(updates=status.updates).pending()


def test_failed_ls_remote_is_an_error_not_up_to_date(up, monkeypatch, tmp_path):
    fake_sh(
        up,
        monkeypatch,
        {
            'is-inside-work-tree': (0, 'true\n', ''),
            '--abbrev-ref HEAD': (0, 'HEAD\n', ''),
            'fetch --quiet': (0, '', ''),
            'describe': (0, 'v1.2\n', ''),
            'ls-remote': (128, '', 'fatal: could not read from remote repository\n'),
        },
    )
    status = up.git_status(server(up, git_pkg(tmp_path)), up.Check(fetch=True))
    assert status.text == f'ls-remote failed: fatal: could not read from remote repository ({tmp_path})'
    assert 'up to date' not in status.text


def test_full_clone_at_current_tag_compares_tags_not_origin_head(up, monkeypatch, tmp_path):
    fake_sh(
        up,
        monkeypatch,
        {
            'is-inside-work-tree': (0, 'true\n', ''),
            '--abbrev-ref HEAD': (0, 'HEAD\n', ''),
            'fetch --quiet': (0, '', ''),
            'describe': (0, 'v1.2\n', ''),
            'ls-remote': (0, 'abc\trefs/tags/v1.2\nabd\trefs/tags/v1.1\n', ''),
            '--verify --quiet origin/HEAD': (0, 'f00\n', ''),
            'rev-list --count': (0, '3\n', ''),
        },
    )
    status = up.git_status(server(up, git_pkg(tmp_path)), up.Check(fetch=True))
    assert status.text == 'tag v1.2 (up to date)'
    assert of(status.updates, up.Manual) == ()
    assert not up.Plan(updates=status.updates).pending()


def test_full_clone_at_stale_tag_reports_newer_tag(up, monkeypatch, tmp_path):
    fake_sh(
        up,
        monkeypatch,
        {
            'is-inside-work-tree': (0, 'true\n', ''),
            '--abbrev-ref HEAD': (0, 'HEAD\n', ''),
            'fetch --quiet': (0, '', ''),
            'describe': (0, 'v1.1\n', ''),
            'ls-remote': (0, 'abc\trefs/tags/v1.2\nabd\trefs/tags/v1.1\n', ''),
            '--verify --quiet origin/HEAD': (0, 'f00\n', ''),
            'rev-list --count': (0, '3\n', ''),
        },
    )
    status = up.git_status(server(up, git_pkg(tmp_path)), up.Check(fetch=True))
    assert status.text == 'tag v1.1 -> v1.2 (tag-pinned - manual)'
    assert status.updates == (up.Manual(server(up, git_pkg(tmp_path))),)


def test_detached_off_tag_still_compares_to_origin_head(up, monkeypatch, tmp_path):
    fake_sh(
        up,
        monkeypatch,
        {
            'is-inside-work-tree': (0, 'true\n', ''),
            '--abbrev-ref HEAD': (0, 'HEAD\n', ''),
            'fetch --quiet': (0, '', ''),
            'describe': (128, '', 'fatal: no tag exactly matches\n'),
            '--verify --quiet origin/HEAD': (0, 'f00\n', ''),
            'rev-list --count': (0, '3\n', ''),
        },
    )
    status = up.git_status(server(up, git_pkg(tmp_path)), up.Check(fetch=True))
    assert status.text == '3 commit(s) behind origin/HEAD'
    assert status.updates == (up.Manual(server(up, git_pkg(tmp_path))),)


# --------------------------------------------------------------------------- #
# L7: --no-fetch does not claim a uv project is current
# --------------------------------------------------------------------------- #
def test_uv_project_no_fetch_is_not_checked(up, monkeypatch, tmp_path):
    calls = fake_sh(up, monkeypatch, {})
    pkg = {'manager': 'uv-project', 'repo': 'o/x', 'ref': 'v1', 'path': str(tmp_path)}
    status = up.uv_project_status(server(up, pkg), up.Check(fetch=False))
    assert status == up.Status('v1 (not checked: --no-fetch)')
    assert calls == []


# --------------------------------------------------------------------------- #
# L8: manual packages
# --------------------------------------------------------------------------- #
def test_manual_manager_is_reported(up):
    status = up.server_status(server(up, {'manager': 'manual'}, name='s'), up.Check(fetch=False))
    assert status == up.Status('manual (not tracked)')
    assert status.updates == ()


def test_solidcraft_catalog_is_manual():
    raw = tomllib.loads((REPO / 'catalog' / 'mcp' / 'solidcraft.toml').read_text())
    assert raw['package']['manager'] == 'manual'


# --------------------------------------------------------------------------- #
# L9: warn when a clone is off its remote default branch
# --------------------------------------------------------------------------- #
def test_off_default_branch_is_flagged(up, monkeypatch, tmp_path):
    fake_sh(
        up,
        monkeypatch,
        repo_responses(
            **{'--abbrev-ref HEAD': (0, 'feat/idea-type\n', ''), '@{u}': (0, 'origin/feat/idea-type\n', '')}
        ),
    )
    status = up.git_status(server(up, git_pkg(tmp_path)), up.Check(fetch=True))
    assert 'on feat/idea-type, not main' in status.text


def test_default_branch_is_not_flagged(up, monkeypatch, tmp_path):
    fake_sh(up, monkeypatch, repo_responses())
    status = up.git_status(server(up, git_pkg(tmp_path)), up.Check(fetch=True))
    assert status == up.Status('main up to date')


# --------------------------------------------------------------------------- #
# Build retry: a build that has not succeeded at HEAD is planned again
# --------------------------------------------------------------------------- #
def built_repo(tmp_path: Path, marker: str | None) -> dict[str, tuple[int, str, str]]:
    """A clean, current checkout whose build marker holds ``marker`` (None = absent)."""
    git_dir = tmp_path / '.git'
    git_dir.mkdir(exist_ok=True)
    if marker is not None:
        (git_dir / 'agent-update-build').write_text(marker + '\n')
    return repo_responses(**{'--git-path': (0, '.git/agent-update-build\n', ''), 'rev-parse HEAD': (0, 'abc123\n', '')})


def build_pkg(path: Path) -> dict:
    return {**git_pkg(path), 'build': [['npm', 'ci']]}


def test_missing_build_marker_plans_build(up, monkeypatch, tmp_path):
    fake_sh(up, monkeypatch, built_repo(tmp_path, None))
    srv = server(up, build_pkg(tmp_path), {'commands': {}})
    status = up.git_status(srv, up.Check(fetch=True))
    assert status.text == 'main up to date (build pending)'
    assert status.updates == (up.Rebuild(srv),)
    assert srv.checkout == str(tmp_path)
    assert up.Plan(updates=status.updates).pending()


def test_stale_build_marker_plans_build(up, monkeypatch, tmp_path):
    fake_sh(up, monkeypatch, built_repo(tmp_path, 'old999'))
    srv = server(up, build_pkg(tmp_path), {'commands': {}})
    assert up.git_status(srv, up.Check(fetch=True)).updates == (up.Rebuild(srv),)


def test_current_build_marker_plans_nothing(up, monkeypatch, tmp_path):
    fake_sh(up, monkeypatch, built_repo(tmp_path, 'abc123'))
    status = up.git_status(server(up, build_pkg(tmp_path), {'commands': {}}), up.Check(fetch=True))
    assert status.text == 'main up to date'
    assert of(status.updates, up.Rebuild) == ()
    assert not up.Plan(updates=status.updates).pending()


def test_no_build_steps_never_checks_marker(up, monkeypatch, tmp_path):
    calls = fake_sh(up, monkeypatch, built_repo(tmp_path, None))
    assert up.git_status(server(up, git_pkg(tmp_path)), up.Check(fetch=True)).text == 'main up to date'
    assert not any('--git-path' in ' '.join(cmd) for cmd, _ in calls)


def test_pending_build_runs_and_records_marker(up, monkeypatch, tmp_path):
    calls = fake_sh(up, monkeypatch, {**built_repo(tmp_path, None), 'npm': (0, 'ok\n', '')})
    record_resync(up, monkeypatch)
    plan = up.Plan(updates=(up.Rebuild(at(up, tmp_path, [['npm', 'ci']])),))
    assert up.apply_updates(plan) == []
    assert (['npm', 'ci'], str(tmp_path)) in calls
    assert (tmp_path / '.git' / 'agent-update-build').read_text().strip() == 'abc123'


def test_failed_build_leaves_no_marker(up, monkeypatch, tmp_path):
    fake_sh(up, monkeypatch, {**built_repo(tmp_path, None), 'npm': (1, '', 'ERR!\n')})
    plan = up.Plan(updates=(up.Rebuild(at(up, tmp_path, [['npm', 'ci']])),))
    assert up.apply_updates(plan) == ['x: build `npm ci` failed: ERR!']
    assert not (tmp_path / '.git' / 'agent-update-build').exists()


def test_build_is_not_planned_twice_when_pulling(up, monkeypatch, tmp_path):
    fake_sh(up, monkeypatch, {**built_repo(tmp_path, None), 'rev-list --count': (0, '2\n', '')})
    srv = server(up, build_pkg(tmp_path), {'commands': {}})
    plan = up.Plan(updates=up.git_status(srv, up.Check(fetch=True)).updates)
    assert of(plan.updates, up.Pull) == (up.Pull(srv, 'main'),)
    assert srv.checkout == str(tmp_path)
    assert of(plan.updates, up.Rebuild) == ()


# --------------------------------------------------------------------------- #
# resync: pick Git Bash on Windows, record a missing bash as a failure
# --------------------------------------------------------------------------- #
def test_find_bash_prefers_git_bash_on_windows(up, monkeypatch, tmp_path):
    git = tmp_path / 'Git' / 'cmd' / 'git.exe'
    bash = tmp_path / 'Git' / 'bin' / 'bash.exe'
    for f in (git, bash):
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text('')
    monkeypatch.setattr(
        up.shutil, 'which', lambda name, path=None: str(git) if name == 'git' else r'C:\WINDOWS\system32\bash.EXE'
    )
    assert up.find_bash(windows=True) == str(bash)


def test_find_bash_skips_wsl_and_store_stubs(up, monkeypatch, tmp_path):
    sys32 = tmp_path / 'Windows' / 'System32'
    apps = tmp_path / 'AppData' / 'Local' / 'Microsoft' / 'WindowsApps'
    good = tmp_path / 'msys' / 'usr' / 'bin'
    for d in (sys32, apps, good):
        d.mkdir(parents=True)
        (d / 'bash.exe').write_text('')
    monkeypatch.setenv('PATH', up.os.pathsep.join(map(str, (sys32, apps, good))))
    monkeypatch.setattr(up.shutil, 'which', lambda name, path=None: None)
    assert up.find_bash(windows=True) == str(good / 'bash.exe')


def test_find_bash_on_posix_uses_path(up, monkeypatch):
    monkeypatch.setattr(up.shutil, 'which', lambda name, path=None: '/bin/bash' if name == 'bash' else None)
    assert up.find_bash(windows=False) == '/bin/bash'


def test_resync_without_bash_is_a_failure_not_a_crash(up, monkeypatch, tmp_path):
    def boom(cmd, **kw):
        raise FileNotFoundError(2, 'not found', cmd[0])

    monkeypatch.setattr(up.subprocess, 'run', boom)
    assert up.resync() is False
    cat = tmp_path / 'x.toml'
    cat.write_text('[package]\nref = "1.0"\n')
    failures = up.apply_updates(up.Plan(updates=(up.CatalogBump(up.Server('x', cat, {}), '1.0', '1.1'),)))
    assert any('agent-sync' in f for f in failures)


def test_uv_argv_prefers_env_then_commands_then_path(up, monkeypatch):
    monkeypatch.delenv('UV', raising=False)
    assert up.uv_argv({'commands': {}}) == ['uv']
    assert up.uv_argv({'commands': {'uv': 'C:/bin/uv.exe'}}) == ['C:/bin/uv.exe']
    assert up.uv_argv({'commands': {'uv': ['wsl', 'uv']}}) == ['wsl', 'uv']
    monkeypatch.setenv('UV', '/env/uv')
    assert up.uv_argv({'commands': {'uv': 'C:/bin/uv.exe'}}) == ['/env/uv']


def test_uv_tool_upgrade_uses_check_uv(up, monkeypatch):
    calls = fake_sh(up, monkeypatch, {'upgrade': (0, 'Updated t\n', '')})
    record_resync(up, monkeypatch)
    check = up.Check(fetch=False, outdated={'t': ('1', '2')}, uv=('C:/bin/uv.exe',))
    status = up.uv_tool_status(server(up, {'manager': 'uv-tool', 'name': 't'}), check)
    assert up.apply_updates(up.Plan(updates=status.updates)) == []
    assert calls[0][0] == ['C:/bin/uv.exe', 'tool', 'upgrade', 't']


def test_uv_outdated_uses_given_uv(up, monkeypatch):
    calls = fake_sh(up, monkeypatch, {'outdated': (0, 'ruff v0.1.0 [latest: 0.2.0]\n', '')})
    assert up.uv_outdated(['C:/bin/uv.exe']) == {'ruff': ('0.1.0', '0.2.0')}
    assert calls[0][0] == ['C:/bin/uv.exe', 'tool', 'list', '--outdated']


# --------------------------------------------------------------------------- #
# Server, the manager dispatch table and the plan's apply order
# --------------------------------------------------------------------------- #
def test_server_resolves_path_and_build_once(up):
    ctx = {'AGENT_HOME': '/agents', 'commands': {'npm': ['cmd', '/c', 'npm']}}
    pkg = {'manager': 'git', 'repo': 'o/x', 'path': '${AGENT_HOME}/vendor/x', 'build': [['npm', 'ci']]}
    srv = up.Server.from_catalog('x', Path('x.toml'), pkg, ctx)
    assert srv.checkout == '/agents/vendor/x'
    assert srv.build == (('cmd', '/c', 'npm', 'ci'),)
    assert (srv.manager, srv.repo) == ('git', 'o/x')


def test_server_is_hashable_and_pkg_read_only(up):
    pkg = {'manager': 'git', 'repo': 'o/x'}
    srv = up.Server.from_catalog('x', Path('x.toml'), pkg, {})
    assert hash(up.Pull(srv, 'main')) == hash(up.Pull(srv, 'main'))
    pkg['repo'] = 'o/y'
    assert srv.repo == 'o/x'
    with pytest.raises(TypeError):
        srv.pkg['repo'] = 'o/z'


def test_build_steps_are_only_read_for_git(up):
    srv = up.Server.from_catalog('x', Path('x.toml'), {'manager': 'npm', 'build': [3]}, {})
    assert srv.build == ()


def test_every_documented_manager_is_dispatched(up):
    assert set(up.STATUS) == {'npm', 'uv-tool', 'git', 'uv-project', 'source', 'remote', 'manual'}
    check = up.Check(fetch=False)
    assert up.server_status(server(up, {'manager': 'source'}), check).text == 'local source (always current)'
    assert up.server_status(server(up, {'manager': 'remote'}), check).text == 'remote (hosted)'


def test_unknown_manager_is_reported_not_tracked(up):
    status = up.server_status(server(up, {'manager': 'brew'}), up.Check(fetch=False))
    assert status == up.Status("unknown manager 'brew' (not tracked)")
    assert up.server_status(server(up, {}), up.Check(fetch=False)).text == "unknown manager '?' (not tracked)"


def test_uv_tool_status_queues_upgrade_from_outdated_map(up):
    check = up.Check(fetch=False, outdated={'ruff': ('0.1.0', '0.2.0')})
    status = up.uv_tool_status(server(up, {'manager': 'uv-tool', 'name': 'ruff'}), check)
    assert status == up.Status('0.1.0 -> 0.2.0', (up.UvUpgrade('ruff'),))
    assert up.uv_tool_status(server(up, {'manager': 'uv-tool', 'name': 'ty'}), check) == up.Status('up to date')


def test_pinned_npm_queues_catalog_bump(up, monkeypatch):
    fake_sh(up, monkeypatch, {'dist-tags.1.0.0': (0, '1.2.0\n', '')})
    srv = server(up, {'manager': 'npm', 'name': 'pkg', 'ref': '1.0.0'})
    assert up.npm_status(srv, up.Check(fetch=True)) == up.Status(
        'pinned 1.0.0 -> 1.2.0', (up.CatalogBump(srv, '1.0.0', '1.2.0'),)
    )


def test_plan_collects_updates_in_check_order(up, tmp_path):
    a, b = at(up, tmp_path, name='a'), at(up, tmp_path, name='b')
    statuses = [up.Status('a', (up.Manual(a),)), up.Status('ok'), up.Status('b', (up.Pull(b, 'main'), up.Manual(b)))]
    plan = up.plan_from(statuses)
    assert plan.updates == (up.Manual(a), up.Pull(b, 'main'), up.Manual(b))
    assert of(plan.updates, up.Manual) == (up.Manual(a), up.Manual(b))


def test_apply_runs_kinds_in_fixed_order(up, monkeypatch, tmp_path):
    calls = fake_sh(up, monkeypatch, {'pull': (0, 'ok\n', ''), 'clone': (0, '', ''), 'upgrade': (0, 'ok\n', '')})
    record_resync(up, monkeypatch)
    srv = at(up, tmp_path / 'x')
    plan = up.Plan(updates=(up.Pull(srv, 'main'), up.UvUpgrade('t'), up.Clone(srv)))
    assert up.apply_updates(plan) == []
    kinds = [next(w for w in ('upgrade', 'clone', 'pull') if w in cmd) for cmd, _ in calls]
    assert kinds == ['upgrade', 'clone', 'pull']
