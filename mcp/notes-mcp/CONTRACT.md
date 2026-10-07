# Frozen interface contract v2 (hardening pass)

Build against this file. It supersedes the build-time contract (v1); the review
fix list wins on behavior, this file wins on signatures. Do not change a
signature here without re-briefing every agent.

Repo `~/.dotfiles-agents`, server `mcp/notes-mcp`, package `src/notes_mcp`.
Vault `/Users/msetsma/notes` (git `main`, remote `origin`).
Ruff config is global (`~/.config/ruff/ruff.toml`): single quotes, line 120,
`max-complexity=10`, `max-branches=15`, `max-statements=60`, `max-locals=20`,
camelCase args banned (N803), builtins `type`/`id`/`filter` banned (A002).
`stdout` is the MCP channel; diagnostics go to `stderr`.

## 0. Envelope (unchanged)

Success `{"ok": True, ...payload..., "commit": sha|None, "pending_push": bool,
"needs_index_update": bool}`. Failure `{"ok": False, "error": {code, message,
hint, **context}}`. Never raise a `NotesError` out of a tool.

## 1. errors.py (wave 0)

Codes: `CONFLICT, CATEGORY_NOT_FOUND, DUPLICATE_FILENAME, FIND_NOT_UNIQUE,
PUSH_FAILED_LOCAL_COMMITTED, PATH_REJECTED, INVALID_FRONTMATTER,
INDEX_UNAVAILABLE, CONFIG_ERROR, NOT_FOUND, PENDING_SYNC`. `PENDING_SYNC`
annotates a **success** payload when a transient (offline) sync was skipped:
`payload["sync_code"] = "PENDING_SYNC"`, `payload["pending_sync"] = True`.

## 2. config.py (wave 0) — new fields

```
qmd_daemon_url: str | None = "http://localhost:8181/mcp"  # None disables the daemon
qmd_timeout: float = 60.0
qmd_embed_on_write: bool = True
human_name: str | None = None      # env HUMAN_NAME; None -> ambient git identity
human_email: str | None = None     # env HUMAN_EMAIL
```
Env: `QMD_DAEMON_URL` (unset -> default; `''`/`0`/`false`/`no`/`none`/`off` -> None),
`QMD_TIMEOUT`, `QMD_EMBED_ON_WRITE`, `HUMAN_NAME`, `HUMAN_EMAIL`.

## 3. atomic.py (wave 0)

`def atomic_write(path: Path, text: str) -> None` — mkdir parents, temp file in
the target dir, `os.replace`. All file writes go through this (git conflict note,
notes, the Index).

## 4. paths.py (B) — PathGuard

```
resolve(self, rel, *, for_write=False, allow_index=False) -> Path
```
- Realpath under vault; reject outside vault, `.git/ .obsidian/ .githooks/`,
  `AGENTS.md`, non-`.md` on write, and `00 Meta/` writes except `03 Conflicts/`.
- **Case-insensitive** forbidden checks: compare `part.casefold()` (APFS-safe).
- `allow_index=True` additionally permits exactly `00 Meta/00.00 Index.md`
  (used only by `notes_move_category`).
- `sanitize_filename` **raises** `PATH_REJECTED` when the cleaned name is empty,
  `.`/`..`, or starts with `.`.

## 5. index.py (B) — VaultIndex

```
categories(refresh=False) -> list[Category]
get(category_id) -> Category                      # CATEGORY_NOT_FOUND
by_path_prefix(rel) -> Category | None            # LONGEST match wins
validate(category_id) -> Category                 # in table AND dir under vault
rewrite_path(category_id, new_path) -> str        # new Index text, Path cell replaced
apply_path(category_id, new_path) -> None         # atomic write + cache refresh
```
- Index path must resolve inside the vault; IDs must be non-empty and unique
  (duplicates -> `INDEX_UNAVAILABLE`).
- `_cells` must not split on escaped pipes (`\|`); unescape them.

