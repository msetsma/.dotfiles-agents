---
name: demo-gif
description: "Record a terminal demo GIF of a CLI or TUI with vhs for a README: an isolated, offline, repeatable tape, then frames pulled out to check it. Use when asked for a terminal GIF, demo recording, screencast, or vhs tape."
---

# demo-gif

A demo GIF is a [vhs](https://github.com/charmbracelet/vhs) tape checked into
the repo (`demo/demo.tape`) plus the GIF it renders (`demo/demo.gif`). Anyone
can rerun the tape and get the same recording, so it never touches the
author's real config, network or paths.

`~/.dotfiles-agents/bin/demo-gif` handles the mechanics (call it by full path
if `demo-gif` is not on PATH; link it with
`ln -s ~/.dotfiles-agents/bin/demo-gif ~/.local/bin/`):

```sh
demo-gif new [demo/demo.tape]   # starter tape with the hidden-setup block
demo-gif [demo/demo.tape]       # vhs render, then frames, size and length
demo-gif frames demo/demo.gif   # frames only (DEMO_GIF_FPS, default 1)
vhs validate demo/demo.tape     # syntax check without rendering
```

Needs `vhs` and `ffmpeg` (`brew install vhs ffmpeg`).

## Steps

1. **Plan the scenes.** Read the README and the code to find what the feature
   looks like on screen. Pick 3-6 beats that each show one thing changing.
   Under ~30 s total. Lead with the payoff: the first visible change lands
   within ~1 s of the GIF starting, because viewers leave fast. Anything the
   feature needs first (history, files, a populated list) happens in the
   hidden block, so the opening frame is already the interesting state. Only
   show setup on screen when the setup is the feature.
2. **Isolate.** The recording runs in a throwaway `HOME` (the starter tape does
   this), so the tool reads no real config and the screen shows `~/my-project`,
   not a username. Write the config the tool needs under that `HOME`, or point
   the tool's config-dir env var at a temp dir. Plugins or extensions load from
   `$REPO`, captured before `cd`. If the tool or its runtime lives next to the
   repo, use `${TOOL_DIR:-$REPO/../tool}` so other checkouts can override it,
   and name it in the tape's `# Needs:` line and the README.
   - The throwaway `HOME` also drops `~/.gitconfig` and version-manager setup.
     If the demo uses git, write a minimal `~/.gitconfig` in the hidden block.
   - Turn off first-run noise: update checks, what's-new or changelog screens,
     telemetry notices, startup banners, env or dotfile loading.
   - Set the tool's own theme to match the vhs theme (dark with dark).
   - Lengthen hint, toast or menu timeouts so they stay up while the viewer
     reads.
3. **Go offline.** If the tool calls a network service (LLM, API), give it a
   fake that returns canned replies. Many SDKs ship one: look for `faux`,
   `mock` or `fake` exports before writing a mock server. Often it is a small
   plugin or provider file in `demo/` with a short list of scripted replies.
   Make the replies read like a real session, and check the fake does not show
   on screen (a loaded-plugins list, a model name, a status bar). Name it
   plainly or hide it with a quiet-startup setting.
4. **Hide the setup.** Everything up to the moment the UI is ready goes inside
   `Hide` ... `Show`, typed with `Type@1ms` (hidden typing still costs render
   time). Launch the tool there and wait for it with
   `Wait+Screen@30s /text the ready UI shows/`; fall back to a fixed `Sleep`
   only if nothing reliable appears. Before the first render, run the hidden
   commands in a real shell (or with the `peek` skill) to confirm the tool
   starts with that config. A failed setup still renders, as a GIF of errors.
5. **Write the scenes.** Open with at most `Sleep 500ms` after `Show`, then
   the first change. For input: `Type "..."`, `Sleep 400ms`, `Enter`, then
   `Sleep 1.5-3s` after each visible change (or `Wait` for the result).
   Several commands can share one line.
6. **Render and look.** `demo-gif demo/demo.tape`. Read the first frame before
   anything else: a shell prompt or error text there means the hidden setup
   failed. Then Read 6+ frames spread across the run and check that the
   feature shows in each beat, with no home path, username, hostname, real
   project names, tokens, warnings, error text or long dead stretches. For a
   beat that 1 fps misses, add `Screenshot demo/beat.png` to the tape (do not
   commit those). Fix and rerender until it is right.
7. **Embed.** In the README, after the intro: `![<what the GIF shows>](demo/demo.gif)`.
   Alt text describes the content, not "demo". Add one line under Development
   saying `vhs demo/demo.tape` regenerates it and what it needs.
8. **Commit** the tape, the GIF and any fake in `demo/`. If the repo
   typechecks or lints, make sure the fake passes or is excluded.

## vhs gotchas

- **Alt keys** (seen on macOS). `Alt+o` arrives as a plain `o`. Send the
  escape sequence by hand: `Escape` then `Type@1ms "o"`, and raise the tool's
  escape timeout in the hidden block if it has a setting for it. If it has
  none, show the feature through a command or a non-Alt binding instead.
- `Ctrl+Space`, `Shift+Tab`, `Escape`, arrows and `Ctrl+<letter>` work as-is.
- Quote with backticks when the typed text has double quotes:
  ``Type `printf '{"a":1}' > ~/cfg.json` ``.
- macOS temp dirs (`/tmp`, and `mktemp -d` under `/var`) are symlinks into
  `/private`. Tools print the resolved path, which shows as `~/...` only when
  `HOME` is resolved too, so the starter sets `HOME` with `pwd -P`. Do not
  `cd /tmp/x` in a tape.
- vhs runs from the current directory; run it from the repo root so relative
  `Output` and `$PWD` paths hold.

## Size

Aim for under 1.5 MB (the script warns past 2 MB). In order: cut `Sleep`s and
scenes, drop `Set Framerate` to 15 or 10, then shrink `Width`/`Height`.

GitHub shows README images at most ~830 px wide, so a 1200 px GIF is scaled
to about 70%. Use `FontSize` 22 at `Width 1200` (about 85 columns), or keep
the ratio if you change the width. Go smaller only when the tool needs more
columns than that, and then cut the width the tool doesn't use instead.
