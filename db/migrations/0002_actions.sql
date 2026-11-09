-- 0002_actions: support for the confirmed-action flow.

-- The exact canonical JSON text whose sha256 is payload_hash. Hashing JSONB would depend on how
-- each language re-serializes numbers and key order; hashing stored text does not.
ALTER TABLE action_confirmations
    ADD COLUMN payload_canonical TEXT,
    ADD COLUMN summary JSONB NOT NULL DEFAULT '{}',
    ADD COLUMN status TEXT NOT NULL DEFAULT 'proposed'
        CHECK (status IN ('proposed', 'confirmed', 'executed', 'failed', 'cancelled', 'expired')),
    ADD COLUMN result JSONB,
    ADD COLUMN executed_at TIMESTAMPTZ;

CREATE INDEX action_confirmations_user_idx ON action_confirmations (user_id, created_at DESC);

-- Generated purchase-order PDFs (file lives on the shared volume; the row records who/when).
CREATE TABLE po_documents (
    pr_id        TEXT PRIMARY KEY REFERENCES purchase_requests(id),
    path         TEXT NOT NULL,
    sha256       TEXT NOT NULL,
    generated_by TEXT NOT NULL REFERENCES users(id),
    generated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