## 6. lock.py (A) — FileLock

`acquire` breaks a stale lock only when it is older than `stale` **and** (its
recorded PID is dead, or the PID is absent). Break atomically: `os.rename` the
sentinel to a unique temp (the winner of the rename deletes it), so two
contenders cannot both "break" it. Timeout -> `CONFLICT`.

## 7. git.py (A)

```
commit(self, *, tool, summary, session, paths: list[str]) -> str
    # git -c user.name=agent:<name> -c user.email=agent+<name>@noreply.local
    # commit -m "agent(<tool>): <summary>" -m "Agent: <name>" -m "Session: <sess>" -- <paths>
    # The pathspec keeps foreign (e.g. Obsidian Git) staged files out of the commit.
dirty_paths(self, paths: list[str]) -> list[str]     # git status --porcelain -- <paths>
snapshot_dirty(self, paths: list[str], *, tool, summary) -> str | None
    # if dirty: git add -- <dirty>; commit under the HUMAN identity (config.human_name/
    # human_email via -c when set, else ambient), message "human: preserve edits before <tool>";
    # returns sha or None. Never commits paths outside `paths`.
pull_rebase_autostash(self) -> PullResult            # NEVER raises
    # PullResult(ok: bool, transient: bool, conflict: bool, detail: str,
    #            local_sha: str, remote_sha: str)
    # conflict: a rebase/merge is actually in progress (.git/rebase-merge|rebase-apply)
    #   or stderr shows unmerged paths -> caller runs the conflict path (then abort_rebase()).
    # transient: fetch/network failure with no rebase in progress -> caller proceeds and
    #   records PENDING_SYNC.
push(self, *, retries: int = 3) -> bool
    # git push <remote> <branch> (current branch, NOT HEAD); on real rejection only
    # (markers: "non-fast-forward", "fetch first", "[rejected]", "stale info",
    # "! [remote rejected]") pull+retry with exponential backoff; else return False.
write_conflict_note(...) -> str                      # writes atomically via atomic.py
```
Keep `run`, `add`, `wait_for_index_lock`, `abort_rebase`, `status_summary`,
`log_name_only`, `current_branch`.

## 8. search.py (C) — Searcher

```
search(self, query, *, collection=None, limit=10, rerank=True) -> list[SearchHit]
    # NO filter arg (removed). When the daemon is configured+up: POST JSON-RPC
    # tools/call name="query" to config.qmd_daemon_url with
    # arguments={"searches":[{"type":"lex","query":q}] (+ {"type":"vec","query":q} when
    # rerank), "intent": q, "collections":[coll], "limit": limit}; parse the SSE
    # `data:` frame, read result.structuredContent.results, strip "<coll>/" from `file`.
    # On daemon error/timeout/refusal -> fall back to the CLI. Daemon disabled/None ->
    # CLI only. rerank=False -> lex-only (BM25); rerank=True -> hybrid.
    # rerank=False CLI fallback: qmd search; rerank=True CLI fallback: qmd query.
    # qmd binary missing AND daemon down -> INDEX_UNAVAILABLE.
reindex(self, *, embed: bool | None = None) -> None  # update (+ embed when config.qmd_embed_on_write)
schedule_reindex(self, *, delay=5.0) -> None         # debounced background; never raises
health(self) -> dict                                 # + "daemon": {"up": bool, "url": str|None}
```
Timeout from `config.qmd_timeout`. Unit tests must not require the live daemon:
make the HTTP call patchable and default tests to `qmd_daemon_url=None` or a fake.

## 9. withwrite.py (D)

```
with_write(*, config, git, lock=None, paths, tool, summary, fn) -> dict
```
Order: lock -> `wait_for_index_lock` -> `pull_rebase_autostash`:
- `conflict` -> abort, write conflict note, commit+push it **only if the tree is
  clean** with `commit(paths=[note_rel])`; return `CONFLICT` (no second pull).
