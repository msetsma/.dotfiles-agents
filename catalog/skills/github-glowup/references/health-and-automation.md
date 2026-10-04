# GitHub Health & Automation

Reference for auditing a repo's community-health files, security defaults, and automation. Each item lists what it is, how to add it (exact path or UI), what it unlocks, and who should bother.

## Tie-together: Community Profile

`https://github.com/<owner>/<repo>/community/profile` (public repos only) scores these: README, LICENSE, CODE_OF_CONDUCT, CONTRIBUTING, SECURITY, SUPPORT, issue templates, PR template, and description.

File precedence when a health file lives in more than one place: `.github/` → root → `docs/`.

- **Issue templates live only in `.github/ISSUE_TEMPLATE/`** (never root or `docs/`).
- **PR templates** may also be in `.github/`, root, or `docs/`.
- An account/org default `.github` repo can supply health files — but **not** LICENSE, CODEOWNERS, GOVERNANCE, or CITATION.

## A. Community health files

### 1. Issue templates

| | |
|---|---|
| What | Structured bug/feature intake. Legacy Markdown templates or YAML issue forms. |
| How | Legacy: `.github/ISSUE_TEMPLATE/bug_report.md` with frontmatter `name, about, title, labels, assignees`. Form: `.github/ISSUE_TEMPLATE/bug_report.yml` with keys `name, description, title, labels, assignees, type, projects, body`; body element types `markdown`, `textarea`, `input`, `dropdown`, `checkboxes`, `upload`; supports validations. Chooser: `.github/ISSUE_TEMPLATE/config.yml` (`blank_issues_enabled`, `contact_links`). |
| Unlocks | Ticks the Community Profile checklist — but only if the template has valid `name`/`about` (`.md`) or `name`/`description` (`.yml`). |
| Fit | small → large |

Ordering is alphanumeric; prefix filenames `01-`, `02-` to control order.

### 2. PR template

| | |
|---|---|
| What | Pre-filled description for new pull requests. |
| How | `.github/pull_request_template.md` (also root or `docs/`); multiple under `.github/PULL_REQUEST_TEMPLATE/<name>.md`, opened with `?template=<name>.md`. Must exist on the default branch. |
| Unlocks | Consistent PR descriptions; checklist drives contributors. |
| Fit | small → large |

### 3. SECURITY.md

| | |
|---|---|
| What | Vulnerability disclosure policy. |
| How | `.github/SECURITY.md`, root, or `docs/`. |
| Unlocks | Security tab and a pointer from the new-issue flow. |
| Fit | strongly recommended once public |

### 4. CODE_OF_CONDUCT.md

| | |
|---|---|
| What | Behavioral expectations and enforcement. |
| How | root, `docs/`, or `.github/`. Contributor Covenant is the common base. |
| Unlocks | Community Profile tick; sets norms. |
| Fit | small → large |

### 5. CONTRIBUTING.md

| | |
|---|---|
| What | How to contribute: setup, tests, style, PR expectations. |
| How | `.github/`, root, or `docs/`. |
| Unlocks | Linked automatically on PR and issue pages. |
| Fit | small → large |

### 6. SUPPORT.md

| | |
|---|---|
| What | Where to get help (discussions, chat, docs). |
| How | `.github/`, root, or `docs/`. |
| Unlocks | Linked on the new-issue page under "Helpful resources". |
| Fit | large |

### 7. GOVERNANCE.md

| | |
|---|---|
| What | Decision-making structure. **Conventional only — not a GitHub-recognized health file.** |
| How | root/`docs/`. |
| Unlocks | Nothing automated; documents process. |
| Fit | large projects only |

### 8. CODEOWNERS

| | |
|---|---|
| What | Maps paths/globs to users or teams for review ownership. |
| How | `.github/`, root, or `docs`. Syntax: `<glob> <@user-or-@org/team>`. Last matching line wins. |
| Unlocks | Automatic review requests and the "Require review from Code Owners" rule. |
| Fit | small → large |

Constraints: owners need write access; file must be `<3MB`; matching is case-sensitive; **no** gitignore-style negation. Protect the CODEOWNERS file itself with a branch rule.

### 9. FUNDING.yml

