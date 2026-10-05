# README Rendering on GitHub

What actually renders in committed `.md` files (README, wikis, docs), where each feature shows up, and which suggested features GitHub does **not** support.

## The pipeline you must not forget

Something that works in an issue comment may be dead in a committed file. Key differences:

- **Autolinked references are NOT created in repository files or wikis.** `#123`, `GH-123`, and `owner/repo#123` only autolink in conversations (issues, PRs, discussions, comments). In a README, write the full URL.
- **Rich link/snippet previews render only in comments**, not in committed files.
- Full URLs, `@mentions`, emoji, footnotes, alerts, math, mermaid, tables, task-list checkboxes, `<details>`, `<kbd>`, `<sub>/<sup>`, and `<picture>` all render in `.md` files.
- **Rendered files truncate at 500 KiB.** Keep READMEs well under that.
- **Which README is shown:** GitHub picks `.github/README.md` → root `README.md` → `docs/README.md` (first found wins).

## Feature reference

| Feature | Syntax | Renders in | Audience fit | Notes |
|---|---|---|---|---|
| Alerts | `> [!NOTE]` | README, issues, PRs, discussions, wikis | All | GitHub-only extension; 1-2 per article, never consecutive |
| Mermaid diagrams | ` ```mermaid ` | README, issues, PRs, discussions, wikis; standalone `.mermaid`/`.mmd` | Team, OSS | Live SVG; ~v11.16.x bundled |
| Math / LaTeX | `$x$`, `$$x$$`, ` ```math ` | README, issues, PRs, discussions, wikis | OSS (technical) | MathJax |
| Footnotes | `text[^1]` … `[^1]: def` | README, issues, PRs, discussions | OSS | Not in wikis |
| Collapsible sections | `<details><summary>` | Everywhere | All | Blank lines required |
| Task lists | `- [x]` / `- [ ]` | README/.md (read-only) | Team, OSS | Interactive only in issue bodies |
| Tables | `\| a \| b \|` | Everywhere | All | No row/colspan; no block elements |
| Keycaps | `<kbd>Ctrl</kbd>` | Everywhere | All | Don't overuse |
| Sub/superscript | `<sub>`, `<sup>` | Everywhere | All | Prefer MathJax for real math |
| Dark/light images | `<picture>` | `.md` on github.com | OSS | Highest ROI on logo/banner |
| Code fences | ` ```lang ` | Everywhere | All | Language id only (no title, no line highlights) |
| Relative / root links | `[x](docs/f.md)`, `[x](/docs/f.md)` | Everywhere | Team, OSS | Relative to current file |
| Heading anchors | `#section-name` | Everywhere | All | Auto-generated per heading |
| Hidden comments | `<!-- … -->` | Raw source / clones | All | Never put secrets here |
| Emoji | `:shipit:` | Everywhere | All | Unknown shortcodes render literally |
| Mentions | `@user`, `@org/team` | Everywhere | - | Link but do **not** notify in README |
| Images / embeds | `![alt](path)` | Everywhere | All | Drag-drop uploads get `user-attachments` URLs |

## Details by feature

### Alerts
Five types only: `[!NOTE]`, `[!TIP]`, `[!IMPORTANT]`, `[!WARNING]`, `[!CAUTION]`. Use uppercase. Works in README, issues, PRs, discussions, wikis. It is a **GitHub-only extension**: other renderers show the literal `[!NOTE]` text. Use 1-2 per article and never consecutively. Cannot nest inside lists, blockquotes, or `<details>`; an extra blank line ends the alert.

### Mermaid
Fenced ` ```mermaid ` block renders a live SVG via GitHub's Viewscreen; also works as a standalone `.mermaid`/`.mmd` file. GitHub bundles Mermaid ~v11.16.x (verify with ` ```mermaid info `). Supported types include: flowchart/graph, sequenceDiagram, classDiagram, stateDiagram-v2, erDiagram, gantt, pie, gitGraph, journey, mindmap, timeline, quadrantChart, requirementDiagram, C4*, sankey-beta, xychart-beta, block-beta, packet-beta, architecture-beta, kanban, radar-beta, treemap. `%%{init}%%` theme overrides are stripped; GitHub uses its own light/dark theme. Not screen-reader accessible; no markdown lists inside nodes.

### Math / LaTeX
Inline `$...$`, inline backtick form `` $`\sqrt{x}` ``, block `$$...$$`, and the ` ```math ` fence. Rendered by MathJax in README, issues, PRs, discussions, wikis. Escape a literal `$` as `\$` or `<span>$</span>`. A block after text needs a line break. Prefer the ` ```math ` fence to avoid `$` collisions with prose.

### Footnotes
`text[^1]` with a definition `[^1]: definition`. Renders at the bottom with back-links. Works in README, issues, PRs, and discussions, but **not** in wikis. Multi-line definitions need two trailing spaces on continued lines.

### Collapsible sections
```html
<details><summary>Title</summary>

Markdown content here

</details>
```
Works everywhere. Blank lines around the content are required. Nesting is fiddly. Content still appears in the raw source (no true hiding).

### Task lists
`- [x]` done, `- [ ]` todo. In README/`.md` files the checkboxes are **read-only** (view only). Interactive tracking and progress bars exist only in issue bodies. Tasklist blocks are retired; use sub-issues. A list item beginning with `(` must be escaped as `\(`.

