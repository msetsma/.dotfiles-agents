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
import subprocess
import sys
from pathlib import Path


REPO = Path(__file__).resolve().parent.parent
CATALOG = REPO / 'catalog'
UV = os.environ.get('UV', '/opt/homebrew/bin/uv')
FLOATING_TAGS = {'latest', 'beta', 'next', 'canary', 'rc', 'alpha', 'dev'}

sys.path.insert(0, str(REPO / 'bin'))
import agent_sync as ms


def sh(cmd: list[str], cwd: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)


def npm_latest(name: str, tag: str) -> str | None:
    r = sh(['npm', 'view', name, f'dist-tags.{tag}'])
    return r.stdout.strip().strip('"') or None if r.returncode == 0 else None


def verkey(tag: str) -> list[int]:
    return [int(n) for n in re.findall(r'\d+', tag)] or [0]


def remote_latest_tag(repo: str) -> str | None:
    r = sh(['git', 'ls-remote', '--tags', '--refs', f'https://github.com/{repo}.git'])
    tags = [line.rsplit('/', 1)[-1] for line in r.stdout.splitlines() if line.strip() and re.search(r'\d', line)]
    return max(tags, key=verkey) if tags else None


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


def git_info(path: str, do_fetch: bool) -> dict:
    if not path or not os.path.isdir(path):
        return {'error': 'path not found'}
    if sh(['git', '-C', path, 'rev-parse', '--is-inside-work-tree']).stdout.strip() != 'true':
        return {'error': 'not a git repo'}
    if do_fetch:
        sh(['git', '-C', path, 'fetch', '--quiet', 'origin'])
    branch = sh(['git', '-C', path, 'rev-parse', '--abbrev-ref', 'HEAD']).stdout.strip()
    upstream = sh(['git', '-C', path, 'rev-parse', '--abbrev-ref', '--symbolic-full-name', '@{u}']).stdout.strip()
    ref = upstream
    if not ref:
        for cand in ('origin/HEAD', 'origin/main', 'origin/master'):
            probe = sh(['git', '-C', path, 'rev-parse', '--verify', '--quiet', cand])
            if probe.returncode == 0:
                ref = cand
                break
    behind = None
    if ref:
        rc = sh(['git', '-C', path, 'rev-list', '--count', f'HEAD..{ref}'])
        if rc.returncode == 0 and rc.stdout.strip().isdigit():
            behind = int(rc.stdout.strip())
    if ref and behind is not None:
        return {'branch': branch, 'upstream': upstream or None, 'ref': ref, 'behind': behind}

    # No branch upstream: maybe it's a shallow, tag-pinned checkout.
    cur_tag = sh(['git', '-C', path, 'describe', '--tags', '--exact-match', 'HEAD']).stdout.strip()
    latest_tag = None
    if cur_tag:
        listing = sh(['git', '-C', path, 'ls-remote', '--tags', '--refs', 'origin'])
        tags = [
            line.rsplit('/', 1)[-1] for line in listing.stdout.splitlines() if line.strip() and re.search(r'\d', line)
        ]
        if tags:
            latest_tag = max(tags, key=verkey)
    return {
        'branch': branch,
        'upstream': upstream or None,
        'ref': ref,
        'behind': behind,
        'tag': cur_tag or None,
        'latest_tag': latest_tag,
    }


def rewrite_pin(path: Path, new_ref: str) -> bool:
    text = path.read_text()
    new_text, n = re.subn(r'(?m)^(\s*ref\s*=\s*")[^"]*(")', rf'\g<1>{new_ref}\g<2>', text)
    if n:
        path.write_text(new_text)
    return bool(n)