| | |
|---|---|
| What | Sponsor button config. |
| How | `.github/FUNDING.yml` or Settings → General → Features → Sponsorships. Platforms: `github, patreon, open_collective, ko_fi, tidelift, liberay, issuehunt, community_bridge, polar, buy_me_a_coffee, thanks_dev, custom` (max 4). |
| Unlocks | "Sponsor" button in the repo header. |
| Fit | optional |

### 10. CITATION.cff

| | |
|---|---|
| What | Machine-readable citation metadata (CFF format). |
| How | repo **ROOT only**. |
| Unlocks | "Cite this repository" button. |
| Fit | research / datasets |

### 11. Dependabot

| | |
|---|---|
| What | Dependency alerts, security updates, and version-bump PRs. |
| How | Alerts + security updates are repo/org settings (Security and quality tab). Version updates need `.github/dependabot.yml`. |
| Unlocks | Automated dependency hygiene. |
| Fit | any repo with manifests |

Minimal `.github/dependabot.yml`:

```yaml
version: 2
updates:
  - package-ecosystem: npm
    directory: "/"
    schedule:
      interval: weekly
```

- `schedule.interval`: `daily | weekly | monthly | quarterly | semiannually | yearly | cron`.
- Options: `allow`/`ignore`, `assignees`, `commit-message`, `cooldown`, `groups`, `labels`, `reviewers`, `open-pull-requests-limit`, `registries`.
- Use `directory` or `directories`.
- Ecosystems include `npm, pip, uv, github-actions, bundler, cargo, composer, docker, gomod, gradle, maven, nuget, pnpm, terraform`, and more.
- Dependabot **alerts for `github-actions` require semver tags** (not SHA-pinned actions).

### 12. Workflows (`.github/workflows/*.yml`)

| Workflow | What / How | Unlocks | Fit |
|---|---|---|---|
| CI | Build/test on push & PR. Badge feeds branch protection. | Required status checks. | all |
| Release | Trigger on tag push; `softprops/action-gh-release` or `gh release create`. | Published releases. | small → large |
| Stale | `actions/stale@v10`; `days-before-stale`, `days-before-close`, messages, exempt labels; permissions `issues:write`, `pull-requests:write`. | Auto-triage of inactive issues. | small → large |
| Labeler | `actions/labeler@v6` with `.github/labeler.yml`; use `pull_request_target` for forks. | Path-based auto-labels. | small → large |
| Auto-assign | `kentaro-m/auto-assign-action@v2` with `.github/auto_assign.yml`. | Automatic reviewer assignment. | small → large |

### 13. GitHub Pages

| | |
|---|---|
| What | Static site hosting. |
| How | Settings → Pages; publish from a branch folder or GitHub Actions (`actions/deploy-pages`). |
| Unlocks | User/org site at `<owner>.github.io`; project site at `<owner>.github.io/<repo>`. Supports `.nojekyll`, `CNAME`. |
| Fit | docs / demos |

Private-repo Pages requires a paid plan — and the **published site is still public**.

### 14. Releases + auto notes

| | |
|---|---|
| What | Tagged releases with generated notes. |
| How | Tags + Releases UI; customize via `.github/release.yml` (`changelog.exclude.labels`/`authors`; `changelog.categories[].title`/`labels`/`exclude`; `*` catch-all). |
| Unlocks | Categorized changelogs. |
| Fit | versioned releases |

### 15. Merge queue

| | |
|---|---|
| What | Serialized merge batch that runs CI on the combined result. |
| How | Branch protection/ruleset "Require merge queue". CI **must** add a `merge_group` trigger. |
| Unlocks | Fewer broken main builds under load. |
| Fit | high-volume repos |

Public repos only (private needs a paid plan). Merge queue **stalls if workflows lack `merge_group`**.

### 16. Rulesets & branch protection

| | |
|---|---|
| What | Enforced branch rules. Rulesets (modern) vs legacy branch protection. |
| How | Settings → Rules → Rulesets (≤75/repo, layerable, `fnmatch` targets, bypass lists, push rules) vs legacy Settings → Branches (one rule at a time). |
| Unlocks | Required PR reviews, code-owner review, dismiss stale reviews, required status checks, conversation resolution, signed commits, linear history, merge queue, deployments, lock branch, restrict push, allow force-push/delete. |
| Fit | small → large |

## Audit checklist

