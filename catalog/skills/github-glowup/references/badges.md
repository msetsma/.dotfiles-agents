# Repo Badges

Status/quality badges for a GitHub repo: which to use, how to template them, and how to keep them from lying.

Base host: `https://img.shields.io` (append `.svg` for the SVG variant). GitHub badge templates use `:user`/`:repo`; substitute `OWNER`/`REPO` in prose routes below.

## Badge types at a glance

| Type | Maintenance | Source | Notes |
|---|---|---|---|
| shields `github/*` | Dynamic / zero-maintenance | shields.io | Reads GitHub API; shared token pool |
| Native Actions badge | Dynamic / zero-maintenance | GitHub | Served by GitHub, no third party |
| Third-party (npm / PyPI / crates / Codecov) | Dynamic / zero-maintenance | shields.io | Same token pool for shields routes |
| Static `badge/:label-:message-:color` | Manual | - | Hard-coded; rots silently |
| Hard-coded "build passing" / version / coverage % | Manual | - | Replace with a dynamic equivalent |

Static badges that duplicate dynamic data (a hand-written version next to a release badge, a fixed "build passing" next to CI) are a smell; pick one source of truth.

## Dynamic GitHub badges (shields.io)

All zero-maintenance. Templates below are copy-paste; add `.svg` for SVG.

| Badge | Template | Notes |
|---|---|---|
| Last commit | `/github/last-commit/:user/:repo` | `?path=`, `?display_timestamp=`; branch variant `/…/:branch` |
| Commit activity | `/github/commit-activity/:interval/:user/:repo` | `interval` = `w` \| `m` \| `y` \| `t` |
| Contributors | `/github/contributors/:user/:repo` | also `contributors-anon` |
| Issues | `/github/issues/:user/:repo` | variants `issues-raw`, `issues-closed`, `issues-closed-raw`; by label `/…/:label` |
| Pull requests | `/github/issues-pr/:user/:repo` | variants; `?excludeDrafts=`, `?onlyDrafts=` |
| Stars | `/github/stars/:user/:repo` | default style `social` |
| Forks | `/github/forks/:user/:repo` | default style `social` |
| Watchers | `/github/watchers/:user/:repo` | default style `social` |
| License | `/github/license/:user/:repo` | |
| Repo size | `/github/repo-size/:user/:repo` | |
| Code size | `/github/languages/code-size/:user/:repo` | |
| Top language | `/github/languages/top/:user/:repo` | |
| Language count | `/github/languages/count/:user/:repo` | |
| Release | `/github/v/release/:user/:repo` | `?include_prereleases`, `?sort=semver`, `?display_name=tag`, `?filter=` |
| Tag | `/github/v/tag/:user/:repo` | |
| Release date | `/github/release-date/:user/:repo` | |
| Created at | `/github/created-at/:user/:repo` | |
| Downloads | `/github/downloads/:user/:repo` | `/…/latest`, `/…/:tag`, and `downloads-asset` variants |
| Legacy download routes | `/github/assets-dl/…`, `/github/dt/…` | prefer the `downloads` routes above |

### Copy-paste examples

Owner `badges`, repo `shields`:

```markdown
![Last commit](https://img.shields.io/github/last-commit/badges/shields.svg)
![Commit activity](https://img.shields.io/github/commit-activity/m/badges/shields.svg)
![Contributors](https://img.shields.io/github/contributors/badges/shields.svg)
![Issues](https://img.shields.io/github/issues/badges/shields.svg)
![Pull requests](https://img.shields.io/github/issues-pr/badges/shields.svg)
![Stars](https://img.shields.io/github/stars/badges/shields.svg)
![Forks](https://img.shields.io/github/forks/badges/shields.svg)
![License](https://img.shields.io/github/license/badges/shields.svg)
![Repo size](https://img.shields.io/github/repo-size/badges/shields.svg)
![Top language](https://img.shields.io/github/languages/top/badges/shields.svg)
![Code size](https://img.shields.io/github/languages/code-size/badges/shields.svg)
![Release](https://img.shields.io/github/v/release/badges/shields.svg)
![Downloads](https://img.shields.io/github/downloads/badges/shields.svg)
```

## GitHub Actions status badge

Native (zero-maintenance, served by GitHub):

```
https://github.com/OWNER/REPO/actions/workflows/WORKFLOW-FILE/badge.svg
```

- `WORKFLOW-FILE` is the **filename** (e.g. `main.yml`), **not** the `name:` value.
- Optional query params: `?branch=`, `?event=push`.
- UI: Actions → pick workflow → `⋯` → **Create status badge**.
- Private repos: the badge is **not accessible externally**.

