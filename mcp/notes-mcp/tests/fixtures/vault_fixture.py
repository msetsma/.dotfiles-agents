"""Offline vault fixture: a temp vault plus a bare remote and two clones.

Layout produced by :meth:`VaultFixture.build`::

    <root>/
        remote.git/   bare repository
        human/        human-owned clone (origin -> remote.git)
        agent/        agent-owned clone (origin -> remote.git)

Import-safety: this module imports stdlib + pytest only. ``notes_mcp`` is
imported lazily inside :meth:`VaultFixture.config`, never at module level, so
pytest can load the fixtures while the server package is still being written.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest


# Absolute interpreter path keeps ruff's flake8-bandit S607 quiet and avoids a
# shell; git is always at /usr/bin/git on this platform.
GIT = '/usr/bin/git'

# ---------------------------------------------------------------------------
# Real vault content, copied from /Users/msetsma/notes so fixtures match
# production shapes.
# ---------------------------------------------------------------------------

INDEX_MD = """# Index

| ID | Category | Path | Scope |
|----|----------|------|-------|
| 00 | Meta | 00 Meta | System: index, templates, attachments, conflicts, views. |
| 11 | Project A | 10 Projects/11 Project A | Placeholder project — rename or delete. |
| 12 | Project B | 10 Projects/12 Project B | Placeholder project — rename or delete. |
| 21 | Platform Engineering | 20 Areas/21 Platform Engineering | Ongoing platform ownership. |
| 22 | Team & People Mgmt | 20 Areas/22 Team & People Mgmt | Ongoing team and people responsibilities. |
| 31 | How-tos | 30 Resources/31 How-tos | Step-by-step procedures. |
| 32 | Tools | 30 Resources/32 Tools | Tool reference and setup notes. |
| 33 | Concepts | 30 Resources/33 Concepts | Concepts, definitions, mental models. |
| 34 | People | 30 Resources/34 People | People reference notes. |
| 41 | Daily | 40 Journal/41 Daily | One note per day, named YYYY-MM-DD. |
| 42 | Weekly | 40 Journal/42 Weekly | Weekly reviews, named YYYY-Www. |
| 43 | Meetings | 40 Journal/43 Meetings | Meeting notes, named YYYY-MM-DD <Title>. |
| 90 | Archive | 90 Archive | Finished or retired material. |
"""

TEMPLATE_DAILY = """---
type: daily
status: active
created: "{{date:YYYY-MM-DD}}"
updated: "{{date:YYYY-MM-DD}}"
tags: []
related: []
source: []
qmd:
  metadata:
    type: daily
    status: active
    tags: []
---

# {{date:YYYY-MM-DD}}

## Focus

## Notes

## Agent notes

## Log
"""

TEMPLATE_PROJECT_HUB = """---
type: project
status: active
created: "{{date:YYYY-MM-DD}}"
updated: "{{date:YYYY-MM-DD}}"
tags: []
related: []
source: []
scope:
qmd:
  metadata:
    type: project
    status: active
    tags: []
---

# {{title}}

## Outcome

## Scope

## Status

## Notes

## Links
"""

TEMPLATE_DECISION = """---
type: decision
status: active
created: "{{date:YYYY-MM-DD}}"
updated: "{{date:YYYY-MM-DD}}"
tags: []
related: []
source: []
qmd:
  metadata:
    type: decision
    status: active
    tags: []
---

# {{title}}

## Decision

## Context

