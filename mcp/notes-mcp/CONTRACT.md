"""Frozen interface contract for the `notes` MCP server (Wave 0).

Every Wave 1/2 agent builds against this file. If it conflicts with the Part 2
spec, this contract wins for interfaces; the spec wins for behavior. Do not
change signatures here without re-briefing every agent.

Repo: ~/.dotfiles-agents   Server dir: mcp/notes-mcp   Package: src/notes_mcp
Vault: /Users/msetsma/notes (git `main`, remote `origin` -> git@github.com:msetsma/notes.git)

--------------------------------------------------------------------------------
0. RESULT ENVELOPE (every tool returns a plain dict)
--------------------------------------------------------------------------------
Success:
    {
      "ok": True,
      ...tool-specific payload...,
      "commit": "<sha>" | None,      # set when a git commit was made
      "pending_push": bool,          # True when committed but push failed
      "needs_index_update": bool,    # True when the caller should re-run notes_index
    }
Failure:
    {"ok": False, "error": {"code": "<CODE>", "message": str, "hint": str, **context}}

Never raise out of a tool: catch NotesError and return `e.to_dict()`.
stdout is the MCP protocol channel; diagnostics go to stderr only.

--------------------------------------------------------------------------------
1. ERROR CODES (errors.py, owned by Wave 0)
--------------------------------------------------------------------------------
CONFLICT, CATEGORY_NOT_FOUND, DUPLICATE_FILENAME, FIND_NOT_UNIQUE,
PUSH_FAILED_LOCAL_COMMITTED, PATH_REJECTED, INVALID_FRONTMATTER,
INDEX_UNAVAILABLE, CONFIG_ERROR, NOT_FOUND.
`CONFIG_ERROR` and `NOT_FOUND` are additions the spec omits; they are needed for
a required VAULT_PATH and for read tools targeting a missing file.

class NotesError(Exception):
    code: str; message: str; hint: str; context: dict
    def to_dict(self) -> dict   # {"ok": False, "error": {...}}
Raise helpers: notes_error(code, message, hint, **context) -> NotesError.

--------------------------------------------------------------------------------
2. config.py  (M2)
--------------------------------------------------------------------------------
@dataclass(frozen=True)
class Config:
    vault_path: Path
    agent_name: str = "claude"
    git_remote: str = "origin"
    qmd_collection: str = "notes"
    qmd_embed_model: str | None = None
    qmd_rerank_model: str | None = None
    qmd_generate_model: str | None = None
    session: str = "unknown"

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Config": ...
    # reads VAULT_PATH (required), AGENT_NAME, GIT_REMOTE, QMD_COLLECTION,
    # QMD_EMBED_MODEL, QMD_RERANK_MODEL, QMD_GENERATE_MODEL, AGENT_SESSION.
    # VAULT_PATH missing/unresolvable -> NotesError(CONFIG_ERROR).
    # Implies no network / no llama model loading; QMD_*_MODEL values are passed
    # through to the qmd subprocess env verbatim.

--------------------------------------------------------------------------------
3. paths.py  (M2)  -- the pathGuard
--------------------------------------------------------------------------------
class PathGuard:
    def __init__(self, vault_path: Path) -> None: ...
    def resolve(self, rel: str, *, for_write: bool = False) -> Path: ...
        # realpath() the candidate against vault_path (follow symlinks).
        # Reject -> NotesError(PATH_REJECTED): outside vault; .git/; .obsidian/;
        # .githooks/; AGENTS.md; non-.md when for_write;
        # writes in "00 Meta/" except "00 Meta/03 Conflicts/".
    def relpath(self, path: Path) -> str: ...          # vault-relative posix string
    def sanitize_filename(self, name: str) -> str: ... # strip / \ : * ? " < > |,
        # trim, collapse runs of whitespace to one space.
    def is_meta(self, rel: str) -> bool: ...
    def is_conflicts(self, rel: str) -> bool: ...

--------------------------------------------------------------------------------
4. frontmatter.py  (M2)
--------------------------------------------------------------------------------
REQUIRED_KEYS = ("type", "status", "created", "updated", "tags", "related", "source")
HUB_REQUIRED_KEYS = REQUIRED_KEYS + ("scope",)

def parse(text: str) -> tuple[dict, str]: ...   # (frontmatter, body). No frontmatter
    # -> NotesError(INVALID_FRONTMATTER). Preserve key order and unknown keys.
def serialize(fm: dict, body: str) -> str: ...   # YAML front block + "\n" + body
def validate(fm: dict, *, hub: bool = False) -> None: ...  # -> INVALID_FRONTMATTER
def sync_qmd_metadata(fm: dict) -> dict: ...     # ensure fm["qmd"]["metadata"] mirrors
    # {type, status, tags}; called on EVERY write before serialize. The qmd block
    # is MCP-maintained and is never exposed as user-editable.
def new_frontmatter(*, type: str, status: str, today: str) -> dict: ...  # canonical keys
    # in order, created == updated == today, tags/related/source == [].

Round-trip requirement: parse -> serialize preserves unknown keys and key order.

--------------------------------------------------------------------------------
5. index.py  (M2)  -- vaultIndex
--------------------------------------------------------------------------------
@dataclass(frozen=True)
class Category:
    id: str      # "11"
    name: str    # "Project A"
    path: str    # "10 Projects/11 Project A"
    scope: str

class VaultIndex:
    def __init__(self, config: Config) -> None: ...
    def categories(self, refresh: bool = False) -> list[Category]: ...
        # parse the FIRST markdown table in "00 Meta/00.00 Index.md" with headers
        # ID|Category|Path|Scope. Cache; reload when file mtime changes.
        # Missing/unparseable -> NotesError(INDEX_UNAVAILABLE).
    def get(self, category_id: str) -> Category: ...   # -> CATEGORY_NOT_FOUND
    def by_path_prefix(self, rel: str) -> Category | None: ...
    def validate(self, category_id: str) -> Category: ...
        # valid iff in table AND the folder exists under vault_path.

--------------------------------------------------------------------------------
6. lock.py  (M1)
--------------------------------------------------------------------------------
class FileLock:
    def __init__(self, path: Path, *, timeout: float = 30.0, stale: float = 120.0) -> None: ...
    def acquire(self) -> None: ...   # exclusive; break locks older than `stale`
        # -> NotesError(CONFLICT) on timeout. Poll interval ~0.2s.
    def release(self) -> None: ...
    def __enter__(self) -> "FileLock": ...
    def __exit__(self, *exc) -> None: ...

--------------------------------------------------------------------------------
7. git.py  (M1)
--------------------------------------------------------------------------------
class Git:
    def __init__(self, config: Config, *, cwd: Path | None = None) -> None: ...
    def run(self, args: list[str], *, check: bool = True, env=None) -> subprocess.CompletedProcess:
        # ALWAYS argument arrays, never a shell string. cwd defaults to vault_path.
    def wait_for_index_lock(self, timeout: float = 30.0) -> None: ...
        # while <vault>/.git/index.lock exists, poll; timeout -> NotesError(CONFLICT).
    def pull_rebase_autostash(self) -> None: ...
        # `git pull --rebase --autostash <remote> <branch>`; conflict -> CONFLICT path.
    def add(self, rel_paths: list[str]) -> None: ...   # `git add -- <paths>`, never -A
    def commit(self, *, tool: str, summary: str, session: str) -> str: ...
        # git -c user.name="agent:<AGENT_NAME>" -c user.email="agent+<AGENT_NAME>@noreply.local"
        # commit -m "agent(<tool>): <summary>" -m "Agent: <name>" -m "Session: <session>"
        # returns commit sha.
    def push(self, *, retries: int = 3) -> bool: ...
        # `git push <remote> HEAD`; non-fast-forward -> pull --rebase + retry.
        # returns True if pushed, False if exhausted (caller sets pending_push).
    def abort_rebase(self) -> None: ...
    def write_conflict_note(self, *, rel_path: str, local_sha: str, remote_sha: str,
                            diff_excerpt: str, intended: str) -> str: ...
        # writes "00 Meta/03 Conflicts/YYYY-MM-DD-HHMM <file>.md"; returns rel path.
    def status_summary(self) -> dict: ...   # {branch, ahead, behind, last_pull}
    def log_name_only(self, *, since: str, limit: int) -> list[dict]: ...
        # [{"sha","author","date","paths":[...]}] from `git log --name-only`
    def current_branch(self) -> str: ...

--------------------------------------------------------------------------------
8. search.py  (M3)  -- qmd CLI adapter
--------------------------------------------------------------------------------
@dataclass
class SearchHit:
    path: str; title: str | None; score: float | None; snippet: str | None

class Searcher:
    def __init__(self, config: Config) -> None: ...
    def search(self, query: str, *, collection: str | None = None,
               filter: dict | None = None, limit: int = 10,
               rerank: bool = True) -> list[SearchHit]: ...
        # rerank=True  -> `qmd query --json -n <limit> -c <collection>` (+ --filter)
        # rerank=False -> `qmd search --json -n <limit> -c <collection>`
        # qmd missing / models missing -> NotesError(INDEX_UNAVAILABLE).
        # Normalize qmd://<collection>/<path> -> vault-relative path.
    def reindex(self, *, embed: bool = False) -> None: ...   # `qmd update` (+ `qmd embed`)
    def schedule_reindex(self, *, delay: float = 5.0) -> None: ...
        # debounced background subprocess; never raises into the caller.
    def health(self) -> dict: ...   # {available, version, collection, indexed_at, models}

--------------------------------------------------------------------------------
9. withwrite.py  (T2)
--------------------------------------------------------------------------------
def withWrite(*, config, git, lock, paths: list[str], tool: str, summary: str,
              fn: Callable[[], dict]) -> dict:
    """Run fn() inside the full git protocol.

    Order: acquire lock -> wait_for_index_lock -> pull_rebase_autostash ->
    fn() (atomic writes) -> git.add(paths) -> git.commit(...) -> git.push().
    On rebase/stash conflict: abort, write conflict note, commit+push the note
    only if a fresh pull is clean, return the CONFLICT error dict.
    On push exhaustion: return success payload with pending_push=True (NOT an
    exception) and code PUSH_FAILED_LOCAL_COMMITTED recorded in the payload.
    Always release the lock in `finally`. Schedules a debounced reindex on success.
    Returns a dict merged into the tool result (keys: commit, pending_push,
    needs_index_update plus fn()'s payload).
    """
T3 imports this signature and may stub it until T2 merges.

--------------------------------------------------------------------------------
10. tools/*  (T1 read, T2 write, T3 structure)
--------------------------------------------------------------------------------
All tool functions take an explicit ToolContext as the first arg and return dicts.

# context.py (Wave 3, orchestrator)
@dataclass
class ToolContext:
    config: Config; guard: PathGuard; index: VaultIndex; search: Searcher; git: Git
def build_context(config: Config | None = None) -> ToolContext: ...

Read tools (T1, tools/read.py):
    notes_index(ctx) -> dict
    notes_search(ctx, query, area=None, categoryId=None, type=None, status=None,
                 limit=10, rerank=True) -> dict
    notes_read(ctx, path, fromLine=None, maxLines=None) -> dict
    notes_list(ctx, path=None) -> dict
    notes_recent(ctx, since="7d", author="any") -> dict
    notes_status(ctx) -> dict
Write tools (T2, tools/write.py):
    notes_create(ctx, categoryId, title, type, body, tags=None, related=None,
                 source=None, status="active") -> dict
    notes_update(ctx, path, body=None, edits=None, frontmatter=None) -> dict
    notes_append(ctx, path=None, daily=False, heading=None, text="") -> dict
Structure tools (T3, tools/structure.py):
    notes_move(ctx, path, targetCategoryId) -> dict
    notes_rename(ctx, path, newTitle) -> dict
    notes_sync(ctx) -> dict

Parameter naming is camelCase to match the JSON tool schema (MCP clients send the
published schema). Python internals stay snake_case.

notes_create filename rules: normal note -> "<NN> <Title>.md" inside the category
dir if it is a hub-scoped note, else "<Title>.md"; the MCP refuses to create
directly into 00 Meta. daily -> "40 Journal/41 Daily/YYYY-MM-DD.md" from the
Daily template; weekly -> "40 Journal/42 Weekly/YYYY-Www.md"; meeting ->
"40 Journal/43 Meetings/YYYY-MM-DD <Title>.md". DUPLICATE_FILENAME if the name
exists anywhere in the vault. Every created note gets full frontmatter plus the
qmd mirror.
notes_rename rewrites [[Old]], [[Old|alias]], [[Old#heading]] across all *.md in
one commit.

--------------------------------------------------------------------------------
11. tests/fixtures  (M4)
--------------------------------------------------------------------------------
@dataclass
class VaultFixture:
    root: Path       # tmp dir
    remote: Path     # bare "remote" repo
    human: Path      # human clone
    agent: Path      # agent clone ("origin" -> remote)
    def env(self, *, agent: bool = True, **overrides) -> dict[str, str]: ...
        # returns env for Config.from_env (VAULT_PATH etc.)
    def config(self, *, agent: bool = True, **overrides) -> Config: ...  # late import
    def seed(self) -> None: ...  # minimal vault: 00 Meta/00.00 Index.md + templates,
        # two categories with hub notes, a couple of notes, all with qmd mirrors.
@pytest.fixture
def vault(tmp_path) -> VaultFixture: ...
tests/conftest.py re-exports the fixture.

--------------------------------------------------------------------------------
12. FILE OWNERSHIP (no two agents touch the same file)
--------------------------------------------------------------------------------
Wave 0 (orchestrator): pyproject.toml, CONTRACT.md, src/notes_mcp/__init__.py,
    src/notes_mcp/errors.py, src/notes_mcp/tools/__init__.py, tests/__init__.py
Wave 1 - M1: src/notes_mcp/lock.py, src/notes_mcp/git.py, tests/test_lock.py, tests/test_git.py
         M2: src/notes_mcp/config.py, src/notes_mcp/paths.py,
             src/notes_mcp/frontmatter.py, src/notes_mcp/index.py,
             tests/test_config.py, tests/test_paths.py, tests/test_frontmatter.py, tests/test_index.py
         M3: src/notes_mcp/search.py, tests/test_search.py
         M4: tests/conftest.py, tests/fixtures/__init__.py, tests/fixtures/vault_fixture.py
Wave 2 - T1: src/notes_mcp/tools/read.py, tests/test_tools_read.py
         T2: src/notes_mcp/withwrite.py, src/notes_mcp/tools/write.py,
             tests/test_withwrite.py, tests/test_tools_write.py
         T3: src/notes_mcp/tools/structure.py, tests/test_tools_structure.py
Wave 3 (orchestrator): src/notes_mcp/context.py, src/notes_mcp/server.py,
    README.md, tests/test_server.py, tests/test_tools_integration.py
"""
