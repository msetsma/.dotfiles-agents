# Claude Code mods

Mods are Claude Code plugins built from a hooks module: panes, bands above the
prompt, status line entries, toasts, slash commands, tool-call guards. Each mod
lives in its own repo under [github.com/msetsma](https://github.com/msetsma).
This folder holds only the `msetsma-mods` marketplace, which lists every mod by
GitHub source, the same way third-party plugins come in.

## Mods

| Mod | Repo | What it does |
|---|---|---|
| `style` | [msetsma/claude-style](https://github.com/msetsma/claude-style) | Restyles Claude Code's UI, each part set in the `/style` pane: prompt bubbles, tool-call rails or cards colored by tool or status, a frame around plugin bands, notices, mode badges, the prompt hint, a tab over question dialogs and the turn-time line. Colors are theme keys, so custom themes apply. |
| `sidebar` | [msetsma/claude-sidebar](https://github.com/msetsma/claude-sidebar) | `/sidebar` toggles a pane with model, turns, burn rate ($/hr, $/turn), context and rate-limit bars with reset times, cost, git branch, ahead/behind and the changed files from `git status` with `+add −del` (click to copy), quick-prompt buttons that fill the composer (edit them in place), slash commands (skills, plugin and user commands, MCP prompts) grouped by package (click to fill `/name`), todos, subagents, MCP servers with per-server call and token estimates, tool activity (running calls spin with a live timer, merged repeats, click to copy) and failed calls (click to expand). Click a header to collapse it; ⚙ sets each section to show/auto/hide, the row count, and the look: icon set (unicode, Nerd Font, ASCII), bar style, color and width, header rule, spacing, spinner and bar motion, saved across sessions. Refreshes every 5 s. |

### Hook order and `style`

Mods in the same tier nest in `enabledPlugins` order in `~/.claude/settings.json`,
first outermost. A band hook that answers without calling `next` hides
everything beneath it, so `style` only frames a mod's band when it sits above
it: keep `style@msetsma-mods` first in `enabledPlugins`. A mod installed later
lands after it, which is the order that works.

### Installing from the private repos

The mod repos are private, and plain `git@github.com` SSH here logs in as the
work account (`~/.gitconfig-personal` only applies inside an existing clone).
Claude Code clones each mod into its plugin cache on install and on each
update, so run those with the personal key:

```sh
export GIT_SSH_COMMAND="ssh -F /dev/null -i ~/.ssh/id_ed25519_personal -o IdentitiesOnly=yes -o HostName=ssh.github.com -p 443"
claude plugin marketplace update msetsma-mods
claude plugin install <mod>@msetsma-mods   # or `claude plugin update <mod>@msetsma-mods`
```

## Layout

```
mods/
└── .claude-plugin/marketplace.json   # lists every mod by GitHub source
```

A mod repo has this shape:

```
<mod-repo>/
├── .claude-plugin/plugin.json     # { "name", "version", "description" }
├── hooks/hooks.json               # { "modules": ["./register.tsx"] }
├── hooks/register.tsx             # export const register: Register = (on, options) => { ... }
├── types/index.d.ts               # only if the mod keeps values in $.state
└── tests/*.test.ts                # run with `claude plugin test <clone>`
```

## Adding a mod

1. Create a repo under github.com/msetsma with the files above.
2. Add an entry to `plugins` in `.claude-plugin/marketplace.json`:
   `{ "name": "<mod-name>", "source": { "source": "github", "repo": "msetsma/<repo>" } }`.
3. Check the manifest: `claude plugin validate mods`.
4. Pick up the new entry and install it once:
   `claude plugin marketplace update msetsma-mods`, then
   `claude plugin install <mod-name>@msetsma-mods`.

## One-time setup per machine

```sh
claude plugin marketplace add ~/.dotfiles-agents/mods
claude plugin marketplace update msetsma-mods   # after the marketplace changes
```

## Editing a mod

Clone the mod's repo anywhere and start a session with
`claude --plugin-dir <clone>`. That loads the working tree and reloads on every
save. Check it with `claude plugin validate <clone>` and
`claude plugin test <clone>`. Push the change, then run
`claude plugin update <mod-name>@msetsma-mods` on each machine to pick it up.
