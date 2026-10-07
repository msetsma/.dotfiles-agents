# pi-quiet

A Pi package that makes the transcript readable by default. Instead of dumping
every tool result into the main thread, each tool call collapses to one line and
its full output moves behind a single key.

```
read  apps/web/src/server.ts:12-40   → 28 lines
$ npm --filter web test              → exit 0 · 41 lines
edit  apps/web/src/server.ts         → +3 −1
grep  /verifyToken/ apps/web         → 7 matches
mcp__jira__search flaky test         → 12 lines
```

Press `alt+o` on any of them for the full, scrollable body:

```
╭─ edit  apps/web/src/server.ts ──────────────────╮
│ apps/web/src/server.ts                          │
│                                                 │
│ @@ -12,7 +12,7 @@                               │
│ -  if (!claims.exp) throw new Error(...)        │
│ +  if (!claims.exp) return null                 │
│                    ↑↓ scroll · esc close         │
╰─────────────────────────────────────────────────╯
```

## What it changes

Three levers, matching the three surfaces Pi exposes to extensions:

| Lever | Mechanism | Effect |
|---|---|---|
| **Tools** | `pi.registerToolRenderer()` | One-line call + semantic outcome for every tool, built-in **and** MCP. Behaviour is untouched — only rendering. |
| **Windows** | `ctx.ui.custom({ overlay: true })` | `alt+o` opens a scrollable detail overlay for the most recent tool calls; `[` / `]` (or ←/→) cycle back through history. |
| **Modes** | `/quiet` + `quiet.json` | `quiet` (default), `normal` (Pi's native renderers), `verbose` (full bodies inline). |

Errors are always rendered as a single red line, even in `quiet` mode, so a
failure never hides. Nothing here changes what the model sees; the session
record and HTML export keep the complete tool results.

The renderer is registered with `registerToolRenderer`, not by re-registering
the built-in tools, so pi-quiet does not drift when Pi changes its tool set and
it covers MCP tools added at runtime. `ctrl+o` (Pi's own expand) still works.

## Install

Declared in this repo's catalog (`catalog/packages/pi-quiet.toml`, source
`packages/pi-quiet`), so `make sync` merges it into `~/.pi/agent/settings.json`
and runs `pi update --extensions`. To try it without syncing:

```sh
pi --no-extensions -e ~/.dotfiles-agents/packages/pi-quiet/extensions/quiet.ts
```

## Commands and keys

| Input | Action |
|---|---|
| `alt+o` | Open the detail overlay for the newest tool call |
| `[` / `]` or ← / →, `h` / `l` | Previous / next tool call in the overlay |
| `↑` / `↓`, `j` / `k`, `pageUp` / `pageDown`, `home` / `end` | Scroll the overlay body |
| `esc` or `q` | Close the overlay |
| `/quiet` or `/quiet status` | Report the current mode |
| `/quiet quiet\|normal\|verbose` | Switch rendering mode (persisted) |
| `/quiet detail` | Same as `alt+o` |
| `ctrl+o` | Pi's native expand/collapse (unchanged) |

## Configuration

`~/.pi/agent/quiet.json` (created on the first mode change, alongside
`zentui.json`):

```json
{
  "mode": "quiet",
  "detailKey": "alt+o",
  "recentLimit": 40,
  "previewLines": 0,
  "alwaysShowErrors": true
}
```

| Key | Default | Meaning |
|---|---|---|
| `mode` | `"quiet"` | Startup rendering mode: `quiet`, `normal`, `verbose`. |
| `detailKey` | `"alt+o"` | Keybinding for the overlay. Requires `/reload` after editing. |
| `recentLimit` | `40` | How many recent tool calls the overlay can cycle through. |
| `previewLines` | `0` | Lines of body shown inline when a tool row is expanded with `ctrl+o` (`0` disables, deferring all detail to the overlay). |
| `alwaysShowErrors` | `true` | Render one-line errors in `quiet` mode. |

Malformed or unknown values fall back to the defaults; out-of-range numbers are
clamped. `PI_CODING_AGENT_DIR` is honoured when locating the file.

## Companion settings

pi-quiet only owns tool rendering. Two agent-level settings round out the quiet
defaults (see the repo root README for where they're set):

- `quietStartup: "header"` — keeps the version/key-hint header, drops the
  resource listing.
- `hideThinkingBlock: true` — collapses thinking to a single hidden label.
  Pi's `ctrl+t` toggles it back and persists the change.

Zentui continues to own the editor, user messages, working line, footer, and the
thinking renderer; pi-quiet sits beside it and does not touch those surfaces.

## Layout

```
packages/pi-quiet/
├── extensions/quiet.ts   # wiring: renderers, events, /quiet, overlay component
├── src/config.ts         # quiet.json load/save + coercion
├── src/summaries.ts      # pure formatting (no host imports)
└── tests/                # node --test, *.test.ts
```

`src/` is deliberately free of host imports so the formatting logic is testable
with plain Node.

## Verify

```sh
cd packages/pi-quiet && node --test tests/*.test.ts
```

Chain-of-custody note: the renderer and overlay wiring is also smoke-tested by
importing `extensions/quiet.ts` with a stubbed `ExtensionAPI` and the real
`@earendil-works/pi-tui`; that check is not committed because it needs the host
packages linked into `node_modules/`.