- `transient` -> proceed, set `pending_sync=True`, `sync_code=PENDING_SYNC`.
- otherwise -> proceed.
Then `git.snapshot_dirty(paths, tool=tool, summary=summary)`, `fn()`,
`git.add(paths)`, `git.commit(tool=..., summary=..., session=..., paths=paths)`,
`git.push()`. Push failure -> success payload with `pending_push` +
`push_code=PUSH_FAILED_LOCAL_COMMITTED`. Always release the lock.

## 10. tools (D/E/F)

teeth: tool params stay snake_case in Python; camelCase aliases still accepted.
- read.py (E): `notes_search(ctx, query, area=None, category_id=None, type_=None,
  status=None, tags=None, limit=10, rerank=True)`. With any filter set, request
  `pool = min(max(limit * 5, limit), 50)` hits, then post-filter and truncate to
  `limit`. `tags` matches if any requested tag is in the note's `tags`.
- write.py (D): journal dirs + Daily template derive from the Index categories
  (`00`->Meta, `41`->Daily, `42`->Weekly, `43`->Meetings) with the old constants
  as fallback. Creating a `daily`/`weekly` that already exists **updates** it
  (append body) instead of `DUPLICATE_FILENAME`. The vault-wide filename-uniqueness
  check runs inside the lock and prunes `.git/ .obsidian/ .githooks/`.
