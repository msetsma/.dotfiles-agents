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
  manager = "source"   runs from a working tree (always current).
  manager = "manual"   a binary / app updated by hand.
  manager = "remote"   a hosted endpoint.

Usage
-----
  agent-update            report only
  agent-update --apply    apply uv upgrades + git pulls + catalog pin bumps, then sync
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent))
import agent_sync as ms


REPO = Path(__file__).resolve().parent.parent
CATALOG = REPO / 'catalog'
UV = os.environ.get('UV', '/opt/homebrew/bin/uv')
FLOATING_TAGS = {'latest', 'beta', 'next', 'canary', 'rc', 'alpha', 'dev'}


def sh(cmd: list[str], cwd: str | None = None) -> subprocess.CompletedProcess:
    # which() resolves Windows shims (npm.cmd); on POSIX it is the PATH lookup.
    exe = shutil.which(cmd[0]) or cmd[0]
    return subprocess.run([exe, *cmd[1:]], capture_output=True, text=True, cwd=cwd, check=False)


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


def bump_uv_project(proj: str, catalog_path: Path, old: str, new: str) -> None:
    py = Path(proj) / 'pyproject.toml'
    text = py.read_text()
    py.write_text(text.replace(f'@{old}#', f'@{new}#'))
    ct = catalog_path.read_text()
    catalog_path.write_text(re.sub(r'(?m)^(\s*ref\s*=\s*")[^"]*(")', rf'\g<1>{new}\g<2>', ct))
    sh([UV, 'lock'], cwd=proj)


def uv_outdated() -> dict[str, tuple[str, str]]:
    r = sh([UV, 'tool', 'list', '--outdated'])
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


def git_info(path: str, do_fetch: bool) -> dict:
    if not path or not Path(path).is_dir():
        return {'error': 'path not found'}
    if sh(['git', '-C', path, 'rev-parse', '--is-inside-work-tree']).stdout.strip() != 'true':
        return {'error': 'not a git repo'}
    if do_fetch:
        sh(['git', '-C', path, 'fetch', '--quiet', 'origin'])
    branch = sh(['git', '-C', path, 'rev-parse', '--abbrev-ref', 'HEAD']).stdout.strip()
    upstream = sh(['git', '-C', path, 'rev-parse', '--abbrev-ref', '--symbolic-full-name', '@{u}']).stdout.strip()
    ref = tracking_ref(path, upstream)
    behind = behind_count(path, ref)
    if ref and behind is not None:
        return {'branch': branch, 'upstream': upstream or None, 'ref': ref, 'behind': behind}

    # No branch upstream: maybe it's a shallow, tag-pinned checkout.
    cur_tag = sh(['git', '-C', path, 'describe', '--tags', '--exact-match', 'HEAD']).stdout.strip()
    newest = latest_tag(sh(['git', '-C', path, 'ls-remote', '--tags', '--refs', 'origin']).stdout) if cur_tag else None
    return {
        'branch': branch,
        'upstream': upstream or None,
        'ref': ref,
        'behind': behind,
        'tag': cur_tag or None,
        'latest_tag': newest,
    }


def rewrite_pin(path: Path, new_ref: str) -> bool:
    text = path.read_text()
    new_text, n = re.subn(r'(?m)^(\s*ref\s*=\s*")[^"]*(")', rf'\g<1>{new_ref}\g<2>', text)
    if n:
        path.write_text(new_text)
    return bool(n)


@dataclass
class Plan:
    """Updates found while checking, applied by ``apply_updates``."""

    catalog_bumps: list[tuple[Path, str, str, str]] = field(default_factory=list)  # path, name, old, new
    uv_upgrades: list[str] = field(default_factory=list)
    uv_project_bumps: list[tuple[str, str, Path, str, str]] = field(
        default_factory=list
    )  # name, proj, catalog, old, new
    git_pulls: list[tuple[str, str, str]] = field(default_factory=list)  # name, path, branch
    git_manual: list[tuple[str, str]] = field(default_factory=list)


def npm_status(name: str, path: Path, pkg: dict, plan: Plan) -> str:
    ref = pkg.get('ref') or 'latest'
    latest = npm_latest(pkg['name'], ref)
    if latest is None:
        return 'npm lookup failed'
    if ref in FLOATING_TAGS:
        return f'floating @{ref} (now {latest})'
    if latest == ref:
        return f'pinned {ref} (up to date)'
    plan.catalog_bumps.append((path, name, ref, latest))
    return f'pinned {ref} -> {latest}'


def uv_tool_status(pkg: dict, uv: dict[str, tuple[str, str]], plan: Plan) -> str:
    if pkg['name'] not in uv:
        return 'up to date'
    cur, latest = uv[pkg['name']]
    plan.uv_upgrades.append(pkg['name'])
    return f'{cur} -> {latest}'


