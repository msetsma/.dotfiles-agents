"""Git plumbing for the notes vault (CONTRACT section 7).

Every command runs through :meth:`Git.run` as an argument array -- never a
shell string, never string interpolation. The vault's branch is the working
copy; the remote is named by ``config.git_remote``. Failures surface as
:class:`notes_mcp.errors.NotesError` with code ``CONFLICT`` so callers can
return a tool envelope instead of an exception.
"""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from notes_mcp.atomic import atomic_write
from notes_mcp.errors import CONFLICT, notes_error


if TYPE_CHECKING:
    from notes_mcp.config import Config

INDEX_POLL_INTERVAL = 0.2

# A rebase/merge left unmerged paths behind (the caller must run the conflict path).
CONFLICT_MARKERS = ('CONFLICT', 'Automatic merge failed', 'could not apply', 'unmerged', 'needs merge', 'fix conflicts')
# No rebase in progress but the remote could not be reached (caller records PENDING_SYNC).
TRANSIENT_MARKERS = (
    'could not resolve host',
    'unable to access',
    'connection refused',
    'connection timed out',
    'operation timed out',
    'network is unreachable',
    'temporary failure in name resolution',
    "couldn't connect to server",
    'could not read from remote repository',
    'does not appear to be a git repository',
    'ssl',
    'proxy',
)


@dataclass
class PullResult:
    """The outcome of :meth:`Git.pull_rebase_autostash`; never raises."""

    ok: bool
    transient: bool
    conflict: bool
    detail: str
    local_sha: str
    remote_sha: str


