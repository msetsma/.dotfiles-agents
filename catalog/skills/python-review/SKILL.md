---
name: python-review
description: "Checklist for writing, refactoring or reviewing Python: the judgement calls the clean-python gate cannot enforce (immutability over mutation, composition over inheritance, records instead of threaded values, behaviour-preserving refactors checked against saved output, dead-code checks before deleting or splitting). Use before a Python refactor, when splitting or deleting code, and when reviewing a Python diff."
---

# python-review

Every Python edit already passes `bin/clean-python` (ruff with the global
config, then `bin/python_gate.py`), so do not re-check what it enforces: typed
signatures, no `print`, no blind `except`, `raise ... from`, `X | None`,
function size and argument counts, frozen dataclasses, files of 800 lines or
fewer, dead code that only tests use, and ty type errors. A git pre-commit hook
runs the same gate on staged files.

This checklist covers what no tool can decide.

## Design

- [ ] **Immutability.** New values are built, not mutated in place: return a
      new record (`dataclasses.replace`, a new tuple or dict) instead of
      changing an argument or shared state. A `# gate: mutable: <why>` marker
      needs a reason that holds up.
- [ ] **Composition over inheritance.** Behaviour comes from passed-in
      collaborators or `Protocol`s, not subclass hooks. Inheritance only for a
      true is-a with no overridden behaviour.
- [ ] **Records, not threaded values.** When the same two or three values travel
      together through several functions, make them one frozen dataclass. The
      argument-count rules only catch the extreme case.
- [ ] **Boundaries.** External data (files, APIs, config) is validated once at
      the edge (pydantic) and plain records flow inward.

## Refactors

- [ ] Before a behaviour-preserving refactor, save the program's real output
      (a dry run, a CLI report, a rendered file) to a scratch file. After it,
      run the same command and diff. The diff must be empty, or every line of
      it explained.
- [ ] Tests pass before and after, and no test was changed to fit the new code
      unless the behaviour change was intended.

## Deleting or splitting code

- [ ] Before deleting, check every caller (`rg -n '\bname\b'`), including
      strings, entry points in `pyproject.toml`, decorators that register the
      function, and other repos that import it.
- [ ] Code that only tests use is dead: delete it with its tests, do not keep
      it alive for them. The gate's vulture check cannot see this for names
      listed in `__all__` or for methods whose name is used elsewhere (`run`,
      `get`), so check those by hand.
- [ ] Split a module by responsibility, not by line count, and keep the import
      graph one-directional.

## Reviewing a diff

Report only findings from the lists above, each with file, line and the
concrete fix. Do not restate lint findings: the gate already blocks them.