shields wrapper (optional styling, same data):

```
https://img.shields.io/github/actions/workflow/status/:user/:repo/:workflow
```

- `:workflow` is the filename, **case-sensitive**, include any subdirectory.
- `?branch=`, `?event=`.
- The old `/github/workflow/status/…` (by workflow name) is **deprecated; don't use it**.

## Static & custom badges

```
https://img.shields.io/badge/:label-:message-:color
https://img.shields.io/badge/:message-:color
```

Manual, but useful for things with no queryable source (docs site, Discord, status page). Encoding:

| Input | Renders as |
|---|---|
| `_` or `%20` | space |
| `__` | literal underscore |
| `--` | literal dash |
| `%25` | `%` |

Params:

| Param | Purpose |
|---|---|
| `style` | `flat` (default), `flat-square`, `plastic`, `for-the-badge`, `social` |
| `logo` | Simple Icons slug |
| `logoColor` | logo color |
| `logoSize` | logo size |
| `label` | override left text |
| `labelColor` | left-side color |
| `color` | right-side color |
| `cacheSeconds` | cache TTL (cannot go below the default) |
| `link` | **only works inside `<object>`**, not `<img>` |

`for-the-badge` renders **ALL CAPS** and is deliberately wide.

## Dark mode

shields badges do **not** auto-adapt and there is no shields theme param. Practical rules:

- Prefer mid-tone or brand colors that read on both light and dark backgrounds.
- Add `logoColor=white` for near-black logos.
- For a badge that must differ per theme, use `<picture>` with `prefers-color-scheme` (modern approach).
- The `#gh-dark-mode-only` / `#gh-light-mode-only` URL fragments are **deprecated**; avoid.

## Accessibility & layout

- shields SVGs carry `role="img"` + `aria-label`, but still write meaningful **alt** text: what the badge means; describe the destination if it is linked.
- One row of **3-6** badges near the top.
- Group in this order: build/CI + coverage → version/license → community.
- Keep **one style** across the row.
- **10+** badges is noise.
- Link each badge to its page.
- shields `link=` only works in `<object>`, so use the markdown link form:

```markdown
[![alt text](https://img.shields.io/...)](https://target)
```

## Minimal recommended row

CI (native, linked to Actions), release, license, last commit, issues, stars; works for solo, team, and OSS repos (trim for solo, this set for OSS):

```markdown
[![CI](https://github.com/OWNER/REPO/actions/workflows/main.yml/badge.svg?branch=main)](https://github.com/OWNER/REPO/actions/workflows/main.yml)
[![Release](https://img.shields.io/github/v/release/OWNER/REPO.svg)](https://github.com/OWNER/REPO/releases)
[![License](https://img.shields.io/github/license/OWNER/REPO.svg)](https://github.com/OWNER/REPO/blob/main/LICENSE)
[![Last commit](https://img.shields.io/github/last-commit/OWNER/REPO.svg)](https://github.com/OWNER/REPO/commits)
[![Issues](https://img.shields.io/github/issues/OWNER/REPO.svg)](https://github.com/OWNER/REPO/issues)
[![Stars](https://img.shields.io/github/stars/OWNER/REPO.svg)](https://github.com/OWNER/REPO/stargazers)
```

## Gotchas

- **Token pool**: shields shares a pool of GitHub tokens; heavy/rate-limited usage shows error badges; raise limits via [`/github-auth`](https://shields.io/docs).
- **Caching**: HTTP caching applies; `cacheSeconds` cannot go below the default.
- **Error-state badges**: an inaccessible/invalid/not-found result renders an error badge, and a native badge returns a 404 SVG; both read as broken to visitors.
- **Workflow filename**: case-sensitive; include the subdirectory if the file isn't at `.github/workflows/` root.
- **Branch in URL**: URL-encode spaces and `/` (e.g. `feature%2Fx`, or pass `?branch=feature/x`).
- **No `?branch=`**: the badge can show a misleading green from *any* branch; set the branch explicitly.
- **Private repos**: not externally embeddable.
- **Third party**: shields.io is third-party and can be down; the native Actions badge is served by GitHub.

## Sources

- https://shields.io/badges
- https://shields.io/docs
- https://shields.io/docs/logos
- https://shields.io/blog/token-pool
- https://docs.github.com/en/actions/monitoring-and-troubleshooting-workflows/adding-a-workflow-status-badge
- https://github.com/badges/shields/issues/8671
