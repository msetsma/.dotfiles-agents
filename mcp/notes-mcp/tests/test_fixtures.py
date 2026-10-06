"""Tests for the offline vault fixture itself."""

from __future__ import annotations

import subprocess
from pathlib import Path

from tests.fixtures.vault_fixture import VaultFixture


EXPECTED_FILES = (
    '00 Meta/00.00 Index.md',
    '00 Meta/01 Templates/Daily.md',
    '00 Meta/01 Templates/Project Hub.md',
    '00 Meta/01 Templates/Decision.md',
    '10 Projects/11 Project A/11 Project A.md',
    '20 Areas/21 Platform Engineering/21 Platform Engineering.md',
    '10 Projects/11 Project A/Meeting Notes 2026-01-05.md',
    '30 Resources/33 Concepts/Johnny Decimal.md',
    '40 Journal/41 Daily/2026-01-05.md',
)


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(['/usr/bin/git', *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def test_build_creates_expected_files(vault: VaultFixture) -> None:
    for rel in EXPECTED_FILES:
        assert (vault.human / rel).is_file(), f'human missing {rel}'
        assert (vault.agent / rel).is_file(), f'agent missing {rel}'


def test_agent_origin_points_at_bare_remote(vault: VaultFixture) -> None:
    url = _git(vault.agent, 'remote', 'get-url', 'origin')
    assert Path(url) == vault.remote
    assert url == str(vault.remote)


def test_human_and_agent_track_origin(vault: VaultFixture) -> None:
    for clone in (vault.human, vault.agent):
        assert _git(clone, 'rev-parse', '--abbrev-ref', 'main@{upstream}') == 'origin/main'


def test_hub_note_has_scope_and_qmd_mirror(vault: VaultFixture) -> None:
    text = (vault.agent / '10 Projects/11 Project A/11 Project A.md').read_text(encoding='utf-8')
    assert 'type: hub' in text
    assert 'scope: Placeholder project' in text
    assert 'qmd:' in text
    assert '  metadata:' in text
    assert '    type: hub' in text
    assert '    status: active' in text


def test_env_points_at_selected_clone(vault: VaultFixture) -> None:
    agent_env = vault.env(agent=True)
    assert agent_env['VAULT_PATH'] == str(vault.agent)
    assert agent_env['AGENT_NAME'] == 'claude'
    assert agent_env['GIT_REMOTE'] == 'origin'
    assert agent_env['QMD_COLLECTION'] == 'notes'
    assert agent_env['AGENT_SESSION'] == 'test-session'

    human_env = vault.env(agent=False, AGENT_NAME='other')
    assert human_env['VAULT_PATH'] == str(vault.human)
    assert human_env['AGENT_NAME'] == 'other'
