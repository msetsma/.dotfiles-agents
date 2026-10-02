# entra-mcp

Read-only Entra ID org-structure and access analysis, exposed two ways:

- **`entra`** — a terminal CLI with colorized tables, TSV export, and `fzf`
  pickers.
- **`entra-mcp`** — an MCP server exposing the same core as typed, read-only
  tools for an agent.

Both share one package (`entra_tool`). It answers the questions you actually
ask when designing and validating access rules:

- Who is in this group?
- What groups is this person in — and which of those actually grant access?
- Who reports to this person, and who do they report to?
- Why do two people in the same role have different access?

## Auth: no app registration, no secrets

Every Graph call goes through `az rest`, using whatever session `az login`
already established. There is **no app registration, no client secret, and no
certificate**, and the tool can see exactly what the signed-in user can see —
nothing more. The whole codebase is read-only: the only non-GET call is a
batched `$batch` *read* in the manager lookup.

If a tool reports a Graph error, call `check_auth` (MCP) or run
`az account show` (CLI) first.

```sh
az login          # once
```

The `az` binary is located via `PATH`, falling back to the usual absolute
locations (`/opt/homebrew/bin/az`, …) so GUI-launched MCP clients work without
your shell environment. Override with `ENTRA_AZ_PATH` if needed.

## CLI

```sh
entra user <upn-or-id>        [--direct|--transitive] [--tsv|--fzf] [--full]
entra group <name-or-id>      [--direct|--transitive] [--tsv|--fzf] [--full]
entra reports <upn-or-id>     [--direct|--transitive] [--tsv] [--refresh-cache|--no-cache]
entra users [query]           # fzf picker -> user detail + groups
entra groups [query]          # fzf picker -> group detail + members
entra dept [query]            # fzf department picker
entra compare-users <a> <b>   [--direct|--transitive]
entra compare-groups <a> <b>  [--direct|--transitive]
entra cache status
entra cache refresh [--users] [--groups] [--all]
```

Defaults to `--transitive`, so nested membership is included. `--tsv` writes a
file and prints nothing. `entra --help` has the full flag list.

## MCP tools

All read-only. Identifiers accept a UPN/email or an Entra object id; groups
accept an exact display name or an id.

| Tool | Answers |
|---|---|
| `search_users(query)` | Find a person / their email. Searches the local cache. |
| `search_groups(query)` | Find a group. Searches the local cache. |
| `list_users(department=…)` | Enumerate people by attribute: department, title, company, office, type, enabled, has_title. Complete and unranked, unlike a search. |
| `list_groups(name=…)` | Enumerate groups by attribute: name, mail/security flags, group type. |
| `get_user(user)` | Full profile: title, dept, phones, status, type, proxy addresses. |
| `get_user_groups(user, mode)` | The groups a person is in. |
| `get_user_owned_groups(user)` | The groups a person owns — owners can add members. |
| `get_group(group)` | Group details and owners. |
| `get_group_members(group, mode, include_groups)` | The people in a group; `include_groups` adds the nested groups. |
| `group_audit(group, mode)` | Access review in one call: disabled accounts, guests, untitled members, nested groups, owners. |
| `why_user_in_group(user, group)` | The nesting chain that grants access: `User -> Team Group -> Access Group`. |
| `get_manager(user)` | Who someone reports to. |
| `get_reports(user, mode, include_groups)` | Nested reporting tree under a person. |
| `compare_users(a, b, mode)` | Shared vs. unique groups for two people. |
| `compare_groups(a, b, mode)` | Shared vs. unique members for two groups. |
| `check_auth()` | Is the `az` session usable, and as whom? |

**One deliberate difference from the CLI:** the terminal tables hide disabled
and title-less people, because a table is noisy. The MCP returns them, with
`accountEnabled` and `jobTitle` always present, so an agent can decide for
itself. `get_reports` also reports how many the CLI would have hidden.

`get_reports(include_groups=True)` costs one Graph call per person and is
refused above 100 people — narrow the tree first.

`why_user_in_group` answers membership from one transitive lookup, then traces
the chain with a bounded bidirectional walk (about 120 groups, a couple of
seconds). If the chain is deeper than that bound, `has_access` is still correct
and `path` is null.

`group_audit` reads members, nested groups, and owners in a few calls and
returns counts plus capped samples, rather than the whole member list. Note
that `get_group_members` returns people only — nested groups are omitted unless
you pass `include_groups`, so a plain count can look lighter than the group's
real reach.

