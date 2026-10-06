"""Pytest configuration: re-export the offline vault fixture.

Import-safe by design: this file is loaded for every pytest run (including
other agents' in-flight runs), so it imports stdlib, pytest, and the local
``tests.fixtures`` package only — never ``notes_mcp``.
"""

from __future__ import annotations

from tests.fixtures.vault_fixture import VaultFixture, vault


__all__ = ['VaultFixture', 'vault']