def git_status(name: str, pkg: dict, ctx: dict, do_fetch: bool, plan: Plan) -> str:
    gp = ms.expand(pkg.get('path', ''), ctx)
    gi = git_info(gp, do_fetch=do_fetch)
    if gi.get('error'):
        return f'{gi["error"]} ({gp})'
    if gi['behind'] is not None and gi['behind'] > 0:
        upstream = gi['upstream'] or gi['ref'] or 'origin'
        if gi['branch'] not in ('HEAD', ''):
            plan.git_pulls.append((name, gp, gi['branch']))
        else:
            plan.git_manual.append((name, gp))
        return f'{gi["behind"]} commit(s) behind {upstream}'
    if gi['behind'] == 0 and not gi.get('tag'):
        return f'{gi["branch"]} up to date'
    plan.git_manual.append((name, gp))
    if gi.get('tag'):
        if gi.get('latest_tag') and gi['latest_tag'] != gi['tag']:
            return f'tag {gi["tag"]} -> {gi["latest_tag"]} (tag-pinned - manual)'
        return f'tag {gi["tag"]} (up to date)'
    return f'{gi["branch"]} (no upstream) - manual'


def uv_project_status(name: str, path: Path, pkg: dict, ctx: dict, do_fetch: bool, plan: Plan) -> str:
    ref = pkg.get('ref')
    latest = remote_latest_tag(pkg['repo']) if pkg.get('repo') and do_fetch else None
    if latest and latest != ref:
        plan.uv_project_bumps.append((name, ms.expand(pkg['path'], ctx), path, ref, latest))
        return f'{ref} -> {latest}'
    return f'{ref} (up to date)'


def server_status(
    name: str, path: Path, pkg: dict, ctx: dict, uv: dict[str, tuple[str, str]], do_fetch: bool, plan: Plan
) -> str:
    mgr = pkg.get('manager', '?')
    if mgr == 'npm':
        return npm_status(name, path, pkg, plan)
    if mgr == 'uv-tool':
        return uv_tool_status(pkg, uv, plan)
    if mgr == 'git':
        return git_status(name, pkg, ctx, do_fetch, plan)
    if mgr == 'uv-project':
        return uv_project_status(name, path, pkg, ctx, do_fetch, plan)
    if mgr == 'source':
        return 'local source (always current)'
    if mgr == 'remote':
        return 'remote (hosted)'
    return 'manual'


def print_table(rows: list[tuple[str, str, str]]) -> None:
    width = max((len(r[0]) for r in rows), default=6)
    mwidth = max((len(r[1]) for r in rows), default=7)
    print(f'{"SERVER".ljust(width)}  {"MANAGER".ljust(mwidth)}  STATUS')
    print(f'{"-" * width}  {"-" * mwidth}  {"-" * 40}')
    for name, mgr, status in rows:
        print(f'{name.ljust(width)}  {mgr.ljust(mwidth)}  {status}')


def apply_updates(plan: Plan) -> None:
    print('\napplying...')
    changed_catalog = False
    for path, name, old, new in plan.catalog_bumps:
        if rewrite_pin(path, new):
            print(f'  {name}: catalog ref {old} -> {new}')
            changed_catalog = True
    for name, proj, cpath, old, new in plan.uv_project_bumps:
        print(f'  {name}: {old} -> {new} (uv project {proj})')
        bump_uv_project(proj, cpath, old, new)
    for tool in plan.uv_upgrades:
        print(f'  uv tool upgrade {tool}')
        r = sh([UV, 'tool', 'upgrade', tool])
        print(
            '    ' + (r.stdout.strip().splitlines()[-1] if r.returncode == 0 and r.stdout.strip() else r.stderr.strip())
        )
    for _name, gp, branch in plan.git_pulls:
        print(f'  git pull {gp} ({branch})')
        r = sh(['git', '-C', gp, 'pull', '--ff-only'])
        print('    ' + (r.stdout.strip().splitlines()[-1] if r.returncode == 0 else r.stderr.strip()))
    for name, gp in plan.git_manual:
        print(f'  {name}: {gp} has no upstream/branch - update manually')

    if changed_catalog:
        print('\nre-syncing configs...')
        subprocess.run([str(REPO / 'bin' / 'agent-sync')], check=False)
    elif plan.uv_project_bumps:
        print('\nagent configs unchanged (tag lives in the project); lock refreshed')
    else:
        print('\ncatalog unchanged; no re-sync needed')


def main() -> int:
    ap = argparse.ArgumentParser(description='Check/apply MCP + runtime updates.')
    ap.add_argument('--apply', action='store_true', help='apply updates (default: report)')
    ap.add_argument('--no-fetch', action='store_true', help='skip git fetch')
    args = ap.parse_args()

    ctx = ms.build_context()
    servers: dict[str, tuple[Path, dict]] = {}
    for path in sorted((CATALOG / 'mcp').glob('*.toml')):
        raw = ms.load_toml(path)
        if raw.get('package'):
            servers[raw['name']] = (path, raw['package'])

    uv = uv_outdated()
    plan = Plan()
    rows = [
        (name, pkg.get('manager', '?'), server_status(name, path, pkg, ctx, uv, not args.no_fetch, plan))
        for name, (path, pkg) in servers.items()
    ]
    print_table(rows)

    if not args.apply:
        print()
        if plan.catalog_bumps or plan.uv_upgrades or plan.git_pulls or plan.git_manual:
            print('run `make agent-update` (or agent-update --apply) to apply')
        else:
            print('everything is current')
        return 0

    apply_updates(plan)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
