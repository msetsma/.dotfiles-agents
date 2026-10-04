# Repository Settings Reference

Reference for auditing and improving GitHub repo metadata, features, and config files.

## Summary

| Feature | Mechanism | How to set | Audience fit |
|---|---|---|---|
| Topics | UI + API | Repo page → gear by About → Topics; `gh repo edit --add-topic <t>` | All, esp. OSS |
| Description + homepage | UI + API | About dialog; `gh repo edit --description "..." --homepage "https://..."` | All |
| Social preview | UI-only | Settings → Social preview → Edit → upload | OSS / shared links |
| Discussions | UI toggle + GraphQL | Settings → Features → Set up discussions | Team + large OSS |
| Wiki | UI toggle, separate git repo | Settings → Features → Wikis | Large / docs-heavy |
| GitHub Pages | File/branch/Actions | Settings → Pages | All scales |
| Sponsors / FUNDING | File + UI | `.github/FUNDING.yml` or Settings → General → Features → Sponsorships | OSS maintainers |
| Pinned repos | UI-only | Profile → Customize your pins | Profiles |
| Profile README | File `username/username` | Public repo named exactly your username with `README.md` | Individuals |
| Template repo | UI-only | Settings → Template repository; `is_template:true` | Team / boilerplate |
| Releases + tags | UI + git + `.github/release.yml` | `git tag`, `gh release create`, UI Draft | All |
| Branch protection / rulesets | UI + API | Settings → Rules → Rulesets vs Settings → Branches | Small team → OSS |
| Merge/squash/rebase settings | UI + API | Settings → General → Pull Requests | Team + OSS |
| `.gitattributes` Linguist overrides | File | Root `.gitattributes` | Vendored / generated / docs repos |
| `.gitignore` | File | Root `.gitignore` | All |
| License | File + UI | `LICENSE` at root; choosealicense.com | All public |

## Per-item detail

