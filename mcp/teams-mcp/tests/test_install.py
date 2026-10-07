import json

import pytest

from teams_browser.mcp import install


def test_snippet_shapes():
    assert 'mcpServers' in install.snippet('claude-code')
    assert 'servers' in install.snippet('vscode')
    opencode = install.snippet('opencode')['mcp']['teams-browser']
    assert opencode['type'] == 'local'
    assert isinstance(opencode['command'], list)


def test_install_merges_existing(tmp_path):
    config = tmp_path / 'config.json'
    config.write_text(json.dumps({'mcpServers': {'existing': {'command': 'foo'}}}))

    result = install.install('claude-code', path=config)
    assert result['written'] is True

    written = json.loads(config.read_text())
    assert written['mcpServers']['existing'] == {'command': 'foo'}
    assert 'teams-browser' in written['mcpServers']


def test_install_print_only_does_not_write(tmp_path):
    config = tmp_path / 'config.json'
    result = install.install('cursor', path=config, print_only=True)
    assert result['written'] is False
    assert not config.exists()


def test_install_rejects_invalid_json(tmp_path):
    config = tmp_path / 'config.json'
    config.write_text('{ not json')
    with pytest.raises(ValueError, match='is not valid JSON'):
        install.install('claude-code', path=config)


def test_unknown_client():
    with pytest.raises(ValueError, match="Unknown client 'nope'"):
        install.snippet('nope')


def test_describe_clients_covers_all():
    described = {c['client'] for c in install.describe_clients()}
    assert described == set(install.CLIENTS)
