# Workflow files (disabled)

These GitHub Actions live in `.github/workflows.disabled/` because the GitHub
App that pushed this repository does **not** have the `workflows` permission
— GitHub refuses to let Apps create/update files under `.github/workflows/`.

The repository is fully functional without them. To re-enable CI and the
safety automation:

1. In GitHub: **Settings → Applications → (your GitHub App) → Permissions →
   Workflows → Read and write → Save**.
2. Move the files back into place:

   ```bash
   mkdir -p .github/workflows
   for f in .github/workflows.disabled/*.yml; do
     mv "$f" .github/workflows/
   done
   rmdir .github/workflows.disabled
   ```

3. Commit and push:

   ```bash
   git add .github/workflows
   git commit -m "Enable GitHub Actions workflows"
   git push
   ```

Files here:

| File | Purpose |
|---|---|
| `ci.yml` | CI: runs the test suite on Python 3.10/3.11/3.12 |
| `build-exe.yml` | Windows `.exe` packaging |
| `safety-blocklist-freshness.yml` | Opens a reminder issue when the safety blocklist goes stale (Mon + Thu) |
| `youtube-policy-watch.yml` | Watches YouTube's official policy pages daily; opens an issue when they change (v7.18) |
