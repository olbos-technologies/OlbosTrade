## Unreleased — A deploy now stamps the commit it ships, so "what is the server running" is answerable

- The status-bar footer was written to show the build's commit, but nothing ever passed the commit
  to the frontend build, so production showed the hardcoded fallback "Olbos v5.0" — a version that
  looked real and said nothing. `deploy/hetzner/update.sh` now exports the 7-character commit it
  just verified against `origin/main`; `docker-compose.hetzner.yml` passes it to both builds;
  both Dockerfiles bake it in.
- The footer shows `Olbos <commit>`. An unstamped build (a manual `docker compose build`) now says
  `Olbos · build unknown` instead of inventing a version.
- Backend: `GET /api/health/detail` reports `build.git_sha`, and startup logs `Build stamp: <sha>`
  (or a warning that it is unknown). A malformed value reads as `unknown` and is never echoed. The
  public `/health` is unchanged: it still carries only status, broker and `broker_connected`.
- Check on the server without a browser: `docker exec olbostrade-backend printenv GIT_SHA`.

Deploy: rebuild backend and frontend (the normal `update.sh`). No migration, no new environment
variables. The first deploy that includes this is what makes the stamp appear; builds before it
cannot show one.
