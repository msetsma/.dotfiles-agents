"""Refresh behaviour: the MCP server must never block on a browser."""

import pytest

from teams_browser.auth import login as login_module
from teams_browser.auth import refresh as refresh_module
from teams_browser.auth.session import save_session
from teams_browser.client import TeamsClient
from teams_browser.config import Paths
from teams_browser.errors import AuthRequired
from teams_browser.mcp import server as mcp_server

from .helpers import make_session


def _paths(tmp_path):
    root = tmp_path / "home"
    return Paths(
        root=root,
        profile_dir=root / "profile",
        session_file=root / "session.enc",
        cache_file=root / "tokens.enc",
        key_file=root / "secret.key",
        db_file=root / "archive.db",
    )


def test_refresh_without_browser_fallback_fails_fast(tmp_path, monkeypatch):
    paths = _paths(tmp_path)
    save_session(make_session(), paths)
    monkeypatch.setattr(refresh_module, "refresh_via_http", lambda *a, **k: False)

    def _forbidden(**kwargs):
        raise AssertionError("headless browser must not be launched")

    monkeypatch.setattr(login_module, "refresh_session_headless", _forbidden)

    client = TeamsClient(paths=paths, browser_refresh=False)
    with pytest.raises(AuthRequired, match="teams-browser login"):
        client.refresh()


def test_refresh_with_browser_fallback_uses_browser(tmp_path, monkeypatch):
    paths = _paths(tmp_path)
    save_session(make_session(), paths)
    monkeypatch.setattr(refresh_module, "refresh_via_http", lambda *a, **k: False)
    called = []
    monkeypatch.setattr(
        login_module, "refresh_session_headless", lambda **k: called.append(True)
    )

    client = TeamsClient(paths=paths)
    assert client.refresh() == "browser"
    assert called


def test_mcp_server_disables_browser_refresh():
    client = mcp_server._client()
    try:
        assert client.auto_refresh is True
        assert client.browser_refresh is False
    finally:
        client.close()


def test_start_login_spawns_detached_process(monkeypatch):
    spawned = []

    class FakePopen:
        def __init__(self, args, **kwargs):
            spawned.append((args, kwargs))
            self.pid = 1234

        def poll(self):
            return None  # still running

    monkeypatch.setattr(mcp_server.subprocess, "Popen", FakePopen)
    monkeypatch.setattr(mcp_server, "_login_process", None)

    result = mcp_server.start_login()
    assert result["started"] is True
    args, kwargs = spawned[0]
    assert args[1:] == ["-m", "teams_browser.cli", "login"]
    assert kwargs["start_new_session"] is True

    again = mcp_server.start_login()
    assert again["started"] is False
    assert again["already_running"] is True
    assert len(spawned) == 1  # no second window while one is open
