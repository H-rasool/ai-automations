# Architecture

## Components

| Service | Image / source | Role |
|---|---|---|
| `n8n` | `n8n/Dockerfile` (n8n 2.6.3) | Runs all workflows; stores its own data in Postgres |
| `evolution_api` | `evoapicloud/evolution-api` | Self-hosted WhatsApp gateway (HTTP API + webhooks) |
| `middleware` | `middleware/` (Flask + Waitress) | Smartsheet gateway; the only holder of the Smartsheet token |
| `postgres` | `postgres:15-alpine` | n8n DB, Evolution API DB and the automation tables (`database/schema.sql`) |
| `redis` | `redis:7-alpine` | Evolution API cache |
| `pgadmin` *(profile `tools`)* | `dpage/pgadmin4` | Optional DB UI |
| `tunnel` *(profile `tunnel`)* | `cloudflare/cloudflared` | Optional public URL for Slack/WhatsApp webhooks |

All services share one Docker network (`automation`). Workflows address them by service name:
`http://middleware:5001`, `http://evolution_api:8080`, host `postgres`. Host ports are published on
`127.0.0.1` only.

## System context

```mermaid
flowchart TB
    subgraph External
        WA[WhatsApp]
        SL[Slack]
        SS[Smartsheet]
        GS[Google Sheets]
        GEM[Google Gemini]
    end
    subgraph Docker["Docker network: automation"]
        EVO[evolution_api]
        N8N[n8n]
        MW[middleware]
        PG[(postgres)]
        RD[(redis)]
    end
    WA <--> EVO
    EVO -- "webhook: MESSAGES_UPSERT" --> N8N
    N8N -- "sendText / sendMedia" --> EVO
    SL <-- "events + API" --> N8N
    N8N <--> PG
    N8N -- "X-API-KEY" --> MW
    MW -- "Bearer token" --> SS
    N8N --> GS
    N8N --> GEM
    EVO --- RD
    EVO --- PG
```

## WhatsApp → Slack

```mermaid
sequenceDiagram
    participant WA as WhatsApp group
    participant EVO as Evolution API
    participant N as n8n (WhatsApp to Slack Bridge)
    participant S as Slack
    participant DB as Postgres
    WA->>EVO: new message
    EVO->>N: POST /webhook/whatsapp-to-slack
    N->>N: Check Duplicate (message id)
    N-->>EVO: 200 OK (respond immediately)
    N->>N: Message Parser (skip own msgs / reactions, extract text + media, map group → channel)
    alt normal group
        N->>S: post message / upload file
    else "SUMMARY_ONLY" group
        N->>DB: INSERT whatsapp_daily_logs
    end
```

## Slack → WhatsApp

```mermaid
flowchart LR
    T[Slack Trigger] --> H{Is human?}
    H -- no --> X[ignore]
    H -- yes --> P[Get Slack profile → display name]
    P --> M{Multiple attachments?}
    M -- yes --> SO[Split Out files] --> MP[Message Parser]
    M -- no --> E[Empty Shield] --> ST[Send text via Evolution]
    MP --> IM{Media?}
    IM -- yes --> G[Download file from Slack] --> MD[Media Parser] --> SM[Send media via Evolution] --> W[Wait]
    IM -- no --> X2[no-op]
```

## End-of-day summary

```mermaid
flowchart LR
    CRON[Schedule 06:01] --> F[Fetch unprocessed rows] --> T[Build transcript] --> AI[Gemini 2.5 Flash-Lite] --> SEND[Send to WhatsApp group] --> DONE[Mark rows processed]
```

## Smartsheet task tracking (per project)

```mermaid
flowchart LR
    subgraph Sync
        S1[Schedule] --> S2[TRUNCATE mirror table] --> S3[GET /extract-sheet] --> S4[Split rows] --> S5[Clean nulls] --> S6[INSERT into task_master_*]
    end
    subgraph Reminders
        R1[Schedule 2× weekdays] --> R2[Read tasks + phone directory] --> R3[Velocity / projected delay per task] --> R4[Group by person] --> R5{hour ≥ 21?}
        R5 -- yes --> R6[Evening wrap-up]
        R5 -- no --> R7[Morning audit]
    end
    subgraph Assistant
        A1[Webhook from WhatsApp] --> A2{Switch}
        A2 -- "contains Tid" --> A3[Parse task id + %] --> A4[Match row] --> A5[Log history] 
        A4 --> A6[POST /update-row] 
        A2 -- "admin number" --> A7[Analyze intent] --> A8[Fetch tasks + history + context] --> A9[Format audit] --> A10[Reply + log query]
    end
```

### Employee progress update (sequence)

```mermaid
sequenceDiagram
    participant U as Employee (WhatsApp)
    participant EVO as Evolution API
    participant N as n8n (Admin Assistant)
    participant DB as Postgres
    participant MW as Middleware
    participant SS as Smartsheet
    U->>EVO: "Tid0042 80% finished wiring"
    EVO->>N: webhook
    N->>N: regex → taskId=TID0042, percentage=80%
    N->>DB: SELECT row by Task id
    N->>DB: INSERT task_history_*
    N->>MW: POST /update-row {sheetId,rowId,updates}
    MW->>SS: PUT rows (Percentage Complete = 0.8, Comments)
```

## Design decisions

- **Middleware instead of direct Smartsheet nodes.** Keeps the Smartsheet token out of n8n, lets workflows use column *titles*, and handles any sheet by `sheetId`; one place to add retries or caching later.
- **Postgres mirror of the sheet.** Reminders and the assistant read from Postgres (fast, queryable, no API quota) while Smartsheet stays the source of truth.
- **Respond to the webhook first.** The WhatsApp bridge replies `200` right after de-duplication so the gateway does not retry slow media uploads.
- **Config through environment.** Secrets and IDs are never in the workflow files; they are read from `$env` or n8n credentials, so the same exports work on any instance.
- **Safe-by-default packaging.** Workflows import inactive, ports bind to localhost, and the public tunnel is opt-in.
