-- 0001_init: core schema for Procurement Copilot.
-- Applied by brain/common/migrate.py (plain SQL so both Rust and Python read one source of truth).

-- ---------------------------------------------------------------------------
-- Organisation
-- ---------------------------------------------------------------------------
CREATE TABLE departments (
    id   TEXT PRIMARY KEY,               -- 'IT', 'HR', 'Finance', ...
    name TEXT NOT NULL
);

CREATE TYPE user_role AS ENUM ('employee', 'manager', 'finance', 'admin');

CREATE TABLE users (
    id             TEXT PRIMARY KEY,     -- readable slug, e.g. 'priya.sharma'
    email          TEXT NOT NULL UNIQUE,
    name           TEXT NOT NULL,
    role           user_role NOT NULL,
    department_id  TEXT NOT NULL REFERENCES departments(id),
    spend_limit    NUMERIC(14,2) NOT NULL CHECK (spend_limit >= 0),
    approval_limit NUMERIC(14,2) NOT NULL CHECK (approval_limit >= 0)
);

CREATE TABLE budgets (
    department_id TEXT NOT NULL REFERENCES departments(id),
    fiscal_year   INT  NOT NULL,
    allocated     NUMERIC(14,2) NOT NULL CHECK (allocated >= 0),
    committed     NUMERIC(14,2) NOT NULL DEFAULT 0 CHECK (committed >= 0),  -- approved, not yet invoiced
    spent         NUMERIC(14,2) NOT NULL DEFAULT 0 CHECK (spent >= 0),
    PRIMARY KEY (department_id, fiscal_year),
    CHECK (committed + spent <= allocated)
);

