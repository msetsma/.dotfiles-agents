#!/usr/bin/env python3
"""agent-update - check (and apply) updates for everything in the catalog.

The ``[package]`` block on each server says how it's kept current:

  manager = "npm"      ``npm view <name> dist-tags.<ref>``. A ref of "latest"
                       (or another dist-tag) floats; only a concrete version
                       gets bumped.
  manager = "uv-tool"  ``uv tool list --outdated``; applied with
                       ``uv tool upgrade <name>``.
  manager = "git"      ``git fetch`` + behind-count vs upstream; applied with
                       ``git pull --ff-only`` when on a branch with an upstream.
                       A detached checkout on a tag (shallow or full) is
                       compared to origin's newest tag; a newer tag is
                       reported as manual work.
                       With ``repo = "owner/name"`` a missing ``path`` is
                       reported as not cloned and applied with ``git clone`` over SSH
                       (``git@github.com:owner/name.git``), so the clone
                       matches a ``hasconfig:remote.*.url`` includeIf.
                       Optional ``build`` (argv lists or command strings) runs
                       in ``path`` after a successful clone or pull; argv[0]
                       honours the ``[commands]`` mapping (catalog/paths.toml,
                       then catalog/local.toml), and an unmapped argv[0] is
                       found on PATH (``npm.cmd`` on Windows). A successful build
                       records HEAD in ``.git/agent-update-build``; a checkout
                       whose marker is missing or stale is reported as
                       "build pending" and rebuilt on --apply.
  manager = "source"   runs from a working tree (always current).
  manager = "manual"   a binary / app updated by hand.
  manager = "remote"   a hosted endpoint.

How it fits together: each catalog entry becomes a ``Server`` (its ``path``
expanded and ``build`` steps mapped once). A status function per manager (the
``STATUS`` table) turns a ``Server`` plus the run-wide ``Check`` settings into a
``Status``: the report line and the updates it found (``CatalogBump``,
``ProjectBump``, ``UvUpgrade``, ``Clone``, ``Pull``, ``Rebuild``, ``Manual``).
Those updates form the ``Plan``, and ``--apply`` runs each one in that order.

Usage
-----
  agent-update            report only
  agent-update --no-fetch report without contacting git remotes
  agent-update --apply    apply catalog pin bumps, uv upgrades and locks, git
                          clones/pulls/builds, then re-sync every agent
                          (bin/agent-sync) when any step was applied. A failed
                          re-sync exits nonzero.

Servers excluded by this machine's ``[profile]`` (catalog/local.toml) are skipped.
"""

from __future__ import annotations

import argparse
import os
import re
import shlex
import shutil
import subprocess
import sys
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any


# Sibling modules, importable even when the script dir is not on sys.path (-P).
sys.path.insert(0, str(Path(__file__).resolve().parent))
from agent_profile import is_selected, split_command
from agentsync.catalog import load_raw
from agentsync.common import build_context, expand, load_local


REPO = Path(__file__).resolve().parent.parent
CATALOG = REPO / 'catalog'
FLOATING_TAGS = {'latest', 'beta', 'next', 'canary', 'rc', 'alpha', 'dev'}
BUILD_MARKER = 'agent-update-build'
# Windows bash.exe that is not Git Bash: the WSL launcher and the Store stub.
FOREIGN_BASH_DIRS = ('system32', 'windowsapps')

type Steps = tuple[tuple[str, ...], ...]


def sh(cmd: Sequence[str], cwd: str | None = None) -> subprocess.CompletedProcess:
    # which() resolves Windows shims (npm.cmd); on POSIX it is the PATH lookup.
    exe = shutil.which(cmd[0]) or cmd[0]
    return subprocess.run([exe, *cmd[1:]], capture_output=True, text=True, cwd=cwd, check=False)


def first_line(text: str) -> str:
    lines = text.strip().splitlines()
    return lines[0] if lines else ''


def last_line(text: str) -> str:
    lines = text.strip().splitlines()
    return lines[-1] if lines else ''


def why(r: subprocess.CompletedProcess) -> str:
    """Short reason a command failed: its last stderr line, else the exit code."""
    return last_line(r.stderr or '') or f'exit {r.returncode}'