## Consequences
"""

# A fixed "today" keeps fixtures deterministic.
TODAY = '2026-01-05'


def _yaml_list(items: list[str]) -> str:
    return '[' + ', '.join(items) + ']'


def _body(*blocks: str) -> str:
    """Join body blocks with blank lines, trailing newline."""
    return '\n\n'.join(blocks) + '\n'


def frontmatter(
    *,
    type_: str,
    status: str = 'active',
    created: str = TODAY,
    updated: str | None = None,
    tags: list[str] | None = None,
    related: list[str] | None = None,
    source: list[str] | None = None,
    scope: str | None = None,
) -> str:
    """Render canonical frontmatter with the qmd metadata mirror.

    Mirrors ``notes_mcp.frontmatter.new_frontmatter`` + ``sync_qmd_metadata`` so
    fixtures validate against the real parser without importing it.
    """
    tags = list(tags or [])
    related = list(related or [])
    source = list(source or [])
    updated = updated or created
    lines = [
        '---',
        f'type: {type_}',
        f'status: {status}',
        f'created: "{created}"',
        f'updated: "{updated}"',
        f'tags: {_yaml_list(tags)}',
        f'related: {_yaml_list(related)}',
        f'source: {_yaml_list(source)}',
    ]
    if scope is not None:
        lines.append(f'scope: {scope}')
    lines += [
        'qmd:',
        '  metadata:',
        f'    type: {type_}',
        f'    status: {status}',
        f'    tags: {_yaml_list(tags)}',
        '---',
    ]
    return '\n'.join(lines) + '\n'


def note(*, body: str, **frontmatter_kwargs: Any) -> str:
    """A full note: frontmatter block + blank line + body."""
    return frontmatter(**frontmatter_kwargs) + '\n' + body.lstrip('\n')


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------


@dataclass
class VaultFixture:
    root: Path
    remote: Path
    human: Path
    agent: Path
    agent_name: str = 'claude'
    _built: bool = field(default=False, init=False, repr=False)

    # -- content -----------------------------------------------------------

    def seed(self) -> None:
        """Write the minimal vault into every clone that exists on disk."""
        roots = [self.human]
        if self.agent.exists():
            roots.append(self.agent)
        for root in roots:
            self._write_tree(root)

    def _write_tree(self, root: Path) -> None:
        files: dict[str, str] = {
            '00 Meta/00.00 Index.md': INDEX_MD,
            '00 Meta/01 Templates/Daily.md': TEMPLATE_DAILY,
            '00 Meta/01 Templates/Project Hub.md': TEMPLATE_PROJECT_HUB,
            '00 Meta/01 Templates/Decision.md': TEMPLATE_DECISION,
            '10 Projects/11 Project A/11 Project A.md': note(
                type_='hub',
                scope='Placeholder project — rename or delete.',
                tags=['project'],
                body=_body(
                    '# Project A',
                    '## Outcome',
                    'Placeholder project used by tests.',
                    '## Notes',
                    'See [[Meeting Notes 2026-01-05]].',
                ),
            ),
            '20 Areas/21 Platform Engineering/21 Platform Engineering.md': note(
                type_='hub',
                scope='Ongoing platform ownership.',
                tags=['area'],
                body=_body(
                    '# Platform Engineering',
                    '## Outcome',
                    'Ongoing platform ownership.',
                    '## Notes',
                    'Platform notes live here.',
                ),
            ),
            '10 Projects/11 Project A/Meeting Notes 2026-01-05.md': note(
                type_='meeting',
                tags=['meeting'],
                body=_body(
                    '# Meeting Notes 2026-01-05',
                    '## Attendees',
                    '- Human\n- Agent',
                    '## Decisions',
                    'Ship the fixture.',
                ),
            ),
            '30 Resources/33 Concepts/Johnny Decimal.md': note(
                type_='concept',
                tags=['concept'],
                body=_body('# Johnny Decimal', 'A numbering system for organising notes into areas and categories.'),
            ),
            '40 Journal/41 Daily/2026-01-05.md': note(
                type_='daily',
                body=_body('# 2026-01-05', '## Focus', 'Build the offline fixture.', '## Log', '- [x] Seed the vault.'),
            ),
        }
        for rel, text in files.items():
            path = root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding='utf-8')

    # -- git ---------------------------------------------------------------

    def _set_origin(self, repo: Path) -> None:
        """Point ``origin`` at the bare remote with an absolute URL.

        Written directly into ``.git/config`` so the URL is absolute without
        passing a dynamic path as a subprocess argument.
        """
        block = f'\n[remote "origin"]\n\turl = {self.remote}\n\tfetch = +refs/heads/*:refs/remotes/origin/*\n'
        with (repo / '.git' / 'config').open('a', encoding='utf-8') as handle:
            handle.write(block)

    def build(self) -> VaultFixture:
        """Create remote, seed the human clone, push, then set up the agent clone."""
        if self._built:
            return self
        self.root.mkdir(parents=True, exist_ok=True)

        # Bare "remote".
        self.remote.mkdir(parents=True, exist_ok=True)
        subprocess.run(['/usr/bin/git', 'init', '--bare', '-b', 'main'], cwd=self.remote, check=True)

        # Human clone: author and push the vault.
        self.human.mkdir(parents=True, exist_ok=True)
        subprocess.run(['/usr/bin/git', 'init', '-b', 'main'], cwd=self.human, check=True)
        subprocess.run(['/usr/bin/git', 'config', 'user.name', 'Human'], cwd=self.human, check=True)
        subprocess.run(['/usr/bin/git', 'config', 'user.email', 'human@example.com'], cwd=self.human, check=True)
        self.seed()
        subprocess.run(['/usr/bin/git', 'add', '-A'], cwd=self.human, check=True)
        subprocess.run(['/usr/bin/git', 'commit', '-m', 'Seed vault'], cwd=self.human, check=True)
        self._set_origin(self.human)
        subprocess.run(['/usr/bin/git', 'push', '-u', 'origin', 'main'], cwd=self.human, check=True)

        # Agent clone: fetch the pushed vault and track origin/main.
        self.agent.mkdir(parents=True, exist_ok=True)
        subprocess.run(['/usr/bin/git', 'init', '-b', 'main'], cwd=self.agent, check=True)
        subprocess.run(['/usr/bin/git', 'config', 'user.name', 'Agent'], cwd=self.agent, check=True)
        subprocess.run(['/usr/bin/git', 'config', 'user.email', 'agent@example.com'], cwd=self.agent, check=True)
        self._set_origin(self.agent)
        subprocess.run(['/usr/bin/git', 'fetch', 'origin'], cwd=self.agent, check=True)
        subprocess.run(['/usr/bin/git', 'reset', '--hard', 'origin/main'], cwd=self.agent, check=True)
        subprocess.run(['/usr/bin/git', 'branch', '--set-upstream-to=origin/main', 'main'], cwd=self.agent, check=True)

        self._built = True
        return self

    # -- config helpers ----------------------------------------------------

    def env(self, *, agent: bool = True, **overrides: Any) -> dict[str, str]:
        """Environment for ``Config.from_env`` pointing at a clone."""
        vault = self.agent if agent else self.human
        base: dict[str, str] = {
            'VAULT_PATH': str(vault),
            'AGENT_NAME': self.agent_name,
            'GIT_REMOTE': 'origin',
            'QMD_COLLECTION': 'notes',
            'AGENT_SESSION': 'test-session',
        }
        base.update({key: str(value) for key, value in overrides.items()})
        return base

    def config(self, *, agent: bool = True, **overrides: Any):
        """Build a real ``Config`` from this fixture (late import)."""
        from notes_mcp.config import Config

        return Config.from_env(self.env(agent=agent, **overrides))


@pytest.fixture
def vault(tmp_path: Path) -> VaultFixture:
    """A built temp vault with bare remote + human + agent clones."""
    return VaultFixture(
        root=tmp_path, remote=tmp_path / 'remote.git', human=tmp_path / 'human', agent=tmp_path / 'agent'
    ).build()
