# Security

## How secrets are handled

- Secrets live only in `.env` (git-ignored) and in n8n's encrypted credential store. Nothing secret is committed.
- Workflow exports reference credentials by **name** and read other values from environment variables
  (`{{ $env.MIDDLEWARE_API_KEY }}`, `{{ $env.SHEET_ID_PROJECT_A }}`, …).
- The Smartsheet token is only given to the `middleware` container, never to n8n.
- Host ports bind to `127.0.0.1`. Exposing n8n publicly (tunnel or reverse proxy) is an explicit, opt-in step.

## Before you push or share a fork

```bash
python scripts/validate_repo.py
```

It validates every workflow export and fails if it finds tokens, private keys, phone numbers, real-looking
WhatsApp/Slack IDs, e-mail addresses or Google Sheet links. Never commit real n8n exports (they contain instance IDs,
credential IDs, webhook paths and chat identifiers) or database/WhatsApp-session dumps.

If a secret was ever committed, rotating it is mandatory; deleting it from a later commit does not remove it from history.

## Reporting a vulnerability

Please open a private security advisory on GitHub (Security → Report a vulnerability) rather than a public issue.
