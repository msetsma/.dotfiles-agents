"""Tests for bin/pygate/dead_code.py (GATE003). The vulture-backed tests need
vulture or uvx on PATH."""

import ast
import shutil
import textwrap
from pathlib import Path

import pytest
from pygate.dead_code import Scope, check_dead_code, is_convention_file, is_test_file
from pygate.findings import Finding


HAS_VULTURE = shutil.which('vulture') is not None or shutil.which('uvx') is not None
needs_vulture = pytest.mark.skipif(not HAS_VULTURE, reason='needs vulture or uvx on PATH')

PYPROJECT = """
    [project]
    name = "pkg"

    [project.scripts]
    pkg = "pkg.cli:main"

    [project.entry-points.pytest11]
    plug = "pkg.plugin:register"
"""


def write(root: Path, name: str, text: str) -> Path:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text).lstrip(), encoding='utf-8')
    return path


@pytest.fixture
def project(tmp_path: Path) -> Path:
    write(tmp_path, 'pyproject.toml', PYPROJECT)
    return tmp_path


def dead(path: Path, package: Path, scan_root: Path | None = None) -> list[Finding]:
    scope = Scope(path, package, scan_root or package)
    return check_dead_code(scope, ast.parse(path.read_text(encoding='utf-8')))


def names(findings: list[Finding]) -> list[str]:
    return [f.message.split("'")[1] for f in findings]


@pytest.mark.parametrize(
    ('path', 'expected'),
    [
        ('tests/a.py', True),
        ('pkg/test/helpers.py', True),
        ('test_a.py', True),
        ('a_test.py', True),
        ('conftest.py', True),
        ('src/pkg/a.py', False),
        ('src/pkg/testing_utils.py', False),
    ],
)
def test_is_test_file(path: str, expected: bool) -> None:
    assert is_test_file(Path(path)) is expected


@pytest.mark.parametrize(
    ('path', 'expected'),
    [
        ('docs/conf.py', True),
        ('app/settings_prod.py', True),
        ('app/migrations/0001_initial.py', True),
        ('alembic/versions/abc.py', True),
        ('setup.py', True),
        ('pkg/config.py', False),
    ],
)
def test_is_convention_file(path: str, expected: bool) -> None:
    assert is_convention_file(Path(path)) is expected


@needs_vulture
def test_function_used_only_by_tests_is_dead(project: Path) -> None:
    core = write(project, 'pkg/core.py', 'def helper() -> int:\n    return 1\n')
    write(project, 'tests/test_core.py', 'from pkg.core import helper\n\n\ndef test_it() -> None:\n    helper()\n')
    findings = dead(core, project)
    assert names(findings) == ['helper']
    assert 'vulture_whitelist.py' in findings[0].message


@needs_vulture
def test_function_used_by_production_is_live(project: Path) -> None:
    core = write(project, 'pkg/core.py', 'def helper() -> int:\n    return 1\n')
    write(project, 'pkg/app.py', 'from pkg.core import helper\n\nprint(helper())\n')
    assert dead(core, project) == []


@needs_vulture
def test_caller_in_another_package_counts(tmp_path: Path) -> None:
    lib = tmp_path / 'libs' / 'core'
    write(lib, 'pyproject.toml', '[project]\nname = "core"\n')
    money = write(lib, 'core/money.py', 'def to_cents(x: float) -> int:\n    return round(x * 100)\n')
    write(tmp_path, 'apps/web/web/view.py', 'from core.money import to_cents\n\nprint(to_cents(1.0))\n')
    assert dead(money, lib) != []
    assert dead(money, lib, scan_root=tmp_path) == []


@needs_vulture
def test_parent_directory_named_like_tests_does_not_hide_the_project(tmp_path: Path) -> None:
    root = tmp_path / 'test_area' / 'vendor'
    core = write(root, 'pkg/core.py', 'def helper() -> int:\n    return 1\n')
    assert names(dead(core, root)) == ['helper']


@needs_vulture
def test_module_constant_is_dead_but_class_fields_are_not(project: Path) -> None:
    text = """
        from dataclasses import dataclass

        UNUSED = 1


        @dataclass(frozen=True)
        class Record:
            field: int


        print(Record(1))
    """
    core = write(project, 'pkg/core.py', text)
    assert [(f.code, f.line) for f in dead(core, project)] == [('GATE003', 3)]


@needs_vulture
def test_registered_functions_are_live(project: Path) -> None:
    text = """
        from pydantic import BaseModel, field_validator
        from server import app, mcp


        @mcp.tool(name='x')
        def tool() -> int:
            return 1


        @app.middleware('http')
        async def timing() -> None:
            pass


        class User(BaseModel):
            name: str

            @field_validator('name')
            @classmethod
            def strip_name(cls, value: str) -> str:
                return value.strip()


        print(User)
    """
    server = write(project, 'pkg/server.py', text)
    assert dead(server, project) == []


@needs_vulture
def test_plainly_decorated_function_can_still_be_dead(project: Path) -> None:
    text = """
        from functools import cache


        @cache
        def table() -> dict[str, int]:
            return {}
    """
    core = write(project, 'pkg/core.py', text)
    assert names(dead(core, project)) == ['table']


@needs_vulture
def test_overrides_of_external_base_classes_are_live(project: Path) -> None:
    text = """
        import json
        from http.server import BaseHTTPRequestHandler


        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                pass


        class Encoder(json.JSONEncoder):
            def default(self, o: object) -> object:
                return str(o)


        print(Handler, Encoder)
    """
    web = write(project, 'pkg/web.py', text)
    assert dead(web, project) == []


@needs_vulture
def test_entry_points_and_pytest_hooks_are_live(project: Path) -> None:
    write(project, 'pkg/cli.py', 'def main() -> None:\n    pass\n')
    write(
        project,
        'pkg/plugin.py',
        'def register() -> None:\n    pass\n\n\ndef pytest_addoption(parser: object) -> None:\n    pass\n',
    )
    assert dead(project / 'pkg/cli.py', project) == []
    assert dead(project / 'pkg/plugin.py', project) == []


@needs_vulture
def test_convention_and_test_files_are_not_checked(project: Path) -> None:
    conf = write(project, 'docs/conf.py', 'project = "x"\nextensions = []\n')
    test = write(project, 'tests/test_x.py', 'def unused_helper() -> int:\n    return 1\n')
    assert dead(conf, project) == []
    assert dead(test, project) == []


@needs_vulture
def test_whitelist_module_keeps_a_name_alive(project: Path) -> None:
    core = write(
        project, 'pkg/core.py', 'class Client:\n    def fetch(self) -> int:\n        return 1\n\n\nprint(Client)\n'
    )
    assert names(dead(core, project)) == ['fetch']
    write(project, 'vulture_whitelist.py', 'from pkg.core import Client\n\nClient.fetch\n')
    assert dead(core, project) == []
