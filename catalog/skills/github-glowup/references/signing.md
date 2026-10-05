# Commit signature verification

How GitHub decides whether a commit is "Verified", and how to configure signing.

There is **no separate "signed" badge**: the signature state *is* the verification badge.

## Badge states

Default (vigilant mode **off**):

| Badge | Meaning |
|---|---|
| **Verified** | signed and verified |
| **Unverified** | signed but unverifiable |
| *(no status)* | unsigned |

With vigilant mode **on**:

| Badge | Meaning |
|---|---|
| **Verified** | signed and verified |
| **Partially verified** | signed and verified, but a *distinct* author has vigilant mode enabled |
| **Unverified** | unverifiable, **or** unsigned when the committer/author has vigilant mode on |

### How verification works

- Verification happens **at push time**. GitHub stores a persistent, **immutable** record of the result.
- The record survives key rotation, revocation, and expiry; old commits stay Verified.
- The record is reused across the repo network, so a commit pushed to one fork is recognized elsewhere.
- Admins can **require signed commits** on a protected branch.
- **Rebase-and-merge strips signatures** (GitHub can't sign on your behalf). Rebase locally and push instead.

## SSH signing (recommended)

Requires **Git ≥ 2.34**.

```sh
git config --global gpg.format ssh
# public key path when using ssh-agent
git config --global user.signingkey ~/.ssh/id_ed25519.pub
git config --global commit.gpgsign true
git config --global tag.gpgSign true
```

`user.signingkey` may also be a private-key path or an inline value: `key::ssh-ed25519 AAAA... you@host`. Raw `ssh-rsa` values are deprecated.

### Local trust store (optional)

Only needed for `git verify-commit` / `git verify-tag` and merge/pull checks. **Not needed for GitHub verification.**

```sh
echo "you@example.com $(cat ~/.ssh/id_ed25519.pub)" > ~/.config/git/allowed_signers
git config --global gpg.ssh.allowedSignersFile ~/.config/git/allowed_signers
```

OpenSSH ≥ 8.8 supports valid-after/valid-before, enabling key rotation.

## GPG

```sh
gpg --full-generate-key
gpg --list-secret-keys --keyid-format=long
git config --global user.signingkey <id>
git config --global commit.gpgsign true
git config --global tag.gpgSign true
gpg --armor --export <id>   # paste output into GitHub
```

- Append `!` to the key ID to pin a subkey.
- Use pinentry-mac / GPG Suite for the passphrase prompt.
- Set `GPG_TTY=$(tty)` so pinentry can prompt.

## S/MIME (org X.509)

```sh
git config --global gpg.x509.program smimesign
git config --global gpg.format x509
git config --global user.signingkey <CERT_ID>
```

No public-key upload. Requires Git ≥ 2.19.

## Sigstore / gitsign (keyless OIDC)

```sh
brew install gitsign
git config --local gpg.x509.program gitsign
git config --local gpg.format x509
git config --local commit.gpgsign true
```

**GitHub shows gitsign commits as Unverified**: the Sigstore root is not in GitHub's trust root. Verify locally with `gitsign verify`.

## Adding a key to GitHub

Settings → **SSH and GPG keys** → **New SSH key** / upload GPG.

- For an SSH key, choose key type **signing**. If you use the same key for auth and signing, **upload it twice**.
- CLI: `gh ssh-key add ~/.ssh/id_ed25519.pub --type signing`
- Your git `user.email` **must be a verified email on GitHub**, or commits show Unverified.

## Vigilant mode

Settings → SSH and GPG keys → **Flag unsigned commits as unverified**.

Only enable it if you sign *everything* and the committer email is verified. With mixed signing, it flags your entire history Unverified.

## Web UI and bots

- Commits made in the **GitHub web UI** are auto GPG-signed by GitHub (`web-flow`, Verified). Public key: https://github.com/web-flow.gpg
- **Codespaces** GPG signing is opt-in.
- **GitHub Desktop** signs only if `commit.gpgsign` is true.
- **Dependabot** signs its own commits by default; bots get signed commits by default.
- A `@dependabot merge` merge commit may be unsigned.
- Signing doesn't survive rebase-and-merge.

## Retroactively signing history

You cannot retro-verify without rewriting history; the record is made at push time.

```sh
# preserve author date; changes committer date and SHAs
git rebase --root --exec 'git commit --amend --no-edit -S'

# or, across all refs
git filter-branch -f --commit-filter 'git commit-tree -S "$@"' -- --all
```

Requires a force-push, invalidates clones and PRs, and changes all hashes.

## Gotchas

- Email mismatch → **Unverified**.
- SSH key uploaded only for auth → unverified; re-add it as a **signing** key.
- `allowedSignersFile` is **not** required for GitHub verification (only local verify + merge/pull).
- Git < 2.34 has no SSH signing.
- Vigilant mode with mixed signing flags your whole history Unverified.
- Rebase-and-merge produces unsigned commits.
- gitsign is Unverified by design.
- Expired/revoked keys don't un-verify old records, but a fresh push of an old commit won't verify.
- GPG passphrase prompting needs `GPG_TTY` / pinentry.
- Commit signoff (`git commit -s`) is **not** signing.

## Scope

| Setting | Scope |
|---|---|
| `gpg.format` | machine (`--global`) or repo (`--local`) |
| `user.signingkey` | machine or repo |
| `commit.gpgsign` | machine or repo |
| `tag.gpgSign` | machine or repo |
| `gpg.ssh.allowedSignersFile` | machine or repo |
| `gpg.program` | machine or repo |
| Uploaded signing key | account |
| Verified email | account |
| Vigilant mode | account |
| "Require signed commits" | repo / branch |

Local overrides global.

## Sources

- https://docs.github.com/en/authentication/managing-commit-signature-verification/about-commit-signature-verification
- https://docs.github.com/en/authentication/managing-commit-signature-verification/signing-commits
- https://docs.github.com/en/authentication/managing-commit-signature-verification/telling-git-about-your-signing-key
- https://docs.github.com/en/authentication/managing-commit-signature-verification/displaying-verification-statuses-for-all-of-your-commits
- https://docs.github.com/en/authentication/connecting-to-github-with-ssh/adding-a-new-ssh-key-to-your-github-account
- https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches
- https://git-scm.com/docs/git-config
- https://github.com/sigstore/gitsign
- https://github.blog/engineering/platform-security/commit-signing-support-for-bots-and-other-github-apps/