def npm_latest(name: str, tag: str) -> str | None:
    r = sh(['npm', 'view', name, f'dist-tags.{tag}'])
    return r.stdout.strip().strip('"') or None if r.returncode == 0 else None


def verkey(tag: str) -> list[int]:
    return [int(n) for n in re.findall(r'\d+', tag)] or [0]


def latest_tag(listing: str) -> str | None:
    """Highest version-looking tag in ``git ls-remote --tags --refs`` output."""
    tags = [line.rsplit('/', 1)[-1] for line in listing.splitlines() if line.strip() and re.search(r'\d', line)]
    return max(tags, key=verkey) if tags else None


def remote_latest_tag(repo: str) -> str | None:
    return latest_tag(sh(['git', 'ls-remote', '--tags', '--refs', f'https://github.com/{repo}.git']).stdout)


def uv_argv(ctx: dict) -> list[str]:
    """How to run uv: ``$UV``, else ``[commands].uv``, else ``uv`` on PATH."""
    if env := os.environ.get('UV'):
        return [env]
    head, prefix = split_command(ctx.get('commands', {}).get('uv', 'uv'))
    return [head, *prefix]


def uv_outdated(uv: Sequence[str]) -> dict[str, tuple[str, str]]:
    r = sh([*uv, 'tool', 'list', '--outdated'])
    out: dict[str, tuple[str, str]] = {}
    for line in r.stdout.splitlines():
        m = re.match(r'^([\w.\-]+) v([^\s]+) \[latest: ([^\]]+)\]', line)
        if m:
            out[m.group(1)] = (m.group(2), m.group(3))
    return out


def tracking_ref(path: str, upstream: str) -> str:
    """The branch upstream, else the first origin default branch that exists."""
    if upstream:
        return upstream
    for cand in ('origin/HEAD', 'origin/main', 'origin/master'):
        if sh(['git', '-C', path, 'rev-parse', '--verify', '--quiet', cand]).returncode == 0:
            return cand
    return ''


def behind_count(path: str, ref: str) -> int | None:
    if not ref:
        return None
    rc = sh(['git', '-C', path, 'rev-list', '--count', f'HEAD..{ref}'])
    if rc.returncode == 0 and rc.stdout.strip().isdigit():
        return int(rc.stdout.strip())
    return None


def default_branch(path: str) -> str:
    """origin's default branch, from the local origin/HEAD ref (no network)."""
    r = sh(['git', '-C', path, 'symbolic-ref', '--quiet', '--short', 'refs/remotes/origin/HEAD'])
    return r.stdout.strip().removeprefix('origin/') if r.returncode == 0 else ''


@dataclass(frozen=True)
class GitError:
    """A checkout that could not be inspected."""

    text: str


@dataclass(frozen=True)
class BranchCheckout:
    """A checkout compared to a tracking ref (``behind`` is None when there is none)."""

    branch: str
    upstream: str
    ref: str
    behind: int | None
    default: str = ''  # origin's default branch, when known


@dataclass(frozen=True)
class TagCheckout:
    """A detached checkout sitting on a tag, with origin's newest tag when checked."""

    branch: str
    tag: str
    checked: bool
    latest_tag: str | None = None


type GitInfo = GitError | BranchCheckout | TagCheckout


def git_info(path: str, do_fetch: bool) -> GitInfo:
    if not path or not Path(path).is_dir():
        return GitError('path not found')
    if sh(['git', '-C', path, 'rev-parse', '--is-inside-work-tree']).stdout.strip() != 'true':
        return GitError('not a git repo')
    if do_fetch:
        r = sh(['git', '-C', path, 'fetch', '--quiet', 'origin'])
        if r.returncode != 0:
            return GitError(f'fetch failed: {first_line(r.stderr or "") or f"exit {r.returncode}"}')
    branch = sh(['git', '-C', path, 'rev-parse', '--abbrev-ref', 'HEAD']).stdout.strip()
    upstream = sh(['git', '-C', path, 'rev-parse', '--abbrev-ref', '--symbolic-full-name', '@{u}']).stdout.strip()
    # Detached with no upstream: a tag pin is compared to the newest tag, never
    # to origin/HEAD, whether the clone is shallow or full.
    cur_tag = exact_tag(path) if branch == 'HEAD' and not upstream else ''
    if cur_tag:
        return tag_info(path, branch, cur_tag, do_fetch)
    ref = tracking_ref(path, upstream)
    behind = behind_count(path, ref)
    if ref and behind is not None:
        return BranchCheckout(branch, upstream, ref, behind, default_branch(path))
    return BranchCheckout(branch, upstream, ref, behind)


