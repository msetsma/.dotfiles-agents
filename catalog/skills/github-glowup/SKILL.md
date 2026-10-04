---
name: github-glowup
description: "Audit a GitHub repository and improve its quality-of-life extras: README presentation (GFM alerts, Mermaid, collapsibles, TOC, footnotes, relative/permalinks, dark-mode images), badges, .github/ health files and automation, repo settings/topics, and commit signing. Tailors advice to solo, team, or large-OSS repos. Use when asked to polish or glow up a repo or README, add badges, or improve a GitHub project's discoverability."
---

# github-glowup

Audit a repo that lives on GitHub and raise its quality-of-life: better README
presentation, relevant badges, health files and automation, repo settings, and
commit signing. **Tailor everything to the repo's audience** — a solo personal
repo and a large multi-user library need different things.

## Contract

1. **Suggest first.** Produce a grouped report; change nothing yet.
2. **Ask, then apply.** Use the agent's structured question / multi-select tool
   so the user chooses what to apply. Apply only what they pick.
3. **Never commit or push** unless the user explicitly asks.
4. **Bail on non-GitHub remotes** (GitLab/Bitbucket/etc.) with a short note.

## Hard rules

- **Never recommend features GitHub does not render.** No fence `title="…"`, no
  line highlighting `{2,4}`, no `#123` autolinks inside README files, no
  interactive task lists in READMEs, no footnotes in wikis. See
  `references/readme-rendering.md`.
- **Detect the audience first** (solo / small-team / large-OSS) and gate every
  suggestion through it. State the assumption and let the user override.
- **Separate "files I can add" from "settings you click."** Social preview,
  topics, description, Discussions, Pages, rulesets, vigilant mode, and
  uploading a signing key are UI-only — present them as click-paths, not edits.
- **`gh` is optional — and must be account-checked.** Before trusting any `gh`
  output, confirm the active account matches the remote owner (or can at least
  see the repo). A `gh` session authenticated as a *different* account — e.g. a
  work login against a personal repo — 404s on private repos and silently returns
  wrong or empty data. On a mismatch, fall back to local-only inspection, flag it
  in the report, and suggest `gh auth switch` (or `GH_TOKEN`) as the fix.
- Prefer **dynamic, zero-maintenance** badges; flag stale or broken ones.

## Modes

- **quick-wins** — high impact, low effort only: description + topics, README
  presentation fixes, 3–6 badges, LICENSE, `.gitignore`, and (if public)
  `SECURITY.md`. Sensible default for a personal repo.
- **full-audit** — everything, gated by audience: health files, issue/PR
  templates, Dependabot, CI/release workflows, rulesets, Pages, signing.

## Workflow

**0. Detect.** Is it a git repo with a GitHub remote? Parse `owner/repo`; note
public/private. Then test whether `gh` is usable **for this repo** — the active
account must match the remote owner (or be able to see the repo):

```sh
git remote get-url origin                 # git@github.com:OWNER/REPO.git
gh api user --jq .login                   # the active gh account
gh repo view --json name,owner            # errors if the active account can't see it
```

If the login differs from `OWNER` or `gh repo view` errors, treat `gh` as
unusable: stay local-only, note the mismatch in the report, and suggest
`gh auth switch`.

**1. Inventory** (offline first; enrich with `gh` when the account can see it):

```sh
git remote get-url origin
ls .github .github/workflows 2>/dev/null
# with gh:
gh repo view --json name,owner,description,homepageUrl,repositoryTopics,licenseInfo,isPrivate,stargazerCount,isTemplate
gh api repos/{owner}/{repo}/community/profile
gh api repos/{owner}/{repo}/rulesets
gh api repos/{owner}/{repo}/releases
```

**2. Classify the audience** — solo / small-team / large-OSS. Signals: private vs
public, stars and contributors, LICENSE, package manifests and lockfiles, README
depth, whether issues/PRs are used. See `references/audience-and-checklist.md`.

**3. Build the report** — grouped and audience-gated, each item = *what / why /
exact snippet or click-path / target file / effort*, marking ✅ present and ⚠️
broken. Template in `references/report-format.md`.

**4. Ask** — present the report, then a structured multi-select of what to apply,
with a "quick wins" preset. Split file-doable vs UI-only.

**5. Apply** only the selected file-based items; validate relative links (and
`bin/tests` if the repo has it). Report the UI-only items as a checklist.

**6. Confirm** — summarize what changed and what is left for the user to click.

## References

| File | Use for |
|---|---|
| `references/readme-rendering.md` | GFM features, syntax, audience fit, and what GitHub does NOT render |
| `references/badges.md` | shields.io + native Actions badges, templates, dark mode, gotchas |
| `references/health-and-automation.md` | `.github/` files, templates, Dependabot, workflows, releases, rulesets |
| `references/repo-settings.md` | UI-only + config settings: topics, description, social preview, Pages, linguist… |
| `references/signing.md` | SSH/GPG signing → the green "Verified" |
| `references/audience-and-checklist.md` | community-profile items + the solo/team/OSS tailoring matrix |
| `references/report-format.md` | output template + the selection question set |

Read only the references you need.