| Signal | Path / UI | Fit |
|---|---|---|
| README + description + topics | repo root; Settings | all |
| LICENSE | root | all public |
| SECURITY.md | `.github/` / root / `docs/` | public |
| Issue forms + config.yml | `.github/ISSUE_TEMPLATE/` | small → large |
| PR template | `.github/pull_request_template.md` | small → large |
| CONTRIBUTING | `.github/` / root / `docs/` | small → large |
| CODE_OF_CONDUCT | `.github/` / root / `docs/` | small → large |
| SUPPORT | `.github/` / root / `docs/` | large |
| GOVERNANCE | root / `docs/` | large |
| CODEOWNERS | `.github/` / root / `docs` | small → large |
| FUNDING | `.github/FUNDING.yml` | optional |
| CITATION.cff | repo root | research |
| dependabot.yml | `.github/dependabot.yml` | any with manifests |
| Dependabot alerts/updates | Security and quality tab | any public |
| Workflows | `.github/workflows/*.yml` | CI all; rest small → large |
| release.yml | `.github/release.yml` | versioned releases |
| Pages | Settings → Pages | docs/demos |
| Rulesets / branch protection | Settings → Rules/Branches | small → large |
| Merge queue | branch protection/ruleset | high-volume |

## Validation gotchas

- Issue templates missing `name`/`about` (`.md`) or `name`/`description` (`.yml`) **never tick** the checklist.
- CODEOWNERS with nonexistent or under-permissioned owners **silently fails**.
- CODEOWNERS over **3MB is ignored**.
- `required` field validations only work in **public** repos.
- `github-actions` Dependabot alerts need **semver tags**, not SHA pins.
- Merge queue **stalls** if workflows lack a `merge_group` trigger.
- Private-repo Pages is still a **public** site.

## Sources (consolidated)

- https://docs.github.com/en/communities/setting-up-your-project-for-healthy-contributions/about-community-profiles-for-public-repositories
- https://docs.github.com/en/communities/setting-up-your-project-for-healthy-contributions/creating-a-default-community-health-file
- https://docs.github.com/en/communities/using-templates-to-encourage-useful-issues-and-pull-requests/about-issue-and-pull-request-templates
- https://docs.github.com/en/communities/using-templates-to-encourage-useful-issues-and-pull-requests/configuring-issue-templates-for-your-repository
- https://docs.github.com/en/communities/using-templates-to-encourage-useful-issues-and-pull-requests/syntax-for-githubs-form-schema
- https://docs.github.com/en/communities/using-templates-to-encourage-useful-issues-and-pull-requests/creating-a-pull-request-template-for-your-repository
- https://docs.github.com/en/code-security/how-tos/report-and-fix-vulnerabilities/configure-vulnerability-reporting/add-security-policy
- https://docs.github.com/en/communities/setting-up-your-project-for-healthy-contributions/adding-a-code-of-conduct-to-your-project
- https://docs.github.com/en/communities/setting-up-your-project-for-healthy-contributions/setting-guidelines-for-repository-contributors
- https://docs.github.com/en/communities/setting-up-your-project-for-healthy-contributions/adding-support-resources-to-your-project
- https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/about-code-owners
- https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/displaying-a-sponsor-button-in-your-repository
- https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/about-citation-files
- https://docs.github.com/en/code-security/dependabot/dependabot-version-updates/configuration-options-for-the-dependabot.yml-file
- https://docs.github.com/en/code-security/dependabot/ecosystems-supported-by-dependabot/supported-ecosystems-and-repositories
- https://docs.github.com/en/code-security/dependabot/dependabot-alerts/about-dependabot-alerts
- https://docs.github.com/en/code-security/dependabot/dependabot-security-updates/about-dependabot-security-updates
- https://docs.github.com/en/actions/get-started/quickstart
- https://github.com/actions/starter-workflows
- https://docs.github.com/en/pages/getting-started-with-github-pages/about-github-pages
- https://docs.github.com/en/pages/getting-started-with-github-pages/configuring-a-publishing-source-for-your-github-pages-site
- https://docs.github.com/en/repositories/releasing-projects-on-github/automatically-generated-release-notes
- https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/configuring-pull-request-merges/managing-a-merge-queue
- https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/about-rulesets
- https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches
- https://github.com/actions/stale
- https://github.com/actions/labeler
- https://github.com/kentaro-m/auto-assign-action
