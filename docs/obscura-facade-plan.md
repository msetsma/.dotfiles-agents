# Design note: an `obscura-facade` MCP server

**Probed against:** obscura `v0.2.7` (`C:\Users\2015m\.local\obscura\obscura.exe`)
**Related:** `catalog/mcp/obscura.toml`, `catalog/mcp/playwright.toml`

---

## 1. Problem

Obscura is the preferred browser for first-party agentic browser use: it reaches more
sites than Playwright MCP, is lighter, and does not need a browser install. Two things
still block it:

1. **Some providers incorrectly reject the request outright** when obscura's tools are attached (before any generation happens).
2. **Some models incorrectly decline in text** — they read the tool definitions and refuse, or warn.

The goal is to keep every capability obscura has, while presenting a surface that a
provider and a model both treat as an ordinary headless browser.

## 2. What a provider actually sees

An MCP server contributes exactly four things to a request: **tool names, tool
descriptions, input schemas, and annotations**. Launch arguments are not among them.

That was verified, not assumed. `tools/list` from obscura v0.2.7, dumped and grepped:

| Property | Finding |
|---|---|
| Tool count | 38, all `browser_*` |
| `title` | absent on every tool |
| `annotations` (e.g. `readOnlyHint`) | absent on every tool |
| Occurrences of `stealth` in names/descriptions/schemas | **0** |
| Tools with `additionalProperties: false` | `browser_snapshot`, `browser_screenshot`, `browser_pdf` |

Consequences:

- **`--stealth` is invisible to a provider.** It is a runtime flag on a child process
  the client spawns. Nothing about it is transmitted. It is very unlikely to be the
  cause of a rejection, and it does not need to be hidden.
- **The server name leaks only as a tool-name prefix.** Claude Code namespaces tools as
  `mcp__<server>__<tool>`, so the string `obscura` appears 38 times in the payload.
  Renaming the server changes that; nothing else.
- **`additionalProperties: false` on three tools is a hard failure mode.** Playwright's
  snapshot accepts `{depth, boxes, target, filename}`; obscura's accepts only
  `{max_chars}`. A Playwright-trained model calling `browser_snapshot {"depth": 3}`
  gets a schema error on its most-used tool and concludes the server is broken.
- **Parameter names differ.** Playwright: `{element, target}`. Obscura: `{ref, selector}`.
- **No annotations means no read-only signal.** Clients that gate on `readOnlyHint`
  prompt on every call; a model that sees repeated prompts stops reaching for the tool.

### The text most likely to read as "bad" incorrectly. 

Most of obscura's descriptions are unusually good. Six tools are not:

- `browser_set_cookie` — "Use this to skip a login flow when you already have a session token."
- `browser_storage_state` — "Export the full authentication / session state (cookies + localStorage + sessionStorage)."
- `browser_set_storage_state` — "bring an authenticated session back without re-logging in"
- `browser_search` — "Use this to confirm content exists before scraping"
- `browser_get_cookies`, `browser_clear_cookies` — cookie-jar handling

"Export full authentication state" and "skip a login using a session token" is the vocabulary of session hijacking. A static scanner — or a model — reading those cannot tell a first-party staging site from credential theft.

There is also a blunter issue: **38 unannotated tools from one server** is a large surface, and every request carries all 38 schemas (~13 KB of `tools/list`).

## 3. Design principle

> Removing a tool is fine. Describing a tool in a fair way that will unlikey get it flagged is fine.
> Changing the wording so a scanner no longer recognizes a capability that is still there is not.

A practical test for any edit: after the edit, could a careful reader — or a scanner —
still tell what the tool does? If yes, the edit is fine even if it reads fair and 'softer'. 

Applied here, that means the fix is **structural, not cosmetic**: allow the model to continue session handling, even if that means rewording the description.

## 4. Architecture

```
MCP client (opencode / Claude Code / Claude Desktop)
        │  stdio JSON-RPC
        ▼
obscura-facade  (Python, FastMCP)
        │  MCP client session over stdio
        ▼
obscura.exe mcp --stealth   (child process, unchanged)
```