def exact_tag(path: str) -> str:
    """The tag HEAD sits on exactly, else ''."""
    r = sh(['git', '-C', path, 'describe', '--tags', '--exact-match', 'HEAD'])
    return r.stdout.strip() if r.returncode == 0 else ''


def tag_info(path: str, branch: str, cur_tag: str, do_fetch: bool) -> GitError | TagCheckout:
    """A tag-pinned checkout, with origin's newest version-looking tag.

    Without ``do_fetch`` origin is not queried and the tag is reported unchecked.
    """
    if not do_fetch:
        return TagCheckout(branch, cur_tag, checked=False)
    r = sh(['git', '-C', path, 'ls-remote', '--tags', '--refs', 'origin'])
    if r.returncode != 0:
        return GitError(f'ls-remote failed: {first_line(r.stderr or "") or f"exit {r.returncode}"}')
    return TagCheckout(branch, cur_tag, checked=True, latest_tag=latest_tag(r.stdout))


def rewrite_pin(path: Path, new_ref: str) -> bool:
    text = path.read_text()
    new_text, n = re.subn(r'(?m)^(\s*ref\s*=\s*")[^"]*(")', rf'\g<1>{new_ref}\g<2>', text)
    if n:
        path.write_text(new_text)
    return bool(n)


def build_steps(pkg: Mapping[str, Any], ctx: dict) -> Steps:
    """``[package] build`` as argv tuples, argv[0] mapped through ``[commands]``."""
    steps: list[tuple[str, ...]] = []
    for step in pkg.get('build', []):
        if isinstance(step, str) and step.strip():
            argv = shlex.split(step)
        elif isinstance(step, list) and step and all(isinstance(a, str) for a in step):
            argv = list(step)
        else:
            raise ValueError(f'[package] build step must be a command string or argv list, got {step!r}')
        head, prefix = split_command(ctx.get('commands', {}).get(argv[0], argv[0]))
        steps.append((head, *prefix, *argv[1:]))
    return tuple(steps)


# --------------------------------------------------------------------------- #
# domain records
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Server:
    """One catalog server with a ``[package]`` block, its ctx-dependent parts resolved once."""

    name: str
    catalog: Path  # the server's catalog/mcp/<name>.toml
    pkg: Mapping[str, Any] = field(hash=False)  # the [package] block (read-only view)
    checkout: str = ''  # pkg path, expanded through ctx ('' when unset)
    build: Steps = ()  # argv steps run after a clone or pull (git only)

    def __post_init__(self) -> None:
        # A read-only copy, so the frozen record cannot be changed through pkg.
        object.__setattr__(self, 'pkg', MappingProxyType(dict(self.pkg)))

    @classmethod
    def from_catalog(cls, name: str, catalog: Path, pkg: Mapping[str, Any], ctx: dict) -> Server:
        # Build steps only mean something for a git checkout; other managers
        # never read (or validate) them.
        return cls(
            name=name,
            catalog=catalog,
            pkg=pkg,
            checkout=expand(pkg.get('path', ''), ctx),
            build=build_steps(pkg, ctx) if pkg.get('manager') == 'git' else (),
        )

    @property
    def manager(self) -> str:
        return self.pkg.get('manager', '?')

    @property
    def repo(self) -> str:
        return self.pkg.get('repo', '')


