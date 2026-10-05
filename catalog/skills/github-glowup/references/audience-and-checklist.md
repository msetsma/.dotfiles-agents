# Audience & Checklist Reference

Audience-gating backbone for `github-glowup`: what to suggest depends on who the repo is for, so detect the audience first, then apply only the checks that fit.

## 1. Community Profile checklist

GitHub's **Community Standards** page (Insights → Community Standards) works on **public repos only**; forks are excluded. The REST endpoint `GET /repos/{owner}/{repo}/community/profile` returns a `health_percentage` plus booleans/objects for `description`, `documentation`, and `files.{code_of_conduct, contributing, issue_template, pull_request_template, license, readme}`.

Scored items and what actually counts:

- **Description**: non-empty repository description.
- **README**: a recognized README, resolved in order: `.github/` → root → `docs/`.
- **Code of conduct**: `CODE_OF_CONDUCT.md` in root, `docs/`, or `.github/`; may be supplied by an account default.
- **Contributing**: `CONTRIBUTING.md` in root, `docs/`, or `.github/`.
- **License**: `LICENSE` / `LICENSE.txt` / `LICENSE.md` / `LICENSE.rst` at root, matched by Licensee to an SPDX ID. **Cannot** be supplied by an account default.
- **Security policy**: `SECURITY.md` in root, `docs/`, or `.github/`.
- **Issue templates**: `.github/ISSUE_TEMPLATE/` with a valid `name` + `about` (`.md`) or `name` + `description` (`.yml`). Files elsewhere, or missing those keys, do not tick.
- **Pull request template**: a PR template present on the default branch.

**Multi-location precedence:** `.github/` → root → `docs/`.

**Account-default repo (`.github`)** can supply: `ACCESSIBILITY`, `CODE_OF_CONDUCT`, `CONTRIBUTING`, discussion forms, `FUNDING`, issue + PR templates, `config.yml`, `SECURITY`, `VULNERABILITY_REPORT`, `SUPPORT`. It **cannot** supply: license, `CODEOWNERS`, `GOVERNANCE`, `CITATION`.

The checklist is **minimum hygiene for public repos**. It measures **presence only**, never README quality.

## 2. Tailoring matrix

Legend: ● required, ◐ recommended, ○ optional/nice, ✗ overkill.

### README sections

| Section | Solo | Small team | Large OSS |
|---|---|---|---|
| Title + one-line | ● | ● | ● |
| Long description / why | ○ | ◐ | ● |
| Badges | ○ skill/meta only | ◐ CI+version | ● CI/coverage/version/downloads/license |
| Screenshots / GIF | ○ | ◐ UI/tools | ● user-facing |
| TOC | ✗ | ○ | ● long README |
| Features list | ✗ | ◐ | ● |
| Requirements | ◐ | ● | ● |
| Install | ◐ | ● | ● |
| Usage / quickstart | ◐ | ● | ● |
| API reference | ✗ link docs | ◐ | ● or link generated |
| Configuration / env | ✗ | ● | ● |
| Examples | ✗ | ◐ | ● |
| Roadmap | ✗ | ○ | ◐ |
| Project status / maintenance notice | ◐ abandoned scripts | ◐ | ◐ unmaintained |
| Support | ✗ | ◐ | ● |
| Contributing | ✗ / one line | ◐ internal norms | ● link CONTRIBUTING |
| Code of Conduct link | ✗ | ○ | ● |
| Authors / acknowledgements | ✗ | ○ | ◐ |
| Citation | ✗ | ✗ | ○ research |
| Changelog link | ○ | ◐ | ● |
| License section | ○ if public | ◐ | ● |
| Versioning (SemVer) | ✗ | ○ | ● |

### Badges

| Badge | Solo | Small team | Large OSS |
|---|---|---|---|
| CI | ○ | ● | ● |
| Coverage | ✗ | ◐ | ● |
| Version / release | ✗ | ◐ | ● |
| Downloads | ✗ | ✗ | ● |
| License | ○ | ◐ | ● |
| Docs site | ✗ | ○ | ● |
| Chat | ✗ | ○ | ◐ |
| Funding | ○ | ✗ | ◐ if funded |
| OpenSSF | ✗ | ✗ | ○ |
| Badge walls | ✗ | ✗ | ✗ |

### `.github` files

| File | Solo | Small team | Large OSS |
|---|---|---|---|
| README | ● | ● | ● |
| Issue templates | ✗ | ◐ bug form if tracker used | ● bug + feature + `config.yml` |
| PR template | ✗ | ◐ | ● |
| CONTRIBUTING | ✗ | ◐ | ● |
| CODE_OF_CONDUCT | ✗ | ○ org-wide default | ● Contributor Covenant |
| LICENSE per-repo | ○ public | ◐ | ● |
| SECURITY.md | ✗ | ◐ | ● |
| CHANGELOG | ○ | ◐ | ● |
| FUNDING | ○ | ✗ | ◐ |
| SUPPORT | ✗ | ◐ | ● |
| CITATION.cff | ✗ | ✗ | ○ |
| CODEOWNERS | ✗ | ● review routing | ● |
| GOVERNANCE | ✗ | ✗ | ○ |
| ARCHITECTURE | ○ | ◐ | ◐ |
| Account-default `.github` repo | ✗ | ● org defaults | ● |