def main() -> int:
    ap = argparse.ArgumentParser(description='Check/apply MCP + runtime updates.')
    ap.add_argument('--apply', action='store_true', help='apply updates (default: report)')
    ap.add_argument('--no-fetch', action='store_true', help='skip git fetch')
    args = ap.parse_args()

    ctx = ms.build_context()
    servers: dict[str, tuple[Path, dict]] = {}
    for path in sorted((CATALOG / 'servers').glob('*.toml')):
        raw = ms.load_toml(path)
        if raw.get('package'):
            servers[raw['name']] = (path, raw['package'])

    uv = uv_outdated()
    rows: list[tuple[str, str, str]] = []
    catalog_bumps: list[tuple[Path, str, str, str]] = []  # path, name, old, new
    uv_upgrades: list[str] = []
    uv_project_bumps: list[tuple[str, str, Path, str, str]] = []  # name, proj, catalog, old, new
    git_pulls: list[tuple[str, str, str]] = []  # name, path, branch
    git_manual: list[tuple[str, str]] = []

    for name, (path, pkg) in servers.items():
        mgr = pkg.get('manager', '?')
        if mgr == 'npm':
            ref = pkg.get('ref') or 'latest'
            latest = npm_latest(pkg['name'], ref)
            if latest is None:
                status = 'npm lookup failed'
            elif ref in FLOATING_TAGS:
                status = f'floating @{ref} (now {latest})'
            elif latest == ref:
                status = f'pinned {ref} (up to date)'
            else:
                status = f'pinned {ref} -> {latest}'
                catalog_bumps.append((path, name, ref, latest))
        elif mgr == 'uv-tool':
            if pkg['name'] in uv:
                cur, latest = uv[pkg['name']]
                status = f'{cur} -> {latest}'
                uv_upgrades.append(pkg['name'])
            else:
                status = 'up to date'
        elif mgr == 'git':
            gp = ms.expand(pkg.get('path', ''), ctx)
            gi = git_info(gp, do_fetch=not args.no_fetch)
            if gi.get('error'):
                status = f'{gi["error"]} ({gp})'
            elif gi['behind'] is not None and gi['behind'] > 0:
                upstream = gi['upstream'] or gi['ref'] or 'origin'
                status = f'{gi["behind"]} commit(s) behind {upstream}'
                if gi['branch'] not in ('HEAD', ''):
                    git_pulls.append((name, gp, gi['branch']))
                else:
                    git_manual.append((name, gp))
            elif gi['behind'] == 0 and not gi.get('tag'):
                status = f'{gi["branch"]} up to date'
            elif gi.get('tag'):
                if gi.get('latest_tag') and gi['latest_tag'] != gi['tag']:
                    status = f'tag {gi["tag"]} -> {gi["latest_tag"]} (tag-pinned - manual)'
                else:
                    status = f'tag {gi["tag"]} (up to date)'
                git_manual.append((name, gp))
            else:
                status = f'{gi["branch"]} (no upstream) - manual'
                git_manual.append((name, gp))
        elif mgr == 'uv-project':
            ref = pkg.get('ref')
            latest = remote_latest_tag(pkg['repo']) if pkg.get('repo') and not args.no_fetch else None
            if latest and latest != ref:
                status = f'{ref} -> {latest}'
                uv_project_bumps.append((name, ms.expand(pkg['path'], ctx), path, ref, latest))
            else:
                status = f'{ref} (up to date)'
        elif mgr == 'source':
            status = 'local source (always current)'
        elif mgr == 'remote':
            status = 'remote (hosted)'
        else:
            status = 'manual'
        rows.append((name, mgr, status))

    width = max((len(r[0]) for r in rows), default=6)
    mwidth = max((len(r[1]) for r in rows), default=7)
    print(f'{"SERVER".ljust(width)}  {"MANAGER".ljust(mwidth)}  STATUS')
    print(f'{"-" * width}  {"-" * mwidth}  {"-" * 40}')
    for name, mgr, status in rows:
        print(f'{name.ljust(width)}  {mgr.ljust(mwidth)}  {status}')

    if not args.apply:
        print()
        if catalog_bumps or uv_upgrades or git_pulls or git_manual:
            print('run `cargo make agent-update` (or agent-update --apply) to apply')
        else:
            print('everything is current')
        return 0

    print('\napplying...')
    changed_catalog = False
    for path, name, old, new in catalog_bumps:
        if rewrite_pin(path, new):
            print(f'  {name}: catalog ref {old} -> {new}')
            changed_catalog = True
    for name, proj, cpath, old, new in uv_project_bumps:
        print(f'  {name}: {old} -> {new} (uv project {proj})')
        bump_uv_project(proj, cpath, old, new)
    for tool in uv_upgrades:
        print(f'  uv tool upgrade {tool}')
        r = sh([UV, 'tool', 'upgrade', tool])
        print(
            '    ' + (r.stdout.strip().splitlines()[-1] if r.returncode == 0 and r.stdout.strip() else r.stderr.strip())
        )
    for name, gp, branch in git_pulls:
        print(f'  git pull {gp} ({branch})')
        r = sh(['git', '-C', gp, 'pull', '--ff-only'])
        print('    ' + (r.stdout.strip().splitlines()[-1] if r.returncode == 0 else r.stderr.strip()))
    for name, gp in git_manual:
        print(f'  {name}: {gp} has no upstream/branch - update manually')

    if changed_catalog:
        print('\nre-syncing configs...')
        subprocess.run([str(REPO / 'bin' / 'agent-sync')])
    elif uv_project_bumps:
        print('\nagent configs unchanged (tag lives in the project); lock refreshed')
    else:
        print('\ncatalog unchanged; no re-sync needed')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