@dataclass(frozen=True)
class Check:
    """Run-wide settings every status function reads."""

    fetch: bool  # contact git remotes (fetch, tag lookups)
    outdated: Mapping[str, tuple[str, str]] = field(default_factory=dict)  # uv tool -> (current, latest)
    uv: tuple[str, ...] = ('uv',)  # argv that runs uv (see uv_argv)


@dataclass(frozen=True)
class CatalogBump:
    """A concrete npm pin with a newer version: rewrite the catalog ref."""

    server: Server
    old: str
    new: str


@dataclass(frozen=True)
class ProjectBump:
    """A uv project pinned to a git tag with a newer tag: bump, then ``uv lock``."""

    server: Server
    old: str | None
    new: str
    uv: tuple[str, ...] = ('uv',)  # argv that runs uv lock


@dataclass(frozen=True)
class UvUpgrade:
    tool: str
    uv: tuple[str, ...] = ('uv',)  # argv that runs uv tool upgrade


@dataclass(frozen=True)
class Clone:
    """A git package whose checkout is missing."""

    server: Server


@dataclass(frozen=True)
class Pull:
    """A branch checkout behind its upstream."""

    server: Server
    branch: str


@dataclass(frozen=True)
class Rebuild:
    """A checkout whose build is not current at HEAD (and is not being pulled)."""

    server: Server


@dataclass(frozen=True)
class Manual:
    """A checkout that needs updating by hand (detached, no upstream, newer tag)."""

    server: Server


type Update = CatalogBump | ProjectBump | UvUpgrade | Clone | Pull | Rebuild | Manual


@dataclass(frozen=True)
class Status:
    """One server's report line and the updates it found."""

    text: str
    updates: tuple[Update, ...] = ()


@dataclass(frozen=True)
class Plan:
    """Updates found while checking, applied by ``apply_updates``."""

    updates: tuple[Update, ...] = ()

    def pending(self) -> bool:
        return bool(self.updates)


def plan_from(statuses: Iterable[Status]) -> Plan:
    return Plan(updates=tuple(u for status in statuses for u in status.updates))


# --------------------------------------------------------------------------- #
# build marker
# --------------------------------------------------------------------------- #
def head_sha(gp: str) -> str:
    r = sh(['git', '-C', gp, 'rev-parse', 'HEAD'])
    return r.stdout.strip() if r.returncode == 0 else ''


def marker_path(gp: str) -> Path | None:
    """Where the last successful build's HEAD is recorded (inside .git, so never untracked)."""
    r = sh(['git', '-C', gp, 'rev-parse', '--git-path', BUILD_MARKER])
    out = r.stdout.strip()
    return Path(gp) / out if r.returncode == 0 and out else None


def build_current(gp: str) -> bool:
    """True when the build marker matches HEAD."""
    marker, head = marker_path(gp), head_sha(gp)
    if marker is None or not head or not marker.is_file():
        return False
    return marker.read_text().strip() == head


def record_build(name: str, gp: str) -> list[str]:
    marker, head = marker_path(gp), head_sha(gp)
    if marker is None or not head:
        return [f'{name}: built, but could not record HEAD in {BUILD_MARKER}; the build will rerun next time']
    marker.write_text(head + '\n')
    return []


# --------------------------------------------------------------------------- #
# status: one function per manager, all (Server, Check) -> Status
# --------------------------------------------------------------------------- #
def npm_status(server: Server, check: Check) -> Status:
    ref = server.pkg.get('ref') or 'latest'
    latest = npm_latest(server.pkg['name'], ref)
    if latest is None:
        return Status('npm lookup failed')
    if ref in FLOATING_TAGS:
        return Status(f'floating @{ref} (now {latest})')
    if latest == ref:
        return Status(f'pinned {ref} (up to date)')
    return Status(f'pinned {ref} -> {latest}', (CatalogBump(server, ref, latest),))


def uv_tool_status(server: Server, check: Check) -> Status:
    tool = server.pkg['name']
    if tool not in check.outdated:
        return Status('up to date')
    cur, latest = check.outdated[tool]
    return Status(f'{cur} -> {latest}', (UvUpgrade(tool, check.uv),))


