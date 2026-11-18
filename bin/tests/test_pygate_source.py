"""Tests for bin/pygate/source_checks.py: checks that need only the file itself."""

import ast
import textwrap
from pathlib import Path

import pytest
from pygate.findings import Finding
from pygate.source_checks import MAX_LINES, check_dataclasses, check_length, check_suppressions, check_swallowed_errors


PATH = Path('a.py')


def codes(findings: list[Finding]) -> list[str]:
    return [f.code for f in findings]


def tree_of(text: str) -> ast.Module:
    return ast.parse(textwrap.dedent(text))


# --------------------------------------------------------------------------- #
# GATE001 file length
# --------------------------------------------------------------------------- #
def test_length_at_limit_passes() -> None:
    assert check_length(PATH, 'x = 1\n' * MAX_LINES) == []


def test_length_over_limit_fails() -> None:
    findings = check_length(PATH, 'x = 1\n' * (MAX_LINES + 1))
    assert codes(findings) == ['GATE001']
    assert str(MAX_LINES + 1) in findings[0].message


# --------------------------------------------------------------------------- #
# GATE002 mutable dataclasses
# --------------------------------------------------------------------------- #
def dataclass_findings(text: str) -> list[Finding]:
    return check_dataclasses(PATH, ast.parse(text), text.splitlines())


@pytest.mark.parametrize(
    'decorator', ['@dataclass', '@dataclass()', '@dataclasses.dataclass', '@dataclass(frozen=False, slots=True)']
)
def test_mutable_dataclass_fails(decorator: str) -> None:
    findings = dataclass_findings(f'{decorator}\nclass A:\n    x: int\n')
    assert codes(findings) == ['GATE002']
    assert findings[0].line == 1


@pytest.mark.parametrize('decorator', ['@dataclass(frozen=True)', '@dataclasses.dataclass(slots=True, frozen=True)'])
def test_frozen_dataclass_passes(decorator: str) -> None:
    assert dataclass_findings(f'{decorator}\nclass A:\n    x: int\n') == []


@pytest.mark.parametrize(
    'text',
    [
        '@dataclass  # gate: mutable: filled in place by the parser\nclass A:\n    x: int\n',
        '@dataclass\nclass A:  # gate: mutable: builder\n    x: int\n',
    ],
)
def test_marked_mutable_dataclass_passes(text: str) -> None:
    assert dataclass_findings(text) == []


def test_other_decorators_are_ignored() -> None:
    assert dataclass_findings('@final\nclass A:\n    pass\n') == []


# --------------------------------------------------------------------------- #
# GATE005 type-check suppressions
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    'comment', ['# type: ignore', '# type: ignore[assignment]', '# ty: ignore[invalid-return-type]', '#pyright: ignore']
)
def test_type_suppression_fails(comment: str) -> None:
    findings = check_suppressions(PATH, f'x: int = "a"  {comment}\n')
    assert codes(findings) == ['GATE005']


def test_suppression_text_in_a_string_passes() -> None:
    assert check_suppressions(PATH, 'x = "# type: ignore"\n') == []


# --------------------------------------------------------------------------- #
# GATE006 swallowed errors
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    'clause', ['except Exception:', 'except BaseException as exc:', 'except (ValueError, Exception):', 'except:']
)
def test_broad_except_without_raise_fails(clause: str) -> None:
    text = f"""
        try:
            work()
        {clause}
            log.exception('failed')
    """
    assert codes(check_swallowed_errors(PATH, tree_of(text))) == ['GATE006']


def test_broad_except_that_reraises_passes() -> None:
    text = """
        try:
            work()
        except Exception as exc:
            log.exception('failed')
            raise AppError('work failed') from exc
    """
    assert check_swallowed_errors(PATH, tree_of(text)) == []


def test_specific_except_passes() -> None:
    text = """
        try:
            work()
        except ValueError:
            pass
    """
    assert check_swallowed_errors(PATH, tree_of(text)) == []


@pytest.mark.parametrize('call', ['suppress(Exception)', 'contextlib.suppress(OSError, BaseException)'])
def test_broad_suppress_fails(call: str) -> None:
    text = f"""
        with {call}:
            work()
    """
    assert codes(check_swallowed_errors(PATH, tree_of(text))) == ['GATE006']


def test_specific_suppress_passes() -> None:
    assert check_swallowed_errors(PATH, tree_of('with suppress(FileNotFoundError):\n    work()\n')) == []