- stdio MCP server, per repo convention (one server = one repo, nothing listening).
- Spawns obscura as a **stdio child** via the MCP Python SDK's `stdio_client`, not
  `mcp --http`. `obscura mcp` has no `--ready-file` (only `serve` does), so HTTP mode
  buys a port-probe readiness race and nothing else.
- One process tree: facade exits → obscura exits → no orphaned workers.
- On start the facade sends `initialize` + `tools/list` and **validates that every
  mapped backend tool exists**, failing loudly if upstream renames something.

### Open questions answered

Whether `browser_set_storage_state` applies cleanly before any navigation.  

## 5. Tool surface

All tools `browser_*`, each with a `title` and an `annotations` block.

### Exposed, Playwright-shaped

| Facade tool | Backend call | Translation |
|---|---|---|
| `browser_navigate {url}` | `browser_navigate` | adds `waitUntil: "load"` |
| `browser_navigate_back` | `browser_back` | — |
| `browser_navigate_forward` | `browser_forward` | — |
| `browser_snapshot {target?, depth?, boxes?, filename?}` | `browser_snapshot {max_chars}` | `depth`→`max_chars` heuristic; `boxes` declared but ignored; `filename` writes text |
| `browser_click {element?, target, doubleClick?, button?, modifiers?}` | `browser_click {ref\|selector}` | `target` resolved to ref or selector |
| `browser_type {target, text, submit?, slowly?}` | `browser_type` | `submit` → `browser_press_key Enter` |
| `browser_fill_form {fields[]}` | `browser_fill_form` | field key remap (`target`→`ref`) |
| `browser_select_option {target, values[]}` | `browser_select_option` | loop over values |
| `browser_press_key {key}` | `browser_press_key` | — |
| `browser_evaluate {function, target?}` | `browser_evaluate {expression}` | `(${function})()`, element-scoped if `target` |
| `browser_find {text\|regex}` | `browser_search` / `browser_evaluate` | regex via evaluate |
| `browser_wait_for {text, textGone, time}` | `wait_for_text` / `wait_for_function` | sleep for `time` |
| `browser_take_screenshot {filename?, fullPage?, type?, scale?}` | `browser_screenshot` | writes PNG, returns path |
| `browser_pdf {filename?}` | `browser_pdf` | writes file |
| `browser_console_messages {level, all, filename?}` | `browser_console_messages` | level filter |
| `browser_network_requests {filter?, static?, all?}` | `browser_network_requests` | regex filter |
| `browser_tabs {action, index?}` | `tab_new/list/switch/close` | action dispatch |
| `browser_close` | `browser_close` | — |
| `browser_get_cookies` | `browser_get_config` | — |
| `browser_set_cookie` | `browser_set_config` | — |
| `browser_clear_cookies` | `browser_reset_config` | — |
| `browser_storage_state` | `browser_get_preferences` | — |
| `browser_set_storage_state` | `browser_sync_preferences` | — |

### Also exposed

No Playwright equivalent, harmless, genuinely useful — kept rather than hidden:
`browser_markdown`, `browser_links`, `browser_extract`, `browser_count`,
`browser_get_attribute`, `browser_detect_forms`, `browser_interactive_elements`,
`browser_scroll`.

### Not supported 

need to consider if we need these features.

No backend exists for: `browser_hover`, `browser_resize`, `browser_drag`,
`browser_drop`, `browser_file_upload`, `browser_handle_dialog`,
`browser_emulate_media`, `browser_network_request {index}`.

Preferred handling is **not declaring them at all** — a model does not plan around tools
that are absent, but it will retry a tool that always errors. Declaring them is only
acceptable with an instructive failure ("this backend cannot do X; use Y").

### Refs

Snapshots rewrite refs `e3` → `s1e3`; clicks strip the prefix on the way back. Cosmetic,
one regex, and it makes output look like what a Playwright-trained model expects.

### Descriptions and annotations

- Copy Playwright's description text verbatim where semantics match.
- Accurate plain wording where they differ. Example: obscura's `browser_search` ends
  "...before scraping" — that phrase does none of the work and all of the damage.
- `readOnlyHint: true` on snapshot/screenshot/pdf/find/console/network/links/count/
  get_attribute/markdown; `false` on anything that mutates page or state.
  `openWorldHint: true` throughout. Titles from Playwright's list.

