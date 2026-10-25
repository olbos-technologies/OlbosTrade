# Contributing to OlbosTrade

Thanks for helping improve OlbosTrade. The project includes financial software, so correctness and conservative defaults matter more than feature velocity.

## Getting started

1. Fork the repository and create a feature branch from `main`.
2. Read [README.md](README.md), [TRADING_POLICY.md](TRADING_POLICY.md), and [docs/MASTER_ARCHITECTURE.md](docs/MASTER_ARCHITECTURE.md).
3. Use local, synthetic, or paper-trading data while developing. Never commit brokerage credentials, tokens, account numbers, or customer records.
4. Keep changes narrowly scoped and add tests where behavior changes.

## Local development

Follow the first-time installation instructions in [README.md](README.md). For backend validation, use:

```bash
cd backend
python3 -m pytest tests/ -v
```

For frontend checks, consult `frontend/package.json` for the available scripts rather than assuming a specific command.

## Pull requests

Describe the motivation, implementation, affected modules, tests run, and any risks. Document migration steps and rollback instructions if needed. Do not bypass execution approvals, the kill switch, risk limits, audit trails, or paper/live separation.

Keep strategy performance statements evidence-based; do not advertise guaranteed returns. Do not include sensitive logs or real brokerage data in screenshots.

Security-sensitive findings should use [SECURITY.md](SECURITY.md), not a public issue.