- structure.py (F): `notes_move`/`notes_rename` also rewrite **path-qualified**
  links (`[[<old rel w/o .md>` ) in addition to the existing title links.
  New `notes_move_category(ctx, category_id, new_path)`:
  validate the category; reject involvement of `00 Meta`; `git mv` the folder
  (old->new); rewrite links whose target was under the old path; `index.apply_path`
  to update the Path row; one commit with `paths=[*old_files, *new_files,
  INDEX_REL_PATH]` (list both sides so the rename's deletion is included).
- server.py (F): register the 13th tool `notes_move_category` (write annotations).

## 11. tests

Preserve the offline fixture API. Each agent owns its module's test file(s) and
must keep them hermetic (no live qmd/daemon unless marked `integration`). Do not
edit `tests/fixtures/vault_fixture.py` or `tests/conftest.py` (wave 0 owns them);
use `vault.config(**overrides)` (env keys pass through verbatim, e.g.
`vault.config(**{"HUMAN_NAME": "Human"})`).

## 12. File ownership

Wave 0: `errors.py`, `config.py`, `atomic.py`, `CONTRACT.md`, plan.
Wave 1: A=`git.py`,`lock.py`,`test_git.py`,`test_lock.py`,`test_atomic.py`;
        B=`index.py`,`paths.py`,`test_index.py`,`test_paths.py`;
        C=`search.py`,`test_search.py`.
Wave 2: D=`withwrite.py`,`tools/write.py`,`test_withwrite.py`,`test_tools_write.py`;
        E=`tools/read.py`,`test_tools_read.py`;
        F=`tools/structure.py`,`server.py`,`test_tools_structure.py`,`test_server.py`.
Wave 3: `context.py` (read-only), `test_tools_integration.py`, `README.md`, catalog, `pyproject.toml`.

## 13. v3 additions (follow-up pass)

- **index.py**: `rewrite_row(category_id, *, name=None, path=None, scope=None) -> str`
  and `apply_row(...)`; columns name=1/path=2/scope=3, only non-`None` cells
  change, missing id -> `CATEGORY_NOT_FOUND`. `rewrite_path`/`apply_path` remain
  thin wrappers.
- **structure.py**: `notes_move_category(ctx, category_id, new_path, new_name=None,
  new_scope=None, **options)` — derives the category name from the new basename,
  renames the hub to match (`git mv` + `# H1` + `scope` + qmd mirror), rewrites
  title + path links, updates the Index Category/Path/Scope cells, one commit.
- **frontmatter.py**: `normalize_scalars(value)` coerces every `datetime.date`/
  `datetime.datetime` (recursively) to an ISO string; `parse` applies it so
  unquoted ISO dates in hand-edited notes are accepted and JSON-safe.
- **layout.py** (new): `journal_dir(ctx, kind)`, `templates_dir(ctx)`,
  `attachments_dir(ctx)`, `template_rel(ctx, name)`, `resolve(ctx)` — precedence
  is config env override -> Index category -> built-in fallback.
- **config.py**: `daily_dir`, `weekly_dir`, `meetings_dir`, `templates_dir`,
  `attachments_dir` (env `NOTES_*_DIR`, empty -> None).
- **read.py**: `notes_status` payload gains `layout` (`layout.resolve(ctx)`).

## 14. v4 additions (P2)

Framework: 16 tools (`notes_capture`, `notes_triage`, `notes_lint` added).

- **read.py**: `notes_search(ctx, query, *, area=None, category_id=None, type_=None,
  status=None, tags=None, domain=None, created_after=None, created_before=None,
  updated_after=None, updated_before=None, limit=10, rerank=True)`. `domain` is a
  case-insensitive substring match on the hit path (mirrors the Obsidian Bases
  `file.folder.contains(...)`); date bounds are ISO `YYYY-MM-DD`, inclusive
  (`_after` `>=`, `_before` `<=`), compared on the frontmatter date part; a hit
  with a missing/unparseable date is dropped when its bound is set.
- **frontmatter.py**: `sync_qmd_metadata` adds `created_ts`/`updated_ts` (epoch
  seconds, UTC) after `tags`.
- **write.py**: `watch` added to `_ALLOWED_TYPES`; `_TYPE_TEMPLATES` seeds the
  body from `00 Meta/01 Templates/<name>.md` for templated types, then appends
  the caller body; a missing non-Daily template falls back to the caller body.
- **tools/capture.py**: `notes_capture(ctx, text, title=None, tags=None,
  source=None, **options)` writes one `05 Inbox` note from the Inbox template
  (falls back to today's daily under `## Inbox` if category `05` is absent);
  `notes_triage(ctx, path=None, limit=None, **options)` is read-only and ranks
  category suggestions per inbox note.
- **tools/lint.py**: `notes_lint(ctx)` is read-only and reports `broken_links`,
  `invalid_frontmatter`, `filename_collisions`, `missing_hubs`, `index_drift`,
  `secrets` (each capped at 100; `AGENTS.md` and the raw Index are skipped).

## 15. v5 additions (P3, local only)

Framework: 20 tools. No external search backend (explicitly declined).

- **tools/similar.py** (read-only): `notes_similar(ctx, path=None, text=None,
  limit=5)` — neighbours of a note or raw text; `notes_suggest_links(ctx, path,
  limit=10)` — unlinked candidates. Both drop the source note and `00 Meta`
  hits; `NotesError` -> envelope.
- **criticmarkup.py**: `substitution(old, new)`, `addition(text)`,
  `deletion(text)`, `comment(text)`, `has_markup(text)`.
- **config.py**: `write_mode: str = 'direct'` (env `NOTES_WRITE_MODE`;
  `suggest`/anything-else -> direct).
- **tools/write.py**: `notes_update(..., mode=None)` / `notes_append(...,
  mode=None)`; in `suggest` mode each edit becomes `{~~find~>replace~~}` and an
  appended `text` becomes `{++text++}` (body/frontmatter stay direct).
  `notes_create` attaches a best-effort `similar` list (<=3, non-fatal).
- **tools/digest.py**: `notes_weekly_digest(ctx, week=None, write=True)` —
  draft/refresh the weekly note (`layout.journal_dir(ctx, 'weekly')/<week>.md`)
  with a per-category `## Digest`; `notes_stale_report(ctx, days=90,
  write=False)` — list notes not updated recently, or (write=True) append the
  report to today's daily under `## Reports`.
- **server.py**: `_inspect_tools` builder added (keeps each builder C901-clean);
  `build_tools` merges read + inspect + write + structure.



