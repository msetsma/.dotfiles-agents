"""qmd CLI adapter (CONTRACT section 8).

``Searcher`` shells out to the ``qmd`` binary (verified against 2.8.3). All
invocations use argument arrays, never a shell string, and carry a timeout.
``QMD_EMBED_MODEL`` / ``QMD_RERANK_MODEL`` / ``QMD_GENERATE_MODEL`` are passed
through to the child env verbatim when configured, so model selection stays
offline and deterministic.

The JSON payload from ``qmd search`` / ``qmd query`` is a list of objects::

    {"docid", "score", "file": "qmd://<collection>/<relpath>",
     "line", "title", "context", "snippet"}

``file`` is normalized to a vault-relative POSIX path.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import subprocess
import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING

from notes_mcp.errors import INDEX_UNAVAILABLE, notes_error


if TYPE_CHECKING:
    from notes_mcp.config import Config

QMD_BIN = 'qmd'
DEFAULT_TIMEOUT = 180.0


@dataclass
class SearchHit:
    """One normalized search result."""

    path: str
    title: str | None
    score: float | None
    snippet: str | None


class Searcher:
    """Thin, fail-closed adapter over the ``qmd`` CLI."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self._timer: threading.Timer | None = None
        self._timer_lock = threading.Lock()

    # ------------------------------------------------------------------ #
    # search
    # ------------------------------------------------------------------ #
    def search(
        self,
        query: str,
        *,
        collection: str | None = None,
        filter_: dict | None = None,
        limit: int = 10,
        rerank: bool = True,
        **options: object,
    ) -> list[SearchHit]:
        """Run a BM25 (``rerank=False``) or hybrid+rerank query.

        CONTRACT names the third parameter ``filter``; the clean-python gate
        forbids shadowing the builtin, so the keyword is exposed as ``filter_``
        and ``filter=`` is still accepted (and ignored) for compatibility.

        NOTE: qmd's ``--filter`` is unreliable -- a probe with a genuinely
        matching filter returned ``[]`` -- so the filter is never passed to
        qmd. Callers post-filter hits by path/metadata instead.
        """
        if 'filter' in options:
            filter_ = options.pop('filter')  # type: ignore[assignment]
        if options:
            unknown = ', '.join(sorted(options))
            raise TypeError(f'unexpected keyword argument(s): {unknown}')
        del filter_  # accepted for interface compatibility only; see note above

        coll = self._collection(collection)
        subcommand = 'query' if rerank else 'search'
        args = [subcommand, query, '-c', coll, '--json', '-n', str(limit)]
        proc = self._run(args)
        self._require_ok(proc, subcommand, args)

        try:
            payload = json.loads(proc.stdout or '[]')
        except json.JSONDecodeError as exc:
            raise notes_error(
                INDEX_UNAVAILABLE,
                f'qmd {subcommand} returned unparseable JSON: {exc}',
                'Re-run `qmd update` and `qmd embed`; if it persists, check `qmd status` and the qmd version.',
                args=args,
            ) from exc

        if not isinstance(payload, list):
            raise notes_error(
                INDEX_UNAVAILABLE,
                f'qmd {subcommand} returned {type(payload).__name__}, expected a list',
                'Check `qmd status`; re-run `qmd update`.',
                args=args,
            )

        return [self._to_hit(item, coll) for item in payload if isinstance(item, dict)]

    # ------------------------------------------------------------------ #
    # reindex
    # ------------------------------------------------------------------ #
    def reindex(self, *, embed: bool = False) -> None:
        """Refresh the text index; optionally refresh vector embeddings too."""
        proc = self._run(['update'])
        self._require_ok(proc, 'update', ['update'])
        if embed:
            proc = self._run(['embed'])
            self._require_ok(proc, 'embed', ['embed'])

    def schedule_reindex(self, *, delay: float = 5.0) -> None:
        """Debounced background reindex that never raises into the caller.

        Repeated calls before the delay elapses coalesce into a single pending
        run: the previous timer is cancelled and replaced.
        """
        with self._timer_lock:
            if self._timer is not None:
                self._timer.cancel()
            timer = threading.Timer(delay, self._run_scheduled)
            timer.daemon = True
            self._timer = timer
            timer.start()

    def _run_scheduled(self) -> None:
        with self._timer_lock:
            self._timer = None
        with contextlib.suppress(Exception):
            self.reindex()

    # ------------------------------------------------------------------ #
    # health
    # ------------------------------------------------------------------ #
    def health(self) -> dict:
        """Best-effort health snapshot; never raises."""
        result: dict = {
            'available': False,
            'version': None,
            'collection': self._collection(None),
            'indexed_at': None,
            'models': {},
        }
        try:
            if shutil.which(QMD_BIN) is None:
                return result
            result['available'] = True

            version = self._run(['--version'])
            if version.returncode == 0:
                result['version'] = self._parse_version(version.stdout)

            status = self._run(['status'])
            if status.returncode == 0:
                result['indexed_at'] = self._parse_indexed_at(status.stdout)
                result['models'] = self._parse_models(status.stdout)
        except Exception:
            return result
        return result

    # ------------------------------------------------------------------ #
    # internals
    # ------------------------------------------------------------------ #
    def _collection(self, collection: str | None) -> str:
        return collection or self.config.qmd_collection

    def _env(self) -> dict[str, str]:
        env = dict(os.environ)
        for attr, var in (
            ('qmd_embed_model', 'QMD_EMBED_MODEL'),
            ('qmd_rerank_model', 'QMD_RERANK_MODEL'),
            ('qmd_generate_model', 'QMD_GENERATE_MODEL'),
        ):
            value = getattr(self.config, attr, None)
            if value:
                env[var] = value
        return env

    def _run(self, args: list[str]) -> subprocess.CompletedProcess:
        exe = shutil.which(QMD_BIN)
        if exe is None:
            raise notes_error(
                INDEX_UNAVAILABLE,
                'qmd binary not found on PATH',
                'Install qmd (2.8.3) and ensure it is on PATH; then run `qmd update` and `qmd embed`.',
                args=args,
            )
        # Resolved at call time so tests can monkeypatch subprocess.run.
        run = subprocess.run
        try:
            return run(
                [exe, *args], capture_output=True, text=True, timeout=DEFAULT_TIMEOUT, check=False, env=self._env()
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise notes_error(
                INDEX_UNAVAILABLE,
                f'qmd invocation failed: {exc}',
                'Verify the qmd install and index (`qmd status`); model '
                'failures are usually fixed by `qmd pull` or `qmd embed`.',
                args=args,
            ) from exc

    @staticmethod
    def _require_ok(proc: subprocess.CompletedProcess, subcommand: str, args: list[str]) -> None:
        if proc.returncode == 0:
            return
        stderr = (proc.stderr or '').strip()
        detail = f': {stderr}' if stderr else ''
        raise notes_error(
            INDEX_UNAVAILABLE,
            f'qmd {subcommand} exited {proc.returncode}{detail}',
            'Re-run `qmd update` and `qmd embed`; if a model failed, run '
            '`qmd pull`. Check `qmd status` and the qmd version.',
            args=args,
        )

    @staticmethod
    def _normalize_path(raw: str | None, collection: str) -> str:
        if not raw:
            return ''
        prefix = f'qmd://{collection}/'
        if raw.startswith(prefix):
            return raw[len(prefix) :]
        if raw.startswith('qmd://'):
            remainder = raw[len('qmd://') :].split('/', 1)
            return remainder[1] if len(remainder) == 2 else ''
        return raw

    def _to_hit(self, item: dict, collection: str) -> SearchHit:
        score = item.get('score')
        return SearchHit(
            path=self._normalize_path(item.get('file'), collection),
            title=item.get('title'),
            score=float(score) if score is not None else None,
            snippet=item.get('snippet'),
        )

    @staticmethod
    def _parse_version(stdout: str) -> str | None:
        # Version output looks like: qmd 2.8.3 (facd35e)
        parts = (stdout or '').split()
        return parts[1] if len(parts) > 1 else None

    @staticmethod
    def _parse_indexed_at(stdout: str) -> str | None:
        for line in (stdout or '').splitlines():
            stripped = line.strip()
            if stripped.startswith('Updated:'):
                return stripped.partition(':')[2].strip() or None
        return None

    @staticmethod
    def _parse_models(stdout: str) -> dict[str, str]:
        models: dict[str, str] = {}
        lines = (stdout or '').splitlines()
        for index, line in enumerate(lines):
            if line.strip() != 'Models':
                continue
            for detail in lines[index + 1 :]:
                stripped = detail.strip()
                key, sep, value = stripped.partition(':')
                if sep and key in ('Embedding', 'Reranking', 'Generation'):
                    models[key.lower()] = value.strip()
                elif stripped and not sep and key != 'Models':
                    break
            break
        return models
