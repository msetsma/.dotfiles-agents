---
name: peek
description: "See what a CLI or TUI renders in a terminal: run it in a hidden tmux pane of a fixed size, type keys, and read the screen back as text or as a real Ghostty screenshot. Use when building or debugging a terminal UI, a Claude Code mod (pane, band, status line), a prompt or progress output, or any command whose on-screen layout matters, and to check a change looks right before asking the user for a screenshot."
---

# peek

`~/.dotfiles-agents/bin/peek` (on PATH as `peek`) runs a command in a detached
tmux pane, optionally types keys, waits for the screen to settle, and prints the
screen as plain text. With `-o shot.png` it also saves a real screenshot taken
through Ghostty, which you can then read as an image. Use it to look at a
terminal program yourself instead of asking the user for a screenshot.

## Usage

```sh
peek [options] -- <command...>         # fresh terminal: start, keys, print, close
peek -S <name> [options] -- <command>  # start a named terminal and keep it open
peek -S <name> [options]               # keys + capture on that terminal again
peek --kill <name> | --list
```

| Option | Meaning |
|---|---|
| `-s WxH` | terminal size, default `120x40`; match the user's real width when layout matters |
| `-r REGEX` | after start, wait until the screen matches (the program is ready for keys) |
| `-k TEXT` | type text literally |
| `-K KEY` | press a key by tmux name: `Enter`, `Escape`, `Tab`, `BSpace`, `Up`, `C-c`, `F2` |
| `-u REGEX` | capture once the screen matches; without it, capture when the screen stops changing |
| `-t SECS` | longest wait per step, default 15 |
| `-a` | include scrollback above the visible rows |
| `-c` | keep ANSI color escapes (to check colors; noisy to read) |
| `-o FILE.png` | also save a screenshot: a borderless Ghostty window attaches for ~1 s and is captured (macOS + Ghostty) |

Keys run in the order given. A command that exits stays on screen followed by
`[peek: exited N]`.

## Recipes

```sh
# A plain command at a narrow width
peek -s 60x20 -- mytool --help

# Claude Code with a mod's pane open (a mod change: reload is automatic in a new session)
peek -s 170x45 -r '❯' -k /sidebar -K Enter -u 'Sidebar opened' -- claude

# Crop to the right-hand pane of a split screen
peek -s 170x45 -r '❯' -k /sidebar -K Enter -- claude | cut -c112-

# A real screenshot when colors, glyphs or alignment matter; crop with ImageMagick, then Read it
peek -o /tmp/shot.png -s 170x45 -r '❯' -k /sidebar -K Enter -u 'Sidebar opened' -- claude
magick /tmp/shot.png -gravity East -crop 36%x100%+0+0 +repage /tmp/pane.png

# Drive a TUI over several steps without restarting it
peek -S app -r 'Ready' -- ./app
peek -S app -K Down -K Enter
peek -S app -k 'search term' -K Enter -u 'results'
peek --kill app
```

## Know the limits

- **A fresh process.** Anything session-specific (live counters, history, a
  running Claude conversation) starts empty. Use it for layout and behaviour,
  not to read the user's live numbers.
- **Text hides color problems.** Text that is invisible against its
  background (e.g. a theme's diff-background keys used as text colors) still
  shows in the text capture; take a `-o` screenshot when color or glyphs matter.
  Nerd Font glyphs are captured as text but may display as blanks in tool output.
- **Screenshots** briefly flash a borderless Ghostty window (titled `peek-shot-*`;
  AeroSpace floats it by a rule in the user's aerospace.toml) and need Screen
  Recording permission for the terminal app. `claude` started in an untrusted
  folder shows the trust dialog first: run it from a trusted project.
- **Its own tmux server** (`tmux -L peek`), so the user's tmux sessions are never
  touched. Always `--kill` a named terminal when done (`--list` shows strays).
- Interactive auth prompts, trust dialogs and first-run screens show up like any
  other screen; wait for them with `-r` and answer with `-k`/`-K`.