def off_default(gi: BranchCheckout) -> str:
    """Status suffix when a branch checkout is not origin's default branch."""
    if gi.branch in ('HEAD', '') or not gi.default or gi.branch == gi.default:
        return ''
    return f' (on {gi.branch}, not {gi.default})'


def git_status(server: Server, check: Check) -> Status:
    gp = server.checkout
    if server.repo and gp and not Path(gp).exists():
        return Status(f'not cloned (github.com/{server.repo} -> {gp})', (Clone(server),))
    match git_info(gp, do_fetch=check.fetch):
        case GitError(text):
            return Status(f'{text} ({gp})')
        case TagCheckout() as gi:
            status = tag_status(server, gi)
        case BranchCheckout() as gi:
            status = branch_status(server, gi)
    pulling = any(isinstance(u, Pull) for u in status.updates)
    if server.build and not pulling and not build_current(gp):
        return Status(f'{status.text} (build pending)', (*status.updates, Rebuild(server)))
    return status


def branch_status(server: Server, gi: BranchCheckout) -> Status:
    """Status of a branch checkout; queues a pull or manual update when behind."""
    if gi.behind is None:
        return Status(f'{gi.branch} (no upstream) - manual', (Manual(server),))
    if gi.behind == 0:
        return Status(f'{gi.branch} up to date{off_default(gi)}')
    upstream = gi.upstream or gi.ref or 'origin'
    update = Pull(server, gi.branch) if gi.branch not in ('HEAD', '') else Manual(server)
    return Status(f'{gi.behind} commit(s) behind {upstream}{off_default(gi)}', (update,))


def tag_status(server: Server, gi: TagCheckout) -> Status:
    """Status of a tag-pinned checkout; a newer origin tag is manual work."""
    if not gi.checked:
        return Status(f'tag {gi.tag} (not checked: --no-fetch)')
    if gi.latest_tag and gi.latest_tag != gi.tag:
        return Status(f'tag {gi.tag} -> {gi.latest_tag} (tag-pinned - manual)', (Manual(server),))
    return Status(f'tag {gi.tag} (up to date)')


def uv_project_status(server: Server, check: Check) -> Status:
    ref = server.pkg.get('ref')
    if not check.fetch:
        return Status(f'{ref} (not checked: --no-fetch)')
    latest = remote_latest_tag(server.repo) if server.repo else None
    if latest and latest != ref:
        return Status(f'{ref} -> {latest}', (ProjectBump(server, ref, latest, check.uv),))
    return Status(f'{ref} (up to date)')


type StatusFn = Callable[[Server, Check], Status]


def fixed(text: str) -> StatusFn:
    """A status function for a manager that is never checked."""

    def status(server: Server, check: Check) -> Status:
        return Status(text)

    return status


STATUS: Mapping[str, StatusFn] = {
    'npm': npm_status,
    'uv-tool': uv_tool_status,
    'git': git_status,
    'uv-project': uv_project_status,
    'source': fixed('local source (always current)'),
    'remote': fixed('remote (hosted)'),
    'manual': fixed('manual (not tracked)'),
}


def server_status(server: Server, check: Check) -> Status:
    status_fn = STATUS.get(server.manager)
    if status_fn is None:
        return Status(f'unknown manager {server.manager!r} (not tracked)')
    return status_fn(server, check)


def print_table(rows: Sequence[tuple[Server, Status]]) -> None:
    width = max((len(server.name) for server, _ in rows), default=6)
    mwidth = max((len(server.manager) for server, _ in rows), default=7)
    print(f'{"SERVER".ljust(width)}  {"MANAGER".ljust(mwidth)}  STATUS')
    print(f'{"-" * width}  {"-" * mwidth}  {"-" * 40}')
    for server, status in rows:
        print(f'{server.name.ljust(width)}  {server.manager.ljust(mwidth)}  {status.text}')


# --------------------------------------------------------------------------- #
# re-sync through bash
# --------------------------------------------------------------------------- #
def is_foreign_bash(path: Path) -> bool:
    return any(part.lower() in FOREIGN_BASH_DIRS for part in path.parts)


