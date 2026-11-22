# Setup guide

This guide takes you from a fresh clone to running automations.

## 1. Prerequisites

- Docker with Compose v2 (`docker compose version`)
- A Slack workspace where you can create an app
- A Smartsheet account with an API token (Account → Personal Settings → API Access)
- A Google account for the phone-directory sheet and a [Gemini API key](https://aistudio.google.com/apikey)
- One or two spare WhatsApp numbers to link (one for the Slack bridge, one for the assistant)

## 2. Configure and start the stack

```bash
cp .env.example .env
```

Edit `.env`:

- Replace every `CHANGE_ME` with a strong random value: `openssl rand -hex 32` (hex avoids special characters that break connection strings).
- Set `SMARTSHEET_TOKEN`, `SHEET_ID_PROJECT_A` and (optionally) `SHEET_ID_PROJECT_B`. The sheet ID is in Smartsheet under *File → Properties*.
- Set `ADMIN_WHATSAPP_JID` to the manager's number, e.g. `15550000001@s.whatsapp.net`.

```bash
docker compose up -d --build
docker compose ps          # everything should become "running"/"healthy"
curl http://localhost:5001/health    # {"status":"ok"}
```

On the very first start Postgres runs `database/schema.sql` and creates the automation tables.

## 3. Public URL for webhooks

Slack and the WhatsApp gateway need to reach n8n. For local development use the bundled quick tunnel:

```bash
docker compose --profile tunnel up -d
docker compose logs tunnel | grep trycloudflare.com    # copy the https URL
```

Put that URL in `EXTERNAL_BASE_URL` in `.env` and recreate the services that use it:

```bash
docker compose up -d n8n evolution_api
```

The quick-tunnel URL changes on every restart. For anything permanent use a named Cloudflare tunnel or a real domain.

## 4. n8n first run

1. Open http://localhost:5678 and create the owner account.
2. **Settings → Community nodes → Install** `n8n-nodes-evolution-api-en` (the WhatsApp node used by the admin assistant and reminders).

## 5. Create the credentials

Create these in **n8n → Credentials**. The workflows reference them by name, so keep the names exactly:

| Credential name | Type | Values |
|---|---|---|
| `Postgres account` | Postgres | Host `postgres`, port `5432`, database/user/password from `.env` |
| `Slack account` | Slack API | Bot token (`xoxb-…`) |
| `Slack Download Auth` | Header Auth | Name `Authorization`, value `Bearer xoxb-…` (downloads private Slack files) |
| `Header Auth account` | Header Auth | Name `apikey`, value = `EVOLUTION_API_KEY` (HTTP nodes calling the WhatsApp gateway) |
| `Evolution account` | Evolution API | URL `http://evolution_api:8080`, API key = `EVOLUTION_API_KEY` |
| `Google Sheets account` | Google Sheets OAuth2 | Your OAuth client (used to read the phone directory) |
| `Google Gemini API` | Google Gemini (PaLM) API | Your Gemini API key |

Typical Slack bot scopes: `chat:write`, `files:write`, `files:read`, `channels:history`, `groups:history`,
`users:read`, `users.profile:read`. The *Slack to WhatsApp* workflow uses a Slack Trigger, so the app also needs
Event Subscriptions for `message.channels` / `message.groups` pointing at the trigger URL n8n shows (see the
[n8n Slack Trigger docs](https://docs.n8n.io/integrations/builtin/trigger-nodes/n8n-nodes-base.slacktrigger/)).

## 6. Import the workflows

For each file under `workflows/`: **n8n → Workflows → Import from file**. Then open each workflow and, on every
node showing a credential warning, pick the matching credential from step 5. The credential IDs inside the
files are placeholders by design.

> Imported workflows are **inactive**. Activate them only after the credentials and the configuration below are done.

## 7. Connect WhatsApp (Evolution API)

Open the Evolution manager at http://localhost:8080/manager and log in with `EVOLUTION_API_KEY`.

| Instance name | Used by | Webhook URL (event `MESSAGES_UPSERT`, include media as base64) |
|---|---|---|
| `wa-slack-bridge` | WhatsApp ⇄ Slack bridge, EOD summary | `http://n8n:5678/webhook/whatsapp-to-slack` |
| `whatsapp_1` | Admin assistant, reminders | Project A: `http://n8n:5678/webhook/project-a-admin` · Project B: `http://n8n:5678/webhook/project-b-admin` |

Create each instance, scan the QR code with the WhatsApp number to link, and set its webhook. (If you use a
single instance for both, point its webhook at the URL you need and adjust the instance names in the workflows.)

Inside Docker the gateway reaches n8n through the service name `n8n`; use the `EXTERNAL_BASE_URL` form instead if
the gateway runs elsewhere. Slack events always use the public URL.

## 8. Map your groups and channels

The sample IDs in the workflows are fake (`120363000000000NNN@g.us`, `C0EXAMPLNNN`). Replace them with yours:

- **WhatsApp to Slack Bridge** → *Message Parser* node: edit `CHANNEL_MAP` (WhatsApp group JID → Slack channel ID). The entry whose value is `SUMMARY_ONLY` is logged for the end-of-day summary instead of being forwarded.
- **Slack to WhatsApp Bridge** → *Message Parser* code and the `number` expression in *Send to WhatsApp*: the reverse map.
- **EOD Chat Summarizer** → the group JID in the *The Fetcher* query and in *Summary to WhatsApp*.

To find a WhatsApp group JID, send a message in the group and look at `data.key.remoteJid` in the webhook execution.

## 9. Smartsheet and phone directory

- The sync workflows read the sheet through the middleware; the sheet needs the columns listed in [`workflows.md`](workflows.md#smartsheet-task-tracking).
- Reminders look people up in a Google Sheet with columns **`Assigned to`** (name, as in Smartsheet) and **`Phone no`** (digits with country code). In both *Task Reminders* workflows, open the *Fetched Directory* node and select your spreadsheet (the file ships with the placeholder `YOUR_SPREADSHEET_ID` and the tab names `Project A Phone Directory` / `Project B Phone Directory`).

## 10. Activate

Activate the workflows. Quick checks:

1. Send a message in a mapped WhatsApp group; it should appear in Slack within seconds.
2. Post in the mapped Slack channel; it should appear in WhatsApp.
3. Run *Smartsheet Sync* manually; the `task_master_project_a` table should fill up (`docker compose --profile tools up -d` for pgAdmin).
4. From the admin number, message the assistant `hi`, then an employee name.

## Troubleshooting

| Symptom | Check |
|---|---|
| `variable is not set` on `docker compose up` | A required value is missing in `.env`. |
| Workflow error: *access to env vars denied* | `N8N_BLOCK_ENV_ACCESS_IN_NODE` must be `false` for the n8n service (it is in `docker-compose.yml`). |
| Middleware returns `403` | `MIDDLEWARE_API_KEY` in `.env` is empty or differs between runs; recreate `n8n` and `middleware`. |
| Slack/WhatsApp events never arrive | `EXTERNAL_BASE_URL` is stale (quick-tunnel URLs change on restart) or the instance webhook is not set. |
| Stored credentials unreadable after a rebuild | `N8N_ENCRYPTION_KEY` changed. Restore the old value. |
| WhatsApp node missing | Install the community node `n8n-nodes-evolution-api-en` (step 4). |