## Search is fuzzy

You do not need the exact spelling. `search_users` and `search_groups` score
three tiers, best first:

1. **Substring** — the token appears in the field (a prefix beats a mid-word hit).
2. **Subsequence** — the letters appear in order with gaps allowed. This is what
   the CLI's `fzf` does, and it is what makes `Xuli` find `Xueli`.
3. **Typo tolerance** — a sequence comparison catches transposed and wrong
   letters, so `Setmsa` finds `Setsma` and `huebers` finds `Hubers`.

Multiple words AND together in any order, so `kevin tian` finds
`Tian, Xueli (Kevin)`.

### Loosening the net

Both tools take `limit` (how many results) and `strictness` (how good a match
has to be). The floor is *relative to the best score found*, so it adapts
whether the best match is exact or merely the least-bad option available.

| `strictness` | Floor | `Setmsa` | `Tain` |
|---|---|---|---|
| `strict` (default) | within 90% of the best | 2 | 17 |
| `normal` | within 60% | 25 *(capped)* | 23 |
| `loose` | none | 25 *(capped)* | 25 *(capped)* |

`strict` is usually just the person you meant, so a loose query like `Setmsa`
returns the two Setsmas rather than 1,962 scattered near-misses. Reach for
`loose` when the answer is missing, or when a strong-but-wrong match is
crowding out the typo you wanted: `Tain` finds `Tainoff` strictly, but `loose`
also surfaces `Tian`.

Both passes always run — the typo pass is cheaper than the fuzzy one (~75ms vs
~115ms over 42k names), so keeping near-misses out is the floor's job, not a
reason to skip work. A search costs ~120–240ms.

## Listing vs searching

`search_users` / `search_groups` answer "who matches this string": ranked
best-first and cut off by a score floor. `list_users` / `list_groups` answer
"who is in this set": complete and unranked, with `count` reporting the true
number of matches even when `limit` truncates `matches`. Reach for a list for
"everyone in Finance" or "all disabled accounts"; a search for a name you only
half-remember.

Text filters match whole words, so `department="IT"` does not leak into
"Digital". Filters combine with AND, and a list needs at least one — with none
it would return all 42k rows.

## Caching

The user and group listings cache for 24 hours in
`~/.cache/entra_membership/`, so search is instant. A `launchd` job warms them
daily at 08:30. Force a refresh with `entra cache refresh`, or bypass with
`--no-cache`. Tune with `ENTRA_MEMBERSHIP_CACHE_TTL_SECONDS`.

**A failed refresh serves the stale cache instead of failing.** The directory
changes slowly, so a day-old list beats an error. This applies only to the
implicit refresh a search does — an explicit `entra cache refresh` still
surfaces the failure, as does a machine with no cache yet. MCP search responses
carry `from_cache` and `stale`, and the CLI picker legend shows
`CACHE=fresh|hit|stale`, so both front ends can tell the difference.

Report trees cache direct-report lookups separately, one entry per manager with
its own 24h TTL. Expired entries are dropped on save, so the file does not grow
without bound (`ENTRA_REPORTS_MAX_WORKERS` controls the fetch concurrency).

## Install

```sh
uv tool install --editable ~/.agentdots/mcp/entra-mcp --with fastmcp
```

This provides `entra` and `entra-mcp` in `~/.local/bin`. The CLI itself has no
runtime dependencies; only the MCP server needs `fastmcp`.

To run the MCP without installing:

```sh
uv run --directory ~/.agentdots/mcp/entra-mcp --extra mcp entra-mcp
```

## Tests

Offline, no network and no Entra tenant needed — Graph is mocked:

```sh
uv run --directory ~/.agentdots/mcp/entra-mcp pytest
# or
python3 -m unittest discover -s tests
```

## Planned

`entra.md` used to describe these as if they existed. They do not — this is the
wishlist, and it is the part best suited to an agent rather than a CLI:

- `reports_group_summary` — the `42/50` baseline-access table across a report
  tree.
- `reports_exceptions` — people whose membership differs from peers with the
  same manager, title, or department.
- `--json` output on the CLI for non-MCP automation.

## Layout

```
entra_tool/     core: Graph access, resolution, org walk, CLI + formatters
entra_mcp/      MCP server (thin tool layer over entra_tool)
tests/          offline tests for both
```

`entra_tool` is the single source of truth. The CLI formats rows; the MCP
returns dicts. Anything that both need lives in the core, not in either front
end.