def git_bash_near_git() -> str | None:
    """Git for Windows keeps bash.exe in <Git>/bin, beside <Git>/cmd/git.exe."""
    git = shutil.which('git')
    if not git:
        return None
    for parent in list(Path(git).parents)[:3]:
        cand = parent / 'bin' / 'bash.exe'
        if cand.is_file():
            return str(cand)
    return None


def windows_bash_on_path() -> str | None:
    for entry in os.environ.get('PATH', '').split(os.pathsep):
        cand = Path(entry) / 'bash.exe'
        if entry and cand.is_file() and not is_foreign_bash(cand):
            return str(cand)
    return None


def find_bash(windows: bool = os.name == 'nt') -> str | None:
    """bash for bin/agent-sync. On Windows: Git Bash, never WSL's or the Store stub."""
    if not windows:
        return shutil.which('bash')
    return git_bash_near_git() or windows_bash_on_path()


def resync() -> bool:
    """Run bin/agent-sync through bash: Windows cannot exec a bash script directly."""
    bash = find_bash()
    if bash is None:
        print('  no usable bash found (install Git for Windows)', file=sys.stderr)
        return False
    try:
        return subprocess.run([bash, str(REPO / 'bin' / 'agent-sync')], check=False).returncode == 0
    except OSError as e:
        print(f'  could not run {bash}: {e}', file=sys.stderr)
        return False


# --------------------------------------------------------------------------- #
# apply: one function per update kind, each taking its one record
# --------------------------------------------------------------------------- #
def run_build(server: Server) -> list[str]:
    """Run the server's build steps in its checkout; stop at, and report, the first failure.

    Only a fully successful build records its HEAD, so a failed one is retried.
    """
    if not server.build:
        return []
    for argv in server.build:
        print(f'    build: {" ".join(argv)}')
        r = sh(argv, cwd=server.checkout)
        if r.returncode != 0:
            return [f'{server.name}: build `{" ".join(argv)}` failed: {why(r)}']
    return record_build(server.name, server.checkout)


@dataclass(frozen=True)
class Outcome:
    """What an apply phase did: how many steps succeeded, and each failure."""

    applied: int = 0
    failures: tuple[str, ...] = ()

    def __add__(self, other: Outcome) -> Outcome:
        return Outcome(self.applied + other.applied, self.failures + other.failures)


def step(failures: list[str]) -> Outcome:
    """One step's outcome: applied when it produced no failure."""
    return Outcome(0 if failures else 1, tuple(failures))


def apply_catalog_bump(bump: CatalogBump) -> Outcome:
    if not rewrite_pin(bump.server.catalog, bump.new):
        return Outcome()
    print(f'  {bump.server.name}: catalog ref {bump.old} -> {bump.new}')
    return Outcome(1)


def apply_project_bump(bump: ProjectBump) -> Outcome:
    proj = bump.server.checkout
    print(f'  {bump.server.name}: {bump.old} -> {bump.new} (uv project {proj})')
    py = Path(proj) / 'pyproject.toml'
    py.write_text(py.read_text().replace(f'@{bump.old}#', f'@{bump.new}#'))
    rewrite_pin(bump.server.catalog, bump.new)
    r = sh([*bump.uv, 'lock'], cwd=proj)
    return step([f'{bump.server.name}: uv lock failed: {why(r)}'] if r.returncode != 0 else [])


def apply_uv_upgrade(upgrade: UvUpgrade) -> Outcome:
    print(f'  uv tool upgrade {upgrade.tool}')
    r = sh([*upgrade.uv, 'tool', 'upgrade', upgrade.tool])
    if r.returncode != 0:
        return step([f'uv tool upgrade {upgrade.tool}: {why(r)}'])
    print('    ' + last_line(r.stdout or ''))
    return Outcome(1)


def apply_clone(clone: Clone) -> Outcome:
    server = clone.server
    print(f'  git clone {server.repo} -> {server.checkout}')
    Path(server.checkout).parent.mkdir(parents=True, exist_ok=True)
    r = sh(['git', 'clone', '--quiet', f'git@github.com:{server.repo}.git', server.checkout])
    if r.returncode != 0:
        return step([f'{server.name}: git clone failed: {why(r)}'])
    print('    cloned')
    return Outcome(1, tuple(run_build(server)))


