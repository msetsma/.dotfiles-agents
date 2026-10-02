"""Shared HTTP plumbing for the internal Teams/Substrate APIs.

Thin wrapper over ``httpx`` adding JSON handling, polite jitter, exponential
backoff with ``Retry-After`` support, and translation of HTTP failures into the
package's error types.
"""

from __future__ import annotations

import random
import time
from typing import Any

import httpx

from ..config import MAX_RETRIES, request_timeout
from ..errors import ApiError, RateLimited, TokenExpired
from ..models import TokenSet

_RETRYABLE_STATUS = {429, 500, 502, 503, 504}

# Required to select the flexible schema that embeds transcript/file properties.
_SUBSTRATE_PREFER = (
    'substrate.flexibleschema,outlook.data-source="Substrate",'
    'exchange.behavior="SubstrateFiles"'
)


def substrate_headers(tokens: TokenSet) -> dict[str, str]:
    """Auth + content negotiation for the Substrate ``WorkingSetFiles`` API."""
    if not tokens.substrate:
        raise TokenExpired(
            "No Substrate token in the session. Run `teams-browser login` to refresh."
        )
    return {
        "Authorization": f"Bearer {tokens.substrate.token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Prefer": _SUBSTRATE_PREFER,
    }


class HttpClient:
    def __init__(self, *, timeout: int | None = None, max_retries: int = MAX_RETRIES):
        self._client = httpx.Client(
            timeout=timeout or request_timeout(),
            follow_redirects=True,
            headers={"User-Agent": "teams-browser/0.1"},
        )
        self._max_retries = max_retries

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "HttpClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        params: dict[str, str] | None = None,
        json_body: Any | None = None,
    ) -> httpx.Response:
        last_error: Exception | None = None
        for attempt in range(self._max_retries):
            try:
                response = self._client.request(
                    method, url, headers=headers, params=params, json=json_body
                )
            except httpx.HTTPError as exc:
                last_error = exc
                self._sleep(attempt)
                continue

            if response.status_code in _RETRYABLE_STATUS and attempt < self._max_retries - 1:
                retry_after = _retry_after(response)
                if retry_after is not None:
                    time.sleep(min(retry_after, 30))
                else:
                    self._sleep(attempt)
                last_error = RateLimited("upstream throttled", retry_after)
                continue

            if response.status_code == 401:
                raise TokenExpired(
                    "Upstream returned 401 - the session token is no longer valid. "
                    "Run `teams-browser login` (or let it auto-refresh)."
                )
            if response.status_code >= 400:
                raise ApiError(response.status_code, response.reason_phrase, body=response.text)

            return response

        raise ApiError(0, f"request failed after {self._max_retries} attempts: {last_error}")

    def get_json(self, url: str, **kwargs: Any) -> Any:
        return self.request("GET", url, **kwargs).json()

    def _sleep(self, attempt: int) -> None:
        base = min(1.0 * (2**attempt), 10.0)
        time.sleep(base * (0.5 + random.random() * 0.5))


def _retry_after(response: httpx.Response) -> float | None:
    value = response.headers.get("Retry-After")
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None
