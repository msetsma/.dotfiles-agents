"""Tests for notes_lint: broken links, bad frontmatter, hubs, drift, secrets."""

from __future__ import annotations

from types import SimpleNamespace

from notes_mcp.git import Git
from notes_mcp.index import VaultIndex
from notes_mcp.paths import PathGuard
from notes_mcp.tools import lint


class FakeSearch:
    def schedule_reindex(self, **_kwargs: object) -> None:
        return None


def make_ctx(vault) -> SimpleNamespace:
    config = vault.config()
    return SimpleNamespace(
        config=config,
        guard=PathGuard(config.vault_path),
        index=VaultIndex(config),
        git=Git(config),
        search=FakeSearch(),
    )


def _write_note(root, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8')


def _valid_note(body: str) -> str:
    return (
        '---\n'
        'type: note\n'
        'status: active\n'
        'created: "2026-01-05"\n'
        'updated: "2026-01-05"\n'
        'tags: []\n'
        'related: []\n'
        'source: []\n'
        'qmd:\n'
        '  metadata:\n'
        '    type: note\n'
        '    status: active\n'
        '    tags: []\n'
        '---\n'
        f'{body}'
    )


def test_lint_reports_missing_hub_and_index_drift(vault) -> None:
    result = lint.notes_lint(make_ctx(vault))

    assert result['ok'] is True
    assert result['commit'] is None
    assert {'category_id': '90', 'path': '90 Archive', 'hub': '90 Archive/90 Archive.md'} in result['findings'][
        'missing_hubs'
    ]
    assert {'category_id': '90', 'path': '90 Archive'} in result['findings']['index_drift']
    assert result['summary']['missing_hubs'] == len(result['findings']['missing_hubs'])


def test_lint_finds_one_broken_link(vault) -> None:
    _write_note(vault.agent, '30 Resources/33 Concepts/Linker.md', _valid_note('# Linker\n\nSee [[Nowhere]].\n'))

    result = lint.notes_lint(make_ctx(vault))

    broken = result['findings']['broken_links']
    assert len(broken) == 1
    assert broken[0]['path'] == '30 Resources/33 Concepts/Linker.md'
    assert broken[0]['target'] == 'Nowhere'


def test_lint_ignores_links_in_code_fences(vault) -> None:
    _write_note(
        vault.agent, '30 Resources/33 Concepts/Fenced.md', _valid_note('# Fenced\n\n```\nSee [[Nowhere]].\n```\n')
    )

    result = lint.notes_lint(make_ctx(vault))

    assert result['findings']['broken_links'] == []


def test_lint_reports_bad_frontmatter(vault) -> None:
    _write_note(vault.agent, '30 Resources/33 Concepts/Broken.md', '# Broken\n\nno frontmatter here\n')

    result = lint.notes_lint(make_ctx(vault))

    bad = result['findings']['invalid_frontmatter']
    assert [item['path'] for item in bad] == ['30 Resources/33 Concepts/Broken.md']
    assert 'frontmatter' in bad[0]['message']


def test_lint_reports_filename_collisions(vault) -> None:
    _write_note(vault.agent, '30 Resources/33 Concepts/Dupe.md', _valid_note('# Dupe\n'))
    _write_note(vault.agent, '20 Areas/Dupe.md', _valid_note('# Dupe\n'))

    result = lint.notes_lint(make_ctx(vault))

    collisions = result['findings']['filename_collisions']
    assert collisions == [{'name': 'Dupe.md', 'paths': ['20 Areas/Dupe.md', '30 Resources/33 Concepts/Dupe.md']}]


def test_lint_ignores_agents_md(vault) -> None:
    _write_note(vault.agent, 'AGENTS.md', '# Agent rules\n\nLink first mentions with [[wikilinks]].\n')

    result = lint.notes_lint(make_ctx(vault))

    assert all(item['path'] != 'AGENTS.md' for item in result['findings']['invalid_frontmatter'])
    assert all(item['path'] != 'AGENTS.md' for item in result['findings']['broken_links'])


def test_lint_clean_vault_has_no_secrets(vault) -> None:
    _write_note(vault.agent, '30 Resources/33 Concepts/Linker.md', _valid_note('# Linker\n\nSee [[Nowhere]].\n'))

    result = lint.notes_lint(make_ctx(vault))

    assert result['findings']['secrets'] == []


def test_lint_flags_a_secret(vault) -> None:
    _write_note(
        vault.agent, '30 Resources/33 Concepts/Leaky.md', _valid_note('# Leaky\n\napi_key = "abcdef0123456789abcd"\n')
    )

    result = lint.notes_lint(make_ctx(vault))

    secrets = result['findings']['secrets']
    assert len(secrets) == 1
    assert secrets[0]['path'] == '30 Resources/33 Concepts/Leaky.md'
    assert secrets[0]['pattern'] == 'assignment'
