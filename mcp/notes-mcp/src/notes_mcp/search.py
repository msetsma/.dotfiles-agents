"""qmd adapter: HTTP daemon first, CLI fallback (CONTRACT section 8).

``Searcher`` prefers the live qmd MCP daemon (``config.qmd_daemon_url``) over
shelling out to the ``qmd`` binary. The daemon is spoken to over JSON-RPC
(MCP Streamable-HTTP): a ``tools/call`` named ``query`` whose SSE ``data:``
frame carries ``result.structuredContent.results``. On any daemon failure --
connection refused, timeout, non-2xx, unparseable body, missing results -- it
falls back to the CLI. Daemon disabled (``None``) goes straight to the CLI.

All CLI invocations use argument arrays, never a shell string, and carry a
timeout drawn from ``config.qmd_timeout``. ``QMD_EMBED_MODEL`` /
``QMD_RERANK_MODEL`` / ``QMD_GENERATE_MODEL`` are passed through to the child
env verbatim when configured, so model selection stays offline and
deterministic.

Search results are normalized to vault-relative POSIX paths: a leading
``<collection>/`` or ``qmd://<collection>/`` prefix is stripped.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import subprocess
import threading
import urllib.request
from dataclasses import dataclass
from typing import TYPE_CHECKING
from urllib.parse import urlsplit, urlunsplit

from notes_mcp.errors import INDEX_UNAVAILABLE, notes_error


if TYPE_CHECKING:
    from notes_mcp.config import Config

QMD_BIN = 'qmd'
DEFAULT_TIMEOUT = 180.0
HEALTH_TIMEOUT = 5.0


@dataclass
class SearchHit:
    """One normalized search result."""

    path: str
    title: str | None
    score: float | None
    snippet: str | None


def _http_post_json(url: str, payload: dict, *, timeout: float) -> str:
    """POST ``payload`` as JSON and return the response body text.

    Module-level so tests can patch it and never touch the network.
    """
    body = json.dumps(payload).encode('utf-8')
    opener = urllib.request.build_opener()
    opener.addheaders = [('Content-Type', 'application/json'), ('Accept', 'application/json, text/event-stream')]
    with opener.open(url, data=body, timeout=timeout) as response:
        return response.read().decode('utf-8')


def _http_get(url: str, *, timeout: float) -> str:
    """GET ``url`` and return the body text. Module-level for test patching."""
    opener = urllib.request.build_opener()
    with opener.open(url, timeout=timeout) as response:
        return response.read().decode('utf-8')


class Searcher:
    """Adapter over the qmd daemon with a fail-closed CLI fallback."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self._timer: threading.Timer | None = None
        self._timer_lock = threading.Lock()

    # ------------------------------------------------------------------ #
    # search
    # ------------------------------------------------------------------ #
    def search(
        self, query: str, *, collection: str | None = None, limit: int = 10, rerank: bool = True
    ) -> list[SearchHit]:
        """Run a BM25 (``rerank=False``) or hybrid+rerank query.

        Tries the daemon first when configured; any failure falls back to the
        CLI. ``rerank=False`` is lex-only (BM25), ``rerank=True`` adds a vector
        leg (hybrid rerank).
        """
        coll = self._collection(collection)
        if self.config.qmd_daemon_url:
            with contextlib.suppress(Exception):
                return self._daemon_search(query, coll, limit, rerank)
        return self._cli_search(query, coll, limit, rerank)

    def _daemon_search(self, query: str, coll: str, limit: int, rerank: bool) -> list[SearchHit]:
        searches: list[dict[str, str]] = [{'type': 'lex', 'query': query}]
        if rerank:
            searches.append({'type': 'vec', 'query': query})
        arguments = {'searches': searches, 'intent': query, 'collections': [coll], 'limit': limit}
        payload = {
            'jsonrpc': '2.0',
            'id': 1,
            'method': 'tools/call',
            'params': {'name': 'query', 'arguments': arguments},
        }
        body = _http_post_json(self.config.qmd_daemon_url, payload, timeout=self._timeout())
        data = self._parse_daemon_body(body)
        results = data['result']['structuredContent']['results']
        if not isinstance(results, list):
            raise TypeError('daemon results was not a list')
        return [self._to_hit(item, coll) for item in results if isinstance(item, dict)]

    def _cli_search(self, query: str, coll: str, limit: int, rerank: bool) -> list[SearchHit]:
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
    def reindex(self, *, embed: bool | None = None) -> None:
        """Refresh the text index; optionally refresh vector embeddings too.

        When ``embed`` is ``None`` the ``config.qmd_embed_on_write`` default
        decides whether ``qmd embed`` runs after ``qmd update``.
        """
        if embed is None:
            embed = bool(getattr(self.config, 'qmd_embed_on_write', True))
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
            'daemon': self._daemon_health(),
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

    def _daemon_health(self) -> dict:
        url = getattr(self.config, 'qmd_daemon_url', None)
        if not url:
            return {'up': False, 'url': None}
        up = False
        try:
            _http_get(self._health_url(url), timeout=HEALTH_TIMEOUT)
            up = True
        except Exception:
            up = False
        return {'up': up, 'url': url}

    @staticmethod
    def _health_url(url: str) -> str:
        parts = urlsplit(url)
        return urlunsplit((parts.scheme, parts.netloc, '/health', '', ''))

    # ------------------------------------------------------------------ #
    # internals
    # ------------------------------------------------------------------ #
    def _collection(self, collection: str | None) -> str:
        return collection or self.config.qmd_collection

    def _timeout(self) -> float:
        value = getattr(self.config, 'qmd_timeout', None)
        return float(value) if value else DEFAULT_TIMEOUT

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
                [exe, *args], capture_output=True, text=True, timeout=self._timeout(), check=False, env=self._env()
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
        for prefix in (f'qmd://{collection}/', f'{collection}/'):
            if raw.startswith(prefix):
                return raw[len(prefix) :]
        if raw.startswith('qmd://'):
            remainder = raw[len('qmd://') :].split('/', 1)
            return remainder[1] if len(remainder) == 2 else ''
        return raw

    @staticmethod
    def _parse_daemon_body(text: str) -> dict:
        stripped = (text or '').strip()
        if stripped.startswith('{'):
            return json.loads(stripped)
        for line in stripped.splitlines():
            if line.startswith('data:'):
                return json.loads(line[len('data:') :].strip())
        raise ValueError('daemon response carried no data frame')

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
