## Unreleased — The gateway watchdog no longer restarts a healthy gateway; the gateway API is no longer documented as world-reachable

- **The watchdog restarted a healthy IB Gateway about 800 times.** It probed
  `/api/health/detail` and treated any error as "gateway dead". Once auth was
  enabled that route answered 401 from inside the container, so every probe
  "failed": the gateway was restarted in bursts of four every ~6 hours, often
  during market hours, each time dropping the backend's broker connection.
- The probe now reads the public `/health`, which reports `broker_connected` as
  true, false or null. Only a clear `false` triggers a restart. A 401, a 5xx, a
  timeout, bad JSON, a missing field (older backend) or `null` is "inconclusive":
  no restart, and the restart history is left alone.
- `/health` and `/api/health` gain one field, `broker_connected`. It is a local
  read with no I/O, and "could not tell" is `null`, never `false`.
- The watchdog's `PATH` is now appended to, not prepended, so a stub `docker` can
  win in tests; cron's minimal `PATH` is still filled in.
- The README documented `docker run ... -p 4002:4004`, which publishes the IB
  Gateway API on every interface. Docker bypasses `ufw` for published ports, and
  with `READ_ONLY_API=no` anyone who reaches it can place and cancel orders.
  It now documents `127.0.0.1:4002:4004`, and a test fails if the bare form
  returns anywhere under `deploy/` or `docs/`.
- No schema change, no trading code, no risk-path change. **The server's cron line
  for the watchdog was disabled by hand on 2026-11-02; re-enable it after this
  is deployed** by removing the `#DISABLED-401-BUG ` prefix from `crontab -e`.