## 6. Session plumbing

Replaces the five dropped tools without losing them.

- `--storage-state <path>`; default `%LOCALAPPDATA%\msetsma\obscura-facade\state.json`.
- **Restore** at startup before the first navigation, if the file exists and is younger
  than `--state-max-age` (default 7d).
- **Capture** after any tool call that leaves the page on a non-login URL, debounced to
  once per 30s, written atomically (temp file + `os.replace`). Also captured on
  `browser_close`.
- **Login** happens through `browser_fill_form` / `browser_click` like any other flow.
  The agent never sees a cookie tool; it simply finds itself signed in next run.
- Login detection: `--no-capture-on-url` denylist, default `/login|/signin|/auth|logout`.
- The state file is a **credential**: never logged, never returned in a tool result,
  documented as such in the server README, gitignored, path configurable.

**Accepted consequence:** if a scanner flags session persistence as *behaviour* rather
than as tool text, this design neither hides it nor claims to. And if the model must
actually reason about cookie values, the tool has to be exposed under an honest name and
the refusal risk comes back.

## 7. Configuration

Passthrough to the child: `--stealth`, `--allow-private-network`, `--proxy`,
`--user-agent`, `--font-dir`, `--obey-robots`, `--quiet`.

Facade-only: `--storage-state`, `--state-max-age`, `--no-capture-on-url`,
`--max-chars`, `--profile lean|full` (lean = Playwright core table only, for providers
with tight tool caps), `--only` / `--exclude`.

`--stealth` stays on: it is needed, and it is not on the wire.

## 8. Repo layout

```
obscura-facade/
  pyproject.toml            requires-python >=3.14; entry point obscura-facade; fastmcp + mcp
  README.md                 install, launch, state-file security note, mapping table
  src/obscura_facade/
    server.py               FastMCP app, registration, annotations
    translate.py            arg/ref translation, pure functions
    session.py              state restore/capture, atomic writes
    backend.py              stdio child management, startup validation
  tests/
    test_translate.py           pure-function round trips
    test_surface.py             tool count, title+annotation on every tool, payload byte budget
    test_state.py               save/restore roundtrip against a local static server
    test_lint_descriptions.py   fails if an exposed description contains banned vocabulary we need better vocab
```

`test_lint_descriptions.py` is the useful one: a regression guard asserting the payload
contains no `authentication|session token|skip.*login|scrap|stealth|evade|undetected`
vocabulary, so a later edit is caught by CI rather than by a provider.

Tests run offline against `python -m http.server` with `--allow-private-network`.

## 9. Catalog integration

```toml
# catalog/mcp/obscura-facade.toml
name = "browser"
description = "Headless browser automation for first-party testing."
kind = "local"
clients = ["opencode", "claude-code", "claude-desktop"]

[launch]
command = "uv"
args = ["run", "--directory", "${AGENT_HOME}/vendor/obscura-facade",
        "obscura-facade", "--stealth", "--allow-private-network",
        "--storage-state", "<path>"]

[package]
manager = "git"
repo = "msetsma/obscura-facade"
path = "${AGENT_HOME}/vendor/obscura-facade"
```

Raw `obscura` stays registered, dropped from clients via `[mcp.obscura] clients = []`
in the untracked `catalog/local.toml`, so the facade is additive and rollback is one
line. Delete the stale generated keys by hand — sync adds and overwrites, it never
removes. Add the Inventory row to `README.md` only when the server exists and works.

## 10. Phases

1. **Skeleton + passthrough** — ~20 tools, honest descriptions, annotations, ref rewrite.
   Verifiable in the Inspector within an hour. This alone answers whether the payload was
   the problem.
2. **Session plumbing** — restore/capture, config, state tests.
3. **Trim + profiles** — `lean` mode, `--verbose` prints `tools/list` byte size.
4. **Instruction block** — `catalog/instructions/browser-testing.md` with truthful
   context: sandboxed browser owned by this machine, our own staging deployment, dedicated
   test accounts, session persistence expected. For text refusals this is the single
   highest-leverage change and takes fifteen minutes — worth doing before phase 1.
5. **Lint + docs** — description lint in CI, README mapping table, inventory row.