### 1. Topics
Discoverability via [github.com/topics](https://github.com/topics) and the `topic:` search qualifier. Set at Repo page → gear next to About → **Topics**. Rules: lowercase letters, numbers, hyphens; ≤50 chars each; **max 20** topics. Topics are always public, even when added from a private repo. CLI: `gh repo edit --add-topic <t>`. REST: `PUT`/`DELETE /repos/{owner}/{repo}/topics`.

### 2. Description + homepage
Either field drives search and About display. Set in the About dialog. CLI: `gh repo edit --description "..." --homepage "https://..."`. REST: `PATCH /repos/{owner}/{repo}`. Description capped at ~350 characters.

### 3. Social preview
The image shown when the repo link is shared. Settings → **Social preview** → Edit → upload. Formats PNG/JPG/GIF, **<1MB**, at least **640×320**, best at **1280×640**. Transparency works but a solid background is safer. Not settable via the REST API. Public sharing requires a public repo.

### 4. Discussions
A threaded forum attached to the repo. Settings → Features → **Set up discussions**. Managed through GraphQL; `gh` support is limited. Best for teams and large OSS projects.

### 5. Wiki
Settings → Features → **Wikis**. Backed by a separate git repo at `<repo>.wiki.git`; has a public-editing toggle. Soft limit of **5000 files**. Wiki pages are search-indexed only if the repo has **500+ stars AND public editing is disabled** — prefer GitHub Pages for SEO.

### 6. GitHub Pages
Settings → **Pages**. Source can be a branch + folder (`/` or `/docs`) or a GitHub Actions workflow (`upload-pages-artifact` + `deploy-pages`, using the `github-pages` environment). URL: `<owner>.github.io` for user/org sites, `<owner>.github.io/<repo>` for project sites. `CNAME` is configured in settings (not by a file alone); add `.nojekyll` to bypass Jekyll. Pages on private repos needs a paid plan; making a repo private can unpublish the site — remove DNS records to avoid domain takeover.

### 7. Sponsors / FUNDING
Add `.github/FUNDING.yml` on the default branch, or set via Settings → General → Features → **Sponsorships**. Lists platforms plus a `custom` entry (≤4). Quote URLs containing `:`. No fee on personal accounts.

### 8. Pinned repos
Profile → **Customize your pins**. Up to **6** items (repos and gists).

### 9. Profile README
Create a **public** repo named exactly your username containing `README.md`; it auto-displays on your profile. Repos created before July 2020 must use **Share to profile**.

### 10. Template repositories
Settings → **Template repository**; API field `is_template:true`. Clones start with unrelated histories. Cannot include Git LFS files.

### 11. Releases + tags
Create via `git tag`, `gh release create`, or the UI Draft flow. Up to **1000 assets**, each **<2GiB**. `.github/release.yml` customizes automatically generated release notes.

### 12. Branch protection / rulesets
**Rulesets:** Settings → Rules → **Rulesets** (≤75 per repo; org-wide rules on Team/Enterprise; `fnmatch` patterns; layer with each other and with classic rules; bypass lists; push rules for private/internal across the fork network). **Classic branch protection:** Settings → Branches (one branch at a time). Users with read access can view active rulesets. Protections on private repos need a paid plan; public repos are free.

### 13. Merge / squash / rebase settings
Settings → General → Pull Requests toggles: allow merge, allow squash, allow rebase, allow auto-merge, auto-delete head branches. REST: `allow_merge_commit`, `allow_squash_merge`, `allow_rebase_merge`, `allow_auto_merge`, `delete_branch_on_merge`. Note: **rebase-and-merge rewrites commits and does NOT sign them**.

### 14. `.gitattributes` Linguist overrides
Fix language stats and the primary-language badge. Examples:

```
*.rb linguist-language=Java
special-vendored-path/* linguist-vendored
project-docs/* linguist-documentation
Api.elm linguist-generated
jquery.js -linguist-vendored
*.kicad_pcb linguist-detectable
```

Only committed attributes take effect.

### 15. `.gitignore`
Root `.gitignore`. Bootstrap from [github/gitignore](https://github.com/github/gitignore) or gitignore.io. Use `git rm --cached` for already-tracked files. Global ignore: `~/.config/git/ignore`; per-repo local: `.git/info/exclude`.

### 16. License
Pick one at [choosealicense.com](https://choosealicense.com). Place `LICENSE` (or `.txt`/`.md`/`.rst`) at the repo root; detected by Licensee. Enables the `license:mit` search filter. **No license = default copyright**: others may only view and fork.

## Cross-cutting: fast API / CLI verification

- `GET /repos/{owner}/{repo}` returns `topics`, `description`, `homepage`, `license`, `is_template`, `allow_*_merge`, `delete_branch_on_merge`.
- Topics endpoint: `GET/PUT/DELETE /repos/{owner}/{repo}/topics`.
- `GET /repos/{owner}/{repo}/rulesets`.
- `GET /repos/{owner}/{repo}/releases`.
- **Social preview** and **pinned repos** have **no reliable REST field** → check manually.
- **Account match:** every read above runs as the *active* `gh` account only. Confirm `gh api user --jq .login` matches the remote `{owner}` (and that `gh repo view` resolves the repo) *before* trusting any output; a mismatched account 404s on private repos. Fall back to local inspection and suggest `gh auth switch`.

File-presence checks: `.github/FUNDING.yml`, `.github/release.yml`, `.gitattributes`, `.gitignore`, `LICENSE*`, `README.md`, `CNAME`/`.nojekyll`, `<owner>.github.io`, `username/username`, `docs/` or the wiki remote.

Highest-impact, low-effort: accurate description + topics, a LICENSE, a correct `.gitignore`, social preview + release notes for OSS; rulesets + one enforced merge method for team/OSS.

Scale gating: private Pages and private protections need paid plans; push rulesets are private/internal only; wiki indexing needs 500+ stars with public editing off.

## Sources

- https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/classifying-your-repository-with-topics
- https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/customizing-your-repositorys-social-media-preview
- https://docs.github.com/en/discussions/quickstart
- https://docs.github.com/en/communities/documenting-your-project-with-wikis/about-wikis
- https://docs.github.com/en/pages/getting-started-with-github-pages/what-is-github-pages
- https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/displaying-a-sponsor-button-in-your-repository
- https://docs.github.com/en/account-and-profile/setting-up-and-managing-your-github-profile/customizing-your-profile/pinning-items-to-your-profile
- https://docs.github.com/en/account-and-profile/setting-up-and-managing-your-github-profile/customizing-your-profile/managing-your-profile-readme
- https://docs.github.com/en/repositories/creating-and-managing-repositories/creating-a-template-repository
- https://docs.github.com/en/repositories/releasing-projects-on-github/about-releases
- https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches
- https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/about-rulesets
- https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/configuring-pull-request-merges/about-merge-methods-on-github
- https://github.com/github-linguist/linguist/blob/main/docs/overrides.md
- https://docs.github.com/en/get-started/getting-started-with-git/ignoring-files
- https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/licensing-a-repository
