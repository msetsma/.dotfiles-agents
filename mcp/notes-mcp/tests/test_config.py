"""Tests for notes_mcp.config."""

from __future__ import annotations

import pytest

from notes_mcp.config import Config
from notes_mcp.errors import CONFIG_ERROR, NotesError


def test_from_env_defaults(tmp_path, monkeypatch):
    monkeypatch.setenv('VAULT_PATH', str(tmp_path))

    config = Config.from_env()

    assert config.vault_path == tmp_path.resolve()
    assert config.agent_name == 'claude'
    assert config.git_remote == 'origin'
    assert config.qmd_collection == 'notes'
    assert config.qmd_embed_model is None
    assert config.qmd_rerank_model is None
    assert config.qmd_generate_model is None
    assert config.session == 'unknown'


def test_from_env_overrides(tmp_path):
    env = {
        'VAULT_PATH': str(tmp_path),
        'AGENT_NAME': 'codex',
        'GIT_REMOTE': 'upstream',
        'QMD_COLLECTION': 'vault',
        'QMD_EMBED_MODEL': 'file:///models/embed.gguf',
        'QMD_RERANK_MODEL': 'file:///models/rerank.gguf',
        'QMD_GENERATE_MODEL': 'file:///models/gen.gguf',
        'AGENT_SESSION': 'sess-42',
    }

    config = Config.from_env(env)

    assert config.agent_name == 'codex'
    assert config.git_remote == 'upstream'
    assert config.qmd_collection == 'vault'
    assert config.qmd_embed_model == 'file:///models/embed.gguf'
    assert config.qmd_rerank_model == 'file:///models/rerank.gguf'
    assert config.qmd_generate_model == 'file:///models/gen.gguf'
    assert config.session == 'sess-42'


def test_from_env_empty_optional_falls_back_to_default(tmp_path, monkeypatch):
    env = {'VAULT_PATH': str(tmp_path), 'AGENT_NAME': '', 'QMD_EMBED_MODEL': '', 'AGENT_SESSION': ''}

    config = Config.from_env(env)

    assert config.agent_name == 'claude'
    assert config.qmd_embed_model is None
    assert config.session == 'unknown'


def test_from_env_dir_overrides(tmp_path):
    env = {
        'VAULT_PATH': str(tmp_path),
        'NOTES_DAILY_DIR': '40 Journal/41 Daily',
        'NOTES_WEEKLY_DIR': '40 Journal/42 Weekly',
        'NOTES_MEETINGS_DIR': '40 Journal/43 Meetings',
        'NOTES_TEMPLATES_DIR': '00 Meta/01 Templates',
        'NOTES_ATTACHMENTS_DIR': '00 Meta/02 Attachments',
    }

    config = Config.from_env(env)

    assert config.daily_dir == '40 Journal/41 Daily'
    assert config.weekly_dir == '40 Journal/42 Weekly'
    assert config.meetings_dir == '40 Journal/43 Meetings'
    assert config.templates_dir == '00 Meta/01 Templates'
    assert config.attachments_dir == '00 Meta/02 Attachments'


def test_from_env_dir_overrides_default_to_none(tmp_path, monkeypatch):
    monkeypatch.setenv('VAULT_PATH', str(tmp_path))

    config = Config.from_env()

    assert config.daily_dir is None
    assert config.weekly_dir is None
    assert config.meetings_dir is None
    assert config.templates_dir is None
    assert config.attachments_dir is None


def test_from_env_empty_dir_overrides_are_none(tmp_path):
    env = {
        'VAULT_PATH': str(tmp_path),
        'NOTES_DAILY_DIR': '',
        'NOTES_WEEKLY_DIR': '',
        'NOTES_MEETINGS_DIR': '',
        'NOTES_TEMPLATES_DIR': '',
        'NOTES_ATTACHMENTS_DIR': '',
    }

    config = Config.from_env(env)

    assert config.daily_dir is None
    assert config.weekly_dir is None
    assert config.meetings_dir is None
    assert config.templates_dir is None
    assert config.attachments_dir is None


def test_missing_vault_path_is_config_error():
    with pytest.raises(NotesError) as excinfo:
        Config.from_env({})

    assert excinfo.value.code == CONFIG_ERROR


def test_nonexistent_vault_path_is_config_error(tmp_path):
    missing = tmp_path / 'does-not-exist'

    with pytest.raises(NotesError) as excinfo:
        Config.from_env({'VAULT_PATH': str(missing)})

    assert excinfo.value.code == CONFIG_ERROR


def test_vault_path_expands_user(tmp_path, monkeypatch):
    monkeypatch.setenv('HOME', str(tmp_path))
    (tmp_path / 'notes').mkdir()
    monkeypatch.setenv('VAULT_PATH', '~/notes')

    config = Config.from_env()

    assert config.vault_path == (tmp_path / 'notes').resolve()