def apply_pull(pull: Pull) -> Outcome:
    server = pull.server
    print(f'  git pull {server.checkout} ({pull.branch})')
    r = sh(['git', '-C', server.checkout, 'pull', '--ff-only'])
    if r.returncode != 0:
        return step([f'{server.name}: git pull failed: {why(r)}'])
    print('    ' + last_line(r.stdout or ''))
    return Outcome(1, tuple(run_build(server)))


def apply_rebuild(rebuild: Rebuild) -> Outcome:
    print(f'  build {rebuild.server.name} ({rebuild.server.checkout})')
    return step(run_build(rebuild.server))


def report_manual(manual: Manual) -> Outcome:
    print(f'  {manual.server.name}: {manual.server.checkout} has no upstream/branch - update manually')
    return Outcome()


def apply_one(update: Update) -> Outcome:
    match update:
        case CatalogBump():
            return apply_catalog_bump(update)
        case ProjectBump():
            return apply_project_bump(update)
        case UvUpgrade():
            return apply_uv_upgrade(update)
        case Clone():
            return apply_clone(update)
        case Pull():
            return apply_pull(update)
        case Rebuild():
            return apply_rebuild(update)
        case Manual():
            return report_manual(update)


# The order --apply runs update kinds in; within a kind, check order.
KIND_ORDER: tuple[type, ...] = (CatalogBump, ProjectBump, UvUpgrade, Clone, Pull, Rebuild, Manual)


def apply_plan(plan: Plan) -> Outcome:
    out = Outcome()
    for update in sorted(plan.updates, key=lambda u: KIND_ORDER.index(type(u))):
        out += apply_one(update)
    return out


def apply_updates(plan: Plan) -> list[str]:
    """Apply the plan, then re-sync if anything was applied; return one message per failed step."""
    print('\napplying...')
    out = apply_plan(plan)
    failures = list(out.failures)
    if not out.applied:
        print('\nnothing applied; no re-sync needed')
        return failures
    print('\nre-syncing configs...')
    if not resync():
        failures.append('re-sync failed: bin/agent-sync exited nonzero')
    return failures


def report_footer(plan: Plan, fetched: bool) -> str:
    if plan.pending():
        return 'run `make agent-update` (or agent-update --apply) to apply'
    if not fetched:
        return 'nothing pending (remotes not checked: --no-fetch)'
    return 'everything is current'


# --------------------------------------------------------------------------- #
# entry point
# --------------------------------------------------------------------------- #
def load_servers(local: dict, ctx: dict) -> list[Server]:
    """Catalog servers with a ``[package]`` block that this machine's profile selects."""
    profile = local.get('profile', {})
    servers: dict[str, Server] = {}
    for path in sorted((CATALOG / 'mcp').glob('*.toml')):
        raw = load_raw(path, 'mcp', local)
        if raw.get('package') and is_selected(raw, profile):
            servers[raw['name']] = Server.from_catalog(raw['name'], path, raw['package'], ctx)
    return list(servers.values())


def main() -> int:
    ap = argparse.ArgumentParser(description='Check/apply MCP + runtime updates.')
    ap.add_argument('--apply', action='store_true', help='apply updates (default: report)')
    ap.add_argument('--no-fetch', action='store_true', help='skip git remotes (fetch and tag lookups)')
    args = ap.parse_args()

    local = load_local()
    ctx = build_context(local)
    servers = load_servers(local, ctx)

    uv = tuple(uv_argv(ctx))
    check = Check(fetch=not args.no_fetch, outdated=uv_outdated(uv), uv=uv)
    statuses = [server_status(server, check) for server in servers]
    print_table(list(zip(servers, statuses, strict=True)))
    plan = plan_from(statuses)

    if not args.apply:
        print()
        print(report_footer(plan, fetched=check.fetch))
        return 0

    failures = apply_updates(plan)
    if failures:
        print(f'\n{len(failures)} step(s) failed:', file=sys.stderr)
        for failure in failures:
            print(f'  {failure}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
