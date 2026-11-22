-- =============================================================================
-- Reference schema for the automation tables.
--
-- Loaded automatically by Postgres on the FIRST start of the stack (mounted into
-- /docker-entrypoint-initdb.d by docker-compose.yml). n8n and Evolution API create
-- their own internal tables themselves; this file only adds what the workflows read/write.
--
-- It was reconstructed from the SQL used inside the workflows (not exported from a
-- live database), so adjust column types if your Smartsheet columns differ.
-- =============================================================================

-- ---------------------------------------------------------------------------
-- WhatsApp -> Slack bridge: messages from "summary only" groups are stored here
-- during the day and summarised by the EOD workflow (then flagged processed).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.whatsapp_daily_logs (
    id           BIGSERIAL PRIMARY KEY,
    group_id     TEXT      NOT NULL,           -- WhatsApp group JID, e.g. 120363...@g.us
    sender_name  TEXT      NOT NULL,
    message_text TEXT      NOT NULL,
    processed    BOOLEAN   NOT NULL DEFAULT FALSE,
    created_at   TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_whatsapp_daily_logs_pending
    ON public.whatsapp_daily_logs (group_id, created_at) WHERE processed = FALSE;

-- ---------------------------------------------------------------------------
-- Project A: mirror of a Smartsheet task plan with hierarchy.
-- The sync workflow upserts on "rowId"; the admin assistant reads the same value as "id",
-- so "id" is a generated alias of "rowId".
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.task_master_project_a (
    "rowId"                       BIGINT PRIMARY KEY,           -- Smartsheet row ID
    id                            BIGINT GENERATED ALWAYS AS ("rowId") STORED,
    "parentId"                    BIGINT,
    "rowNumber"                   INTEGER,
    "Hierarchy"                   TEXT,
    "Critical Milestones"         BOOLEAN DEFAULT FALSE,
    "Dashboard Status"            TEXT,
    "Dashboard Data"              TEXT,
    "Task Name"                   TEXT,
    "Start"                       DATE,
    "Finish"                      DATE,
    "Duration"                    TEXT,
    "Percentage Complete"         NUMERIC DEFAULT 0,
    "Predecessors"                TEXT,
    "Assigned To"                 TEXT,                         -- comma separated names
    "Assigned to (% Allocation)"  INTEGER DEFAULT 0,
    "TF_M1"                       NUMERIC DEFAULT 0,
    "FF_M1"                       NUMERIC DEFAULT 0,
    "Float"                       NUMERIC DEFAULT 0,
    "People Hours"                NUMERIC DEFAULT 0,
    "# Resources"                 INTEGER DEFAULT 0,
    "Comments"                    TEXT,
    "Task id"                     TEXT,                         -- e.g. Tid0001, used in WhatsApp updates
    "Critical"                    BOOLEAN DEFAULT FALSE,
    "User Response"               TEXT,
    "User Response Time"          TEXT,
    last_sync                     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_task_master_project_a_task_id
    ON public.task_master_project_a (LOWER(TRIM("Task id")));

-- ---------------------------------------------------------------------------
-- Project B: flat task plan (the sync workflow truncates and reloads it).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.task_master_project_b (
    id                                    BIGINT PRIMARY KEY,   -- Smartsheet row ID
    "Task id"                             TEXT,
    "Task Name"                           TEXT,
    "Assigned To"                         TEXT,
    "Status"                              TEXT,
    "Percentage Complete"                 TEXT,
    "Start"                               TIMESTAMP,
    "Finish"                              TIMESTAMP,
    "Baseline Start"                      TIMESTAMP,
    "Baseline Finish"                     TIMESTAMP,
    "Baseline Start2"                     TIMESTAMP,
    "Baseline Finish2"                    TIMESTAMP,
    "Duration"                            TEXT,
    "Equipment"                           TEXT,
    "Supporting Resource"                 TEXT,
    "Critical"                            BOOLEAN,
    "Allocation %"                        TEXT,
    "Allocation % (Supporting resource 1)" TEXT,
    "Assigned to (Allocation %)"          TEXT,
    "Predecessors"                        TEXT,
    "Variance"                            TEXT,
    "Variance2"                           TEXT,
    "Desing Updates/Reworks"              TEXT,                 -- (sic) matches the Smartsheet column title
    "FW"                                  TEXT,
    "Units Timeline"                      TEXT,
    "Comments"                            TEXT,
    "Phone no"                            TEXT,
    "User Response"                       TEXT,
    "User Response Time"                  TEXT,
    synced_at                             TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_task_master_project_b_task_id
    ON public.task_master_project_b (LOWER(TRIM("Task id")));

-- ---------------------------------------------------------------------------
-- Per-project audit tables written by the admin-assistant workflow
-- ---------------------------------------------------------------------------
-- every progress update an employee sends over WhatsApp
CREATE TABLE IF NOT EXISTS public.task_history_project_a (
    id                  BIGSERIAL PRIMARY KEY,
    sender_phone        VARCHAR(20),
    task_id             VARCHAR(50),
    percentage_reported VARCHAR(10),
    raw_message         TEXT,
    created_at          TIMESTAMP DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS public.task_history_project_b (LIKE public.task_history_project_a INCLUDING ALL);

-- every question the admin asks the assistant (used for conversational context)
CREATE TABLE IF NOT EXISTS public.admin_query_project_a (
    id                BIGSERIAL PRIMARY KEY,
    admin_phone       VARCHAR(50),
    raw_query         TEXT,
    query_type        VARCHAR(100),
    searched_employee VARCHAR(100),
    created_at        TIMESTAMP DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS public.admin_query_project_b (LIKE public.admin_query_project_a INCLUDING ALL);
