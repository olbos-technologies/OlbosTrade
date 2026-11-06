## Unreleased — start.sh trims regenerable caches so a full disk does not stop the desk

- New `scripts/clean_caches.sh`. Run on its own it is a dry run that lists what would be removed;
  with `--apply` it removes an allowlist of caches that regenerate on their own: Python bytecode and
  tool caches under `backend/` (`__pycache__`, `.pytest_cache`, `.mypy_cache`, `.ruff_cache`),
  Vite's dependency cache (`frontend/node_modules/.vite`), and build/test output
  (`frontend/dist`, `frontend/test-results`, `frontend/playwright-report`).
- It never touches trained models (`backend/ml/model_registry`, not in git), `.env` files,
  `node_modules`, virtualenvs, logs, databases or exported research data; skips symlinks; and
  refuses to run if its directory does not look like this project.
- `start.sh` runs it with `--apply` after stopping the old processes and before starting the new
  ones (steps are now numbered /6). A failure is advisory: startup continues.
- On a typical checkout it frees tens of MB; the largest items are `.pytest_cache`, test
  `__pycache__` and the Vite cache. It does not touch Docker images, `~/.npm` or the pip cache.

Deploy: none for the Hetzner server (this is the local start script; the server's `update.sh`
already prunes Docker build cache and old images).
