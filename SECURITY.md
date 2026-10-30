# Security Policy

## Reporting vulnerabilities

**Do not open public issues or pull requests containing exploit details, credentials, or sensitive account information.**

If this repository has GitHub's private vulnerability reporting enabled, use its **Security → Report a vulnerability** flow. If that option is unavailable, contact the repository maintainers through a trusted private channel before sharing technical details. This file does not establish a monitored email address or a guaranteed response time.

Provide a concise description, affected revision, safe reproduction steps, and potential impact. Do not test against other people's accounts or production brokerage sessions.

## Particularly sensitive areas

- Broker API authentication and account isolation
- Manual/Copilot/Autopilot execution authorization
- Paper-versus-live trading configuration
- Kill switches, risk limits, order submission, and reconciliation
- Secrets, logs, dependency vulnerabilities, and personal or financial data

Use paper environments and fabricated data for reproduction. Do not publish working credentials or account data. Maintainers can coordinate a fix and responsible disclosure privately.