### Settings

| Setting | Solo | Small team | Large OSS |
|---|---|---|---|
| Description | ● | ● | ● |
| Topics | ◐ | ◐ | ● |
| Website | ○ | ◐ | ● |
| Social preview | ✗ | ○ | ◐ |
| Discussions | ✗ | ○ | ◐ |
| Wiki | ✗ | ○ | ◐ or external site |
| Branch protection / required reviews | ✗ | ● | ● |
| Private vuln reporting | ✗ | ◐ | ● |
| Sponsors | ○ | ✗ | ◐ |
| Releases + tags | ○ | ◐ | ● |
| `main` branch | ◐ | ● | ● |
| Required status checks | ✗ | ● | ● |

## 3. Overkill / do not add

- **Solo**: CODE_OF_CONDUCT, CONTRIBUTING, issue/PR templates, GOVERNANCE, CODEOWNERS, coverage/downloads badges, a dedicated docs site. A single good README plus a license (if public) is the whole job.
- **Small team**: sponsor/funding badges, public "community" ceremony (CoC usually lives org-wide via the `.github` defaults repo), stars/contributor badge walls, CITATION.cff unless it is research software.
- **Large OSS**: badge walls; duplicated docs (README vs wiki vs site should cross-link, not repeat); stale Roadmap / Project-status. Keep the README a quickstart and push depth to docs/wiki.

## 4. README conventions

Per GitHub's "About READMEs", a README should answer: **what it does, why it's useful, how to start, where to get help, who maintains it**. GitHub auto-generates a TOC from headings and anchors, and truncates READMEs past **500 KiB**. ([About READMEs](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/about-readmes), [makeareadme.com](https://www.makeareadme.com/), [README-Driven Development](https://tom.preston-werner.com/2010/08/23/readme-driven-development.html))

**standard-readme spec** requires, in order: Title, Short Description, [TOC], Install, Usage, Contributing, License (must be last). Optional, in order: Banner, Badges, Long Description, Security, Background, Extra Sections, API, Maintainers, Thanks. Short description **< 120 chars**, no `>`, SPDX license + owner. ([spec](https://github.com/richardlitt/standard-readme/blob/main/spec.md))

**Common section set:** Title/Name, Description (short + long), Badges, Visuals/Screenshots/GIF, TOC, Features, Background/Motivation, Requirements, Install, Usage (smallest runnable example + expected output), API Reference, Configuration, Examples, Roadmap, Project status, Support, Contributing, Code of Conduct link, Authors/Acknowledgements, Citation, Changelog link, License.

**Badge taxonomy:** build/CI, coverage, version/release, downloads, license, language/platform, docs site, OpenSSF/scorecard, chat, funding, stars/contributors. See [Awesome README](https://github.com/matiassingers/awesome-readme).

**Changelog**: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) categories Added / Changed / Deprecated / Removed / Fixed / Security, an `Unreleased` section, ISO dates, SemVer link.

**Versioning**: [Semantic Versioning](https://semver.org/), `MAJOR.MINOR.PATCH`. Licensing help: [choosealicense.com](https://choosealicense.com/), [licensing a repository](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/licensing-a-repository).

## 5. Audit heuristics

1. **Detect the audience first** (public? contributors? downstream consumers?). This gates which checks apply.
2. **Community checklist ≠ README quality.** It scores presence only; validate location validity and issue-template frontmatter keys.
3. **Public repos:** missing LICENSE is **high** (unlicensed = not open source); README / description / SECURITY are **medium**.
4. **OSS libraries:** require install + usage + license + changelog + CONTRIBUTING + issue templates; recommend SemVer + Keep a Changelog.
5. **Solo:** do not nag for community files; focus on a working quickstart, a runnable example, and a license if public.
6. **Dead weight:** broken relative links, stale/failing badges, README > 500 KiB truncated, mixed-language READMEs without BCP-47 names.

## Sources

- https://docs.github.com/en/communities/setting-up-your-project-for-healthy-contributions/about-community-profiles-for-public-repositories
- https://docs.github.com/en/communities/setting-up-your-project-for-healthy-contributions/accessing-a-projects-community-profile
- https://docs.github.com/en/communities/setting-up-your-project-for-healthy-contributions/creating-a-default-community-health-file
- https://docs.github.com/en/communities/using-templates-to-encourage-useful-issues-and-pull-requests/about-issue-and-pull-request-templates
- https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/about-readmes
- https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/licensing-a-repository
- https://docs.github.com/en/rest/metrics/community
- https://opensource.guide/how-to-contribute/
- https://www.makeareadme.com/
- https://github.com/richardlitt/standard-readme/blob/main/spec.md
- https://github.com/matiassingers/awesome-readme
- https://tom.preston-werner.com/2010/08/23/readme-driven-development
- https://keepachangelog.com/en/1.1.0/
- https://semver.org/
- https://choosealicense.com/