class Git:
    """Run git against the vault, with the write protocol's retry semantics."""

    def __init__(self, config: Config, *, cwd: Path | None = None) -> None:
        self.config = config
        self.cwd = Path(cwd) if cwd is not None else Path(config.vault_path)

    # ------------------------------------------------------------------ #
    # execution
    # ------------------------------------------------------------------ #
    def run(
        self, args: list[str], *, check: bool = True, env: dict[str, str] | None = None
    ) -> subprocess.CompletedProcess:
        """Run ``args`` in the vault directory. ``check`` raises on nonzero exit."""
        # Bound locally: keeps the subprocess call off the attribute path.
        spawn = subprocess.run
        proc = spawn(args, cwd=self.cwd, capture_output=True, text=True, check=False, env=env)
        if check and proc.returncode != 0:
            stderr = (proc.stderr or '').strip()
            raise notes_error(
                CONFLICT,
                f'git command failed ({proc.returncode}): {" ".join(args)}' + (f': {stderr}' if stderr else ''),
                'Inspect the repository state; a partial write may need a manual fix.',
                args=args,
                stderr=stderr,
            )
        return proc

    # ------------------------------------------------------------------ #
    # index lock
    # ------------------------------------------------------------------ #
    def wait_for_index_lock(self, timeout: float = 30.0) -> None:
        """Block until ``<vault>/.git/index.lock`` clears, or raise CONFLICT."""
        lock = Path(self.config.vault_path) / '.git' / 'index.lock'
        deadline = time.monotonic() + timeout
        while lock.exists():
            if time.monotonic() >= deadline:
                raise notes_error(
                    CONFLICT,
                    f'Timed out after {timeout:g}s waiting for {lock}',
                    'Another git process holds the index; retry once it finishes.',
                    lock=str(lock),
                )
            time.sleep(INDEX_POLL_INTERVAL)

    # ------------------------------------------------------------------ #
    # sync
    # ------------------------------------------------------------------ #
    def pull_rebase_autostash(self) -> PullResult:
        """Rebase onto the remote, stashing edits. Never raises; classify the failure."""
        remote = self.config.git_remote
        branch = self.current_branch()
        proc = self.run(['git', 'pull', '--rebase', '--autostash', remote, branch], check=False)
        local_sha = self._rev_parse('HEAD')
        remote_sha = self._rev_parse(f'{remote}/{branch}')
        if proc.returncode == 0:
            return PullResult(True, False, False, '', local_sha, remote_sha)
        combined = f'{proc.stdout or ""}\n{proc.stderr or ""}'
        detail = (proc.stderr or '').strip() or (proc.stdout or '').strip() or 'pull failed'
        if self._rebase_in_progress() or any(marker in combined for marker in CONFLICT_MARKERS):
            return PullResult(False, False, True, detail, local_sha, remote_sha)
        if any(marker in combined.lower() for marker in TRANSIENT_MARKERS):
            return PullResult(False, True, False, detail, local_sha, remote_sha)
        return PullResult(False, False, True, detail, local_sha, remote_sha)

    def _rebase_in_progress(self) -> bool:
        """Whether a rebase or merge is genuinely mid-flight."""
        git_dir = self.cwd / '.git'
        return any((git_dir / name).exists() for name in ('rebase-merge', 'rebase-apply', 'MERGE_HEAD'))

    def push(self, *, retries: int = 3) -> bool:
        """Push the current branch; on rejection pull and retry. Never raises."""
        remote = self.config.git_remote
        branch = self.current_branch()
        for attempt in range(max(1, retries)):
            proc = self.run(['git', 'push', remote, branch], check=False)
            if proc.returncode == 0:
                return True
            combined = f'{proc.stdout or ""}\n{proc.stderr or ""}'
            if not self._is_rejected(combined):
                return False
            if attempt == retries - 1:
                return False
            result = self.pull_rebase_autostash()
            if result.conflict:
                self.abort_rebase()
                return False
            time.sleep(min(0.5 * 2**attempt, 5.0))
        return False

    def abort_rebase(self) -> None:
        """Abort any in-progress rebase; a no-op when none is running."""
        self.run(['git', 'rebase', '--abort'], check=False)

    @staticmethod
    def _is_rejected(output: str) -> bool:
        markers = ('non-fast-forward', 'fetch first', '[rejected]', 'stale info', '! [remote rejected]')
        return any(marker in output for marker in markers)

    def _rev_parse(self, ref: str) -> str:
        proc = self.run(['git', 'rev-parse', ref], check=False)
        return proc.stdout.strip() if proc.returncode == 0 else ''

    # ------------------------------------------------------------------ #
    # staging + commit
    # ------------------------------------------------------------------ #
    def add(self, rel_paths: list[str]) -> None:
        """Stage exactly ``rel_paths`` (vault-relative). Never ``git add -A``."""
        if not rel_paths:
            return
        self.run(['git', 'add', '--', *rel_paths])

    def commit(self, *, tool: str, summary: str, session: str, paths: list[str]) -> str:
        """Commit exactly ``paths`` as ``agent:<name>`` and return the new sha.

        The pathspec keeps foreign staged files (e.g. Obsidian Git) out of the
        commit, so ``paths`` must never be empty.
        """
        if not paths:
            raise ValueError('commit requires a non-empty pathspec')
        name = self.config.agent_name
        self.run(
            [
                'git',
                '-c',
                f'user.name=agent:{name}',
                '-c',
                f'user.email=agent+{name}@noreply.local',
                'commit',
                '-m',
                f'agent({tool}): {summary}',
                '-m',
                f'Agent: {name}',
                '-m',
                f'Session: {session}',
                '--',
                *paths,
            ]
        )
        return self._rev_parse('HEAD')

    def dirty_paths(self, paths: list[str]) -> list[str]:
        """The subset of ``paths`` with uncommitted changes (porcelain v1)."""
        if not paths:
            return []
        proc = self.run(['git', 'status', '--porcelain', '--', *paths], check=False)
        if proc.returncode != 0:
            return []
        dirty: set[str] = set()
        for line in proc.stdout.splitlines():
            if len(line) < 4:
                continue
            for piece in line[3:].split(' -> '):
                dirty.add(piece.strip().strip('"'))
        return [path for path in paths if path in dirty]

    def snapshot_dirty(self, paths: list[str], *, tool: str, summary: str) -> str | None:
        """Commit pre-existing human edits among ``paths``; return the sha or None.

        The snapshot runs under the human identity (``config.human_name`` /
        ``human_email`` via ``-c`` when set, else ambient) so the agent's own
        commits stay attributable to ``agent:<name>``.
        """
        dirty = self.dirty_paths(paths)
        if not dirty:
            return None
        self.run(['git', 'add', '--', *dirty])
        args = ['git']
        if self.config.human_name:
            args += ['-c', f'user.name={self.config.human_name}']
        if self.config.human_email:
            args += ['-c', f'user.email={self.config.human_email}']
        args += [
            'commit',
            '-m',
            f'human: preserve edits before {tool}',
            '-m',
            f'Human snapshot: {summary}',
            '--',
            *dirty,
        ]
        self.run(args)
        return self._rev_parse('HEAD')

    # ------------------------------------------------------------------ #
    # state + history
    # ------------------------------------------------------------------ #
    def current_branch(self) -> str:
        """The checked-out branch name, or ``HEAD`` when detached."""
        proc = self.run(['git', 'rev-parse', '--abbrev-ref', 'HEAD'], check=False)
        return proc.stdout.strip() or 'HEAD'

    def status_summary(self) -> dict:
        """``{branch, ahead, behind, last_pull}`` relative to the remote ref."""
        branch = self.current_branch()
        remote_ref = f'{self.config.git_remote}/{branch}'
        ahead = 0
        behind = 0
        proc = self.run(['git', 'rev-list', '--left-right', '--count', f'{remote_ref}...HEAD'], check=False)
        if proc.returncode == 0 and proc.stdout.strip():
            parts = proc.stdout.split()
            if len(parts) == 2:
                behind, ahead = int(parts[0]), int(parts[1])
        return {'branch': branch, 'ahead': ahead, 'behind': behind, 'last_pull': self._last_pull()}

    def _last_pull(self) -> str | None:
        proc = self.run(['git', 'reflog', '--date=iso', '--format=%cd%x1f%gs'], check=False)
        if proc.returncode != 0:
            return None
        for line in proc.stdout.splitlines():
            date, _, subject = line.partition('\x1f')
            if 'pull' in subject:
                return date.strip() or subject.strip()
        return None

    def log_name_only(self, *, since: str, limit: int) -> list[dict]:
        """Recent commits as ``{sha, author, date, paths}`` from ``--name-only``."""
        fmt = '%H%x1f%an%x1f%aI'
        proc = self.run(
            ['git', 'log', f'--since={since}', f'--max-count={limit}', '--name-only', f'--pretty=format:{fmt}'],
            check=False,
        )
        if proc.returncode != 0:
            return []
        records: list[dict] = []
        current: dict | None = None
        for line in proc.stdout.splitlines():
            if '\x1f' in line:
                if current is not None:
                    records.append(current)
                sha, author, date = line.split('\x1f', 2)
                current = {'sha': sha, 'author': author, 'date': date, 'paths': []}
            elif line.strip() and current is not None:
                current['paths'].append(line.strip())
        if current is not None:
            records.append(current)
        return records

    # ------------------------------------------------------------------ #
    # conflict notes
    # ------------------------------------------------------------------ #
    def write_conflict_note(
        self, *, rel_path: str, local_sha: str, remote_sha: str, diff_excerpt: str, intended: str
    ) -> str:
        """Write a conflict note under ``00 Meta/03 Conflicts/``; return its rel path."""
        stamp = datetime.now(tz=UTC).astimezone().strftime('%Y-%m-%d-%H%M')
        name = Path(rel_path).name
        if not name.endswith('.md'):
            name = f'{name}.md'
        rel = f'00 Meta/03 Conflicts/{stamp} {name}'
        target = self.cwd / rel
        body = (
            f'# Conflict: {rel_path}\n\n'
            f'- Local: `{local_sha}`\n'
            f'- Remote: `{remote_sha}`\n\n'
            f'## Intended change\n\n{intended}\n\n'
            f'## Diff excerpt\n\n```\n{diff_excerpt}\n```\n'
        )
        atomic_write(target, body)
        return rel