-- ---------------------------------------------------------------------------
-- Vendors & contracts. PII columns are encrypted app-side (AES-256-GCM, key from
-- PII_ENCRYPTION_KEY); the *_masked columns are what non-privileged roles see.
-- ---------------------------------------------------------------------------
CREATE TABLE vendors (
    id                   TEXT PRIMARY KEY,   -- 'V-0001'
    name                 TEXT NOT NULL,
    category             TEXT NOT NULL,
    status               TEXT NOT NULL CHECK (status IN ('active', 'onboarding', 'suspended', 'offboarded')),
    risk_rating          TEXT NOT NULL CHECK (risk_rating IN ('low', 'medium', 'high')),
    contact_name_enc     BYTEA NOT NULL,
    contact_email_enc    BYTEA NOT NULL,
    contact_email_masked TEXT  NOT NULL,
    phone_enc            BYTEA NOT NULL,
    iban_enc             BYTEA NOT NULL,
    iban_masked          TEXT  NOT NULL,     -- e.g. 'DE89 •••• •••• 3000'
    tax_id_enc           BYTEA NOT NULL,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX vendors_category_idx ON vendors (category);
CREATE INDEX vendors_name_lower_idx ON vendors (lower(name));

CREATE TABLE contracts (
    id          TEXT PRIMARY KEY,           -- 'C-0001'
    vendor_id   TEXT NOT NULL REFERENCES vendors(id),
    title       TEXT NOT NULL,
    start_date  DATE NOT NULL,
    end_date    DATE NOT NULL CHECK (end_date >= start_date),
    value       NUMERIC(14,2) NOT NULL CHECK (value >= 0),
    currency    CHAR(3) NOT NULL DEFAULT 'USD',
    doc_id      TEXT                         -- documents.id of the contract text, if ingested
);

-- ---------------------------------------------------------------------------
-- Purchase requests and approvals
-- ---------------------------------------------------------------------------
CREATE TYPE pr_status AS ENUM (
    'draft', 'pending_manager', 'pending_finance', 'pending_admin',
    'approved', 'rejected', 'ordered', 'cancelled'
);

CREATE SEQUENCE pr_number_seq START 1;

CREATE TABLE purchase_requests (
    -- Human-readable ids so a user can say "approve PR-2026-00042" in chat.
    id              TEXT PRIMARY KEY DEFAULT ('PR-' || to_char(now(), 'YYYY') || '-' || lpad(nextval('pr_number_seq')::text, 5, '0')),
    requester_id    TEXT NOT NULL REFERENCES users(id),
    department_id   TEXT NOT NULL REFERENCES departments(id),
    vendor_id       TEXT NOT NULL REFERENCES vendors(id),
    total           NUMERIC(14,2) NOT NULL CHECK (total > 0),
    currency        CHAR(3) NOT NULL DEFAULT 'USD',
    justification   TEXT NOT NULL,
    status          pr_status NOT NULL DEFAULT 'draft',
    approval_chain  TEXT[] NOT NULL DEFAULT '{}',   -- snapshot of OPA's chain at submit time
    idempotency_key TEXT UNIQUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX pr_requester_idx ON purchase_requests (requester_id, status);
CREATE INDEX pr_status_idx    ON purchase_requests (status, department_id);

CREATE TABLE pr_line_items (
    pr_id       TEXT NOT NULL REFERENCES purchase_requests(id) ON DELETE CASCADE,
    line_no     INT  NOT NULL,
    sku         TEXT NOT NULL,
    description TEXT NOT NULL,
    qty         INT  NOT NULL CHECK (qty > 0),
    unit_price  NUMERIC(14,2) NOT NULL CHECK (unit_price >= 0),
    PRIMARY KEY (pr_id, line_no)
);

CREATE TABLE approvals (
    pr_id       TEXT NOT NULL REFERENCES purchase_requests(id),
    approver_id TEXT NOT NULL REFERENCES users(id),
    level       TEXT NOT NULL CHECK (level IN ('manager', 'finance', 'admin')),
    decision    TEXT NOT NULL CHECK (decision IN ('approved', 'rejected')),
    comment     TEXT NOT NULL DEFAULT '',
    decided_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (pr_id, level),
    -- Separation of duties: one person signs at most one level of a request
    -- (finance may cover the manager level, but then someone else signs finance).
    UNIQUE (pr_id, approver_id)
);

-- Defense in depth: OPA and the service layer already forbid self-approval;
-- the database refuses it too, so a bug in either cannot slip through.
CREATE FUNCTION forbid_self_approval() RETURNS trigger AS $$
BEGIN
    IF NEW.approver_id = (SELECT requester_id FROM purchase_requests WHERE id = NEW.pr_id) THEN
        RAISE EXCEPTION 'self-approval is not allowed (pr %)', NEW.pr_id USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$ LANGUAGE plpgsql;

CREATE TRIGGER approvals_no_self BEFORE INSERT OR UPDATE ON approvals
    FOR EACH ROW EXECUTE FUNCTION forbid_self_approval();

-- Legal status transitions, enforced in the database (the service layer takes
-- SELECT ... FOR UPDATE row locks; this trigger is the backstop).
CREATE TABLE pr_transitions (
    from_status pr_status NOT NULL,
    to_status   pr_status NOT NULL,
    PRIMARY KEY (from_status, to_status)
);
INSERT INTO pr_transitions VALUES
    ('draft', 'pending_manager'), ('draft', 'pending_finance'), ('draft', 'approved'), ('draft', 'cancelled'),
    ('pending_manager', 'pending_finance'), ('pending_manager', 'approved'),
    ('pending_manager', 'rejected'), ('pending_manager', 'cancelled'),
    ('pending_finance', 'pending_admin'), ('pending_finance', 'approved'),
    ('pending_finance', 'rejected'), ('pending_finance', 'cancelled'),
    ('pending_admin', 'approved'), ('pending_admin', 'rejected'), ('pending_admin', 'cancelled'),
    ('approved', 'ordered'), ('approved', 'cancelled');

CREATE FUNCTION enforce_pr_transition() RETURNS trigger AS $$
BEGIN
    IF NEW.status IS DISTINCT FROM OLD.status AND NOT EXISTS (
        SELECT 1 FROM pr_transitions WHERE from_status = OLD.status AND to_status = NEW.status
    ) THEN
        RAISE EXCEPTION 'illegal PR transition % -> % (pr %)', OLD.status, NEW.status, OLD.id
            USING ERRCODE = 'check_violation';
    END IF;
    NEW.updated_at := now();
    RETURN NEW;
END $$ LANGUAGE plpgsql;

CREATE TRIGGER pr_status_transition BEFORE UPDATE ON purchase_requests
    FOR EACH ROW EXECUTE FUNCTION enforce_pr_transition();

-- ---------------------------------------------------------------------------
-- Human-in-the-loop confirmations for write tools
-- ---------------------------------------------------------------------------
CREATE TABLE action_confirmations (
    id           TEXT PRIMARY KEY,           -- proposal id shown on the ActionCard
    user_id      TEXT NOT NULL REFERENCES users(id),
    action       TEXT NOT NULL,              -- tool name
    payload      JSONB NOT NULL,
    payload_hash TEXT NOT NULL,              -- sha256 of canonical payload; must match at confirm time
    expires_at   TIMESTAMPTZ NOT NULL,
    token_hash   TEXT,                       -- sha256 of the one-time HMAC token (never the token)
    used_at      TIMESTAMPTZ,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- Audit & security (append-only)
-- ---------------------------------------------------------------------------
CREATE TABLE audit_log (
    id         BIGSERIAL PRIMARY KEY,
    at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    actor_id   TEXT,                         -- user id, or 'system'
    action     TEXT NOT NULL,
    target     TEXT,
    details    JSONB NOT NULL DEFAULT '{}',  -- always PII-masked
    trace_id   TEXT
);

CREATE FUNCTION forbid_mutation() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION '% is append-only', TG_TABLE_NAME USING ERRCODE = 'insufficient_privilege';
END $$ LANGUAGE plpgsql;

CREATE TRIGGER audit_log_append_only BEFORE UPDATE OR DELETE OR TRUNCATE ON audit_log
    FOR EACH STATEMENT EXECUTE FUNCTION forbid_mutation();

CREATE TABLE security_events (
    id           BIGSERIAL PRIMARY KEY,
    at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    user_id      TEXT,
    layer        TEXT NOT NULL,              -- regex | jev | prompt_guard | output | ingestion
    score        REAL,
    masked_input TEXT NOT NULL,
    trace_id     TEXT
);

CREATE TRIGGER security_events_append_only BEFORE UPDATE OR DELETE OR TRUNCATE ON security_events
    FOR EACH STATEMENT EXECUTE FUNCTION forbid_mutation();

CREATE TABLE notifications (
    id         BIGSERIAL PRIMARY KEY,
    at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    channel    TEXT NOT NULL,                -- 'email' | 'chat:<channel>'
    recipient  TEXT NOT NULL,
    subject    TEXT,
    body       TEXT NOT NULL,
    pr_id      TEXT REFERENCES purchase_requests(id)
);

-- ---------------------------------------------------------------------------
-- Document registry (ingestion). Masked chunk text lives in Qdrant; the
-- original is kept here encrypted so it never leaves Postgres in clear.
-- ---------------------------------------------------------------------------
CREATE TABLE index_versions (
    version     INT PRIMARY KEY,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    notes       TEXT NOT NULL DEFAULT '',
    is_active   BOOLEAN NOT NULL DEFAULT false
);
-- At most one active index version.
CREATE UNIQUE INDEX index_versions_one_active ON index_versions (is_active) WHERE is_active;

CREATE TABLE documents (
    id              TEXT PRIMARY KEY,        -- stable id derived from source path
    source          TEXT NOT NULL,           -- 'far' | 'worldbank' | 'un' | 'cuad' | 'acme'
    title           TEXT NOT NULL,
    path            TEXT NOT NULL,
    sha256          TEXT NOT NULL,
    department      TEXT NOT NULL,           -- department id or 'all'
    classification  TEXT NOT NULL CHECK (classification IN ('public', 'internal', 'confidential', 'restricted')),
    effective_date  DATE,
    index_version   INT REFERENCES index_versions(version),
    ingested_at     TIMESTAMPTZ
);

CREATE TABLE chunks (
    id               TEXT PRIMARY KEY,       -- '<doc_id>#<ord>'
    doc_id           TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    ord              INT  NOT NULL,
    section_path     TEXT NOT NULL,
    text_masked      TEXT NOT NULL,
    text_original_enc BYTEA NOT NULL,
    token_count      INT  NOT NULL,
    injection_flag   BOOLEAN NOT NULL DEFAULT false,
    injection_score  REAL,
    quarantined      BOOLEAN NOT NULL DEFAULT false,  -- high-risk: never upserted to Qdrant
    index_version    INT NOT NULL REFERENCES index_versions(version),
    UNIQUE (doc_id, ord)
);

-- ---------------------------------------------------------------------------
-- Evals
-- ---------------------------------------------------------------------------
CREATE TABLE eval_runs (
    run_id     TEXT PRIMARY KEY,
    git_sha    TEXT NOT NULL,
    suite      TEXT NOT NULL,                -- 'fast' | 'full'
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    metrics    JSONB NOT NULL,
    passed     BOOLEAN NOT NULL
);