### Tables
GFM pipe tables. Alignment via `:---`, `:---:`, `---:`. Renders everywhere. No `rowspan`/`colspan`. No block elements inside cells; use `<br>`. Header row is mandatory. Escape literal pipes as `\|`.

### Code fences
Use only a language identifier after the fence for syntax highlighting. GitHub does **not** support fence `title="…"` (a Docusaurus/Pandoc/mkdocs feature) or line highlighting `{2,4-6}` (a GitLab/Prism/mkdocs feature); both are unimplemented requests. For emphasis, use the `diff` fence with leading `+`/`-` for green/red lines, and put filenames in `**bold**` or a `## heading` above.

### Links, anchors, permalinks
- `[x](docs/f.md)`: relative to the current file.
- `[x](/docs/f.md)`: root-relative.
- `#section`: auto heading anchors, lowercase, spaces → `-`, punctuation stripped, duplicates get `-1`.
- Line links: `.../blob/COMMIT/path#L10` and ranges `#L10-L20`. Markdown files need `?plain=1`.
- Press `y` on a file view to get a commit permalink.
- Link text must be on one line.
- Custom `<a name>` anchors are excluded from the auto-generated TOC.

### Hidden HTML comments
`<!-- … -->` is invisible when rendered but visible in raw source and clones. Never put secrets in them. No `--` inside the comment body.

### Emoji
`:shipit:` style shortcodes. Unknown shortcodes render literally. GitHub custom emoji don't exist elsewhere. Avoid emoji in headings.

### Mentions and autolinks
`@user` and `@org/team` become links. `#N` / `GH-N` / `owner/repo#N` autolink **only in comments**, not in repo files. Commit SHAs shorten automatically. Mentions in a README link but do **not** notify; commit-message mentions stopped notifying on Dec 8, 2025. Never `@` maintainers in a README.

### Embeds and rendered files
- Images: `![alt](path)`.
- Video: drag-drop upload → `user-attachments` URL (MP4/MOV/WEBM, 10MB free / 100MB paid org, H.264). A committed `.mp4` will **not** inline-play.
- `.stl` → 3D viewer (no render above 10MB).
- `.geojson` / `.topojson` → interactive maps (CRS84 only, no render above 10MB, clusters around ~750 markers).
- `.ipynb` → static render; JS plots don't run (use nbviewer).
- PDF → rendered, but links inside are ignored; cannot be inlined in a README.
- CSV / TSV → interactive tables up to 512KB.

## NEVER suggest (GitHub does not support)

- **Fence `title="…"`**: not implemented (Docusaurus/Pandoc/mkdocs only).
- **Line highlighting `{2,4-6}`**: not implemented (GitLab/Prism/mkdocs only).
- **Autolinked references in repo files**: `#123` / `GH-123` / `owner/repo#N` do not autolink in READMEs or wikis.
- **Nested alerts** inside lists, blockquotes, or `<details>`.
- **Interactive task-list checkboxes or progress bars** in a README: only in issue bodies.
- **Footnotes in wikis.**
- **Block elements inside table cells**: use `<br>`.
- **`rowspan` / `colspan`** in tables.
- **Inlining a committed `.mp4`** to auto-play: upload via drag-drop instead.
- **Clickable links inside a rendered PDF** in the repo.
- **`@mentions` as notifications from a README.**

## Audience guidance

- **Personal projects:** a single GIF or screenshot is enough.
- **Team repos:** screenshots/recordings plus CSV tables where data helps.
- **OSS projects:** a hero GIF/video, screenshots, and domain-appropriate diagrams.
- Keep media small and prefer relative paths to files in an `assets/` directory.

## README prose

Write for the reader's repo, not the author's machine:

- No `this machine` / `my setup` framing.
- No history or migration notes: `modeled on`, `migrated from`, `previously`, `was …`.
- No personal or business context: internal hosts, employer or product systems, private paths.
- Terse over exhaustive; let code, tables, and diagrams carry the detail.

## Sources

- https://docs.github.com/en/get-started/writing-on-github/getting-started-with-writing-and-formatting-on-github/basic-writing-and-formatting-syntax
- https://docs.github.com/en/get-started/writing-on-github/working-with-advanced-formatting/creating-diagrams
- https://docs.github.com/en/get-started/writing-on-github/working-with-advanced-formatting/writing-mathematical-expressions
- https://docs.github.com/en/get-started/writing-on-github/working-with-advanced-formatting/creating-and-highlighting-code-blocks
- https://docs.github.com/en/get-started/writing-on-github/working-with-advanced-formatting/autolinked-references-and-urls
- https://docs.github.com/en/get-started/writing-on-github/working-with-advanced-formatting/about-tasklists
- https://docs.github.com/en/get-started/writing-on-github/working-with-advanced-formatting/attaching-files
- https://docs.github.com/en/repositories/working-with-files/using-files/working-with-non-code-files
- https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/about-readmes
- https://docs.github.com/en/get-started/writing-on-github/working-with-advanced-formatting/creating-a-permanent-link-to-a-code-snippet
- https://github.blog/developer-skills/github/how-to-make-your-images-in-markdown-on-github-adjust-for-dark-mode-and-light-mode/
- https://github.blog/developer-skills/github/include-diagrams-markdown-files-mermaid/
- https://github.com/orgs/community/discussions/42489
- https://github.com/orgs/community/discussions/77414
- https://mermaid.js.org/
