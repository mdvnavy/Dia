# Security & secret handling

This is a **public** repository. No credentials may ever be committed.

## Layered protection (all enabled here)

1. **Local** — secrets live in a gitignored `.env` (see `.env.example`), never in
   source. Keys are read from environment variables at runtime.
2. **Push protection** — enable GitHub **Secret Scanning** and **Push Protection**
   on the repo (Settings → Code security and analysis). This blocks a push that
   contains a recognized key before it ever reaches the remote.
3. **CI secret scan** — `.github/workflows/ci.yml` runs
   [gitleaks](https://github.com/gitleaks/gitleaks) on every push and PR.
4. **Production secrets** — the Cloud Run deploy (`deploy-cloudrun.sh`) stores the
   Gemini key in **Google Secret Manager** and mounts it with `--set-secrets`,
   so it is never written into the service config as plaintext.

## Scan locally before pushing

```bash
# one-off scan of the working tree
gitleaks detect --source . --redact
```

Optionally wire it as a pre-commit hook (`.git/hooks/pre-commit`):

```bash
#!/usr/bin/env bash
gitleaks protect --staged --redact || {
  echo "gitleaks found a secret in staged changes — commit aborted."
  exit 1
}
```

## If a key is ever exposed

A key that touches a public commit is compromised the instant it lands —
automated scrapers find public keys within seconds. Removing it from history is
**not** sufficient.

1. **Rotate immediately.** Revoke the leaked Gemini key at
   <https://aistudio.google.com/app/apikey> and issue a new one.
2. Update the local `.env` and the Secret Manager version.
3. Only then scrub history (e.g. `git filter-repo` or BFG) if needed.

Order matters: rotate first, scrub second. The rotation is what actually closes
the hole.
