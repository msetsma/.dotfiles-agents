# Report & selection format

How `github-glowup` presents findings and asks what to apply.

## Report skeleton

Open with: repo `owner/name`, public/private, **detected audience** (plus a note
that the user can override), and which mode is running.

Then sections, omitting any that are empty:

1. **✅ Already good**: present items worth acknowledging.
2. **⚠️ Broken / dead**: failing badge, README >500 KiB (truncated), wrong
   language stats, dead relative links, unlicensed public repo, `gh` account
   mismatch / `gh` unavailable (running offline).
3. **README polish**
4. **Badges**
5. **Health files & automation**
6. **Repo settings (UI-only)**
7. **Commit signing**
8. **Deliberately skipped**: list what you are *not* suggesting and why (this is
   the audience gate made visible).

## Item format

> **<title>**: <one-line why>
> - Add `<path>`:
>   ```<lang>
>   <snippet>
>   ```
> - Effort: low/med/high · Audience: solo/team/OSS · Kind: file | UI-only
> - UI-only click: <Settings → …>

Keep it scannable; put exact snippets in fenced blocks; no fluff.

## Legend

| Marker | Meaning |
|---|---|
| ⚠️ high | missing LICENSE on a public repo; broken CI badge; README truncated |
| ● med | missing description/topics; no `SECURITY.md` when public |
| ○ low / nice | extra badges; TOC on a long README |

## Selection questions

After the report, ask with the agent's structured multi-select tool (batch ≤4
questions):

1. **Quick wins?**: apply the preset (description + topics, README polish,
   3-6 badges, LICENSE, `.gitignore`, `SECURITY.md` if public).
2. **README polish** (multi-select): alerts, Mermaid, `<details>`, TOC,
   footnotes, dark-mode logo, relative-link fixes.
3. **Badges**: multi-select from the shortlist generated for this repo.
4. **Health & automation** (multi-select): `SECURITY.md`, Dependabot, CI
   workflow, release notes, issue/PR templates, `CODEOWNERS`.
5. **Settings (you click)**: a checklist to acknowledge; the agent does not
   apply these.
6. **Signing**: offer SSH signing setup (edits `~/.gitconfig`; confirm first).

If the agent has no structured-question tool, print a numbered checklist and
wait for the reply.

## Apply & verify

- Apply only the selected file-based items. Never hand-edit a client's generated
  config.
- Validate: relative links resolve; workflow YAML parses; badge URLs use the
  correct workflow filename and an explicit `branch`.
- Do not commit or push unless asked. If asked, keep "README polish" and
  "automation" as separate commits.
