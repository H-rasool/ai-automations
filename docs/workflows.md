# Workflow reference

All workflows are exported from n8n 2.x and import **inactive**. Credential names are listed so you can map
them (see [setup.md](setup.md#5-create-the-credentials)).

| Folder | Workflow | Trigger | Credentials |
|---|---|---|---|
| `whatsapp-slack-bridge/` | [WhatsApp to Slack Bridge](#whatsapp-to-slack-bridge) | Webhook `POST /webhook/whatsapp-to-slack` | Slack account, Postgres account |
| | [Slack to WhatsApp Bridge](#slack-to-whatsapp-bridge) | Slack Trigger | Slack account, Slack Download Auth, Header Auth account |
| | [EOD Chat Summarizer (Gemini)](#eod-chat-summarizer) | Schedule, daily 06:01 | Postgres account, Google Gemini API, Header Auth account |
| `smartsheet-task-tracking/project-*/` | [Smartsheet Sync](#smartsheet-task-tracking) | Schedule | Postgres account |
| | [Task Reminders](#smartsheet-task-tracking) | Schedule, 2× on weekdays | Postgres account, Google Sheets account, Evolution account |
| | [Admin Assistant and Smartsheet Update](#smartsheet-task-tracking) | Webhook `POST /webhook/project-{a,b}-admin` | Postgres account, Evolution account |
| `experimental/` | [Critical Path Calculator](#critical-path-calculator-experimental) | Schedule, 2× on weekdays | Postgres account |

Environment variables read by workflows (`{{ $env.NAME }}`): `MIDDLEWARE_API_KEY`, `SHEET_ID_PROJECT_A`,
`SHEET_ID_PROJECT_B`, `ADMIN_WHATSAPP_JID`.

---

## WhatsApp to Slack Bridge

`whatsapp-slack-bridge/whatsapp-to-slack.json`: 10 nodes.

1. **WhatsApp Webhook**: receives Evolution API `MESSAGES_UPSERT` events (with media as base64).
2. **Check Duplicate**: drops messages whose id was already seen (kept in workflow static data).
3. **Respond to Webhook**: answers `200` immediately so the gateway never retries.
4. **Message Parser** (Code): ignores messages sent by the bot itself, reactions and message updates; recursively
   finds the base64 payload (falls back to the JPEG thumbnail); detects text / image / video / audio / document and
   captions; maps the WhatsApp group JID to a Slack channel through `CHANNEL_MAP`; ignores unmapped groups.
5. **Route by Channel**: a `SUMMARY_ONLY` entry goes to Postgres, everything else to Slack.
6. **If Media → Upload a file / Send a message**: posts to Slack as `*Sender*: text`, uploading the file when there is media.
7. **Insert rows in a table**: stores `group_id`, `sender_name`, `message_text` in `whatsapp_daily_logs` for the EOD summary.

## Slack to WhatsApp Bridge

`whatsapp-slack-bridge/slack-to-whatsapp.json`: 17 nodes.

1. **Slack Trigger** fires on channel messages; **Is Human?** drops bot messages.
2. **Get a user's profile** resolves the sender's display name.
3. Messages with several attachments are **split into one item per file** (Split Out → Message Parser → Wait between sends).
4. **Message Parser** maps the Slack channel to a WhatsApp group JID and detects the media type from the MIME type.
5. **Get Media** downloads the file from Slack (authenticated), **Media Parser** prepares it, **Send Media** posts it
   through Evolution's `sendMedia` with the caption `*Sender*: text`.
6. Text-only messages pass the **Empty Shield** and go through `sendText`.

## EOD Chat Summarizer

`whatsapp-slack-bridge/eod-summarizer.json`: 6 nodes.

1. **Schedule Trigger**: every day at 06:01 (timezone from `GENERIC_TIMEZONE`).
2. **The Fetcher**: unprocessed rows of the summary group from `whatsapp_daily_logs`.
3. **Formatter & Gateway**: builds a transcript and the list of row ids (stops quietly when there were no messages).
4. **Summary AI**: Gemini 2.5 Flash-Lite with a strict prompt: *Executive TL;DR, Test Results & Data Logged, Faults & Diagnostics, Key Discussions, Action Items*; "do not invent numbers" and "ignore chatter" rules.
5. **Summary to WhatsApp**: posts the summary to the group; **Cleanup DB** marks the rows `processed = TRUE`.

## Smartsheet task tracking

Three workflows per project (`project-a/`, `project-b/`). They are copies that differ in sheet ID (`SHEET_ID_PROJECT_*`),
table names (`task_master_project_*`, `task_history_project_*`, `admin_query_project_*`), the webhook path and the
directory-sheet tab name.

### Smartsheet Sync (`smartsheet-sync.json`)

`Schedule → TRUNCATE task_master_* → GET middleware /extract-sheet → Split Out → clean nulls → INSERT`
Project A runs every minute and upserts by `rowId` (hierarchy: `parentId`, `rowNumber`); Project B reloads every 8 hours.

Expected Smartsheet column titles (as used by the inserts): `Task id`, `Task Name`, `Assigned To`, `Percentage Complete`,
`Start`, `Finish`, `Duration`, `Predecessors`, `Comments`, `Critical` plus the extra columns listed in `database/schema.sql`.

### Task Reminders (`task-reminders.json`)

`Schedule (14:00 and 21:00 on weekdays, edit as needed) → read tasks + phone directory (Google Sheet) → Extract Tasks → Aggregate per person → If hour ≥ 21 → evening wrap-up, else morning audit`

- Skips tasks at 100 %.
- **Velocity model**: `percent complete ÷ days since start` → projected finish date → projected delay versus the planned finish; labels *OVERDUE*, *DUE SOON* (≤ 48 h), *FUTURE*, *ACTIVE* and pace *ON TRACK / SLIGHTLY BEHIND / CRITICAL SLOWDOWN*.
- Names are matched to phone numbers through the directory sheet (`Assigned to`, `Phone no`); multi-assignee tasks notify everyone; one message per person.

### Admin Assistant and Smartsheet Update (`admin-assistant.json`)

Webhook from the assistant's WhatsApp number, routed by a **Switch**:

1. **Employee update**: message contains `Tid`:
   - extracts the task id (`Tid\d{1,6}`) and percentage (`80%`),
   - finds the row in `task_master_*`, logs the message in `task_history_*`,
   - converts to a Smartsheet decimal and calls the middleware `update-row` (sets *Percentage Complete* and *Comments*).
2. **Admin query**: message from `ADMIN_WHATSAPP_JID`:
   - **Message analyzer** (Code): greeting/menu, quick commands (`active`, `completed`, `pending`) or an employee-name search with noise-word removal,
   - loads all tasks, the task history and the *last searched employee* (conversation memory),
   - **Formatter node** builds the WhatsApp report: rating, average completion, finished/active/not-started counts, engagement, latest message, top active tasks; handles ambiguous names ("I found 3 profiles…"),
   - replies through Evolution and logs the query in `admin_query_*`.
3. Anything else is ignored.

## Critical Path Calculator (experimental)

`experimental/critical-path.json`: `Schedule (08:00 and 17:00 on weekdays) → read tasks from Postgres → calculate CP → POST middleware /update-batch`

Module-wise critical-path engine: supports FS/SS/FF/SF dependencies and lags, working days, groups tasks by
`parentId`, treats *total float ≤ 0 and percent complete < 100* as critical, and returns **only rows whose Critical flag
changed**.

> Status: experimental. It was written before the middleware became multi-sheet: it reads a legacy table
> (`task_master_log`, not in `schema.sql`) and the *Update sheet* body does not include `sheetId`. Point the query at
> `task_master_project_a` and add `sheetId` to the request body to use it.
