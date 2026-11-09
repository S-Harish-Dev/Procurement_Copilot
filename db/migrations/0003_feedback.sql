-- 👍/👎 on chat answers. Text is PII-masked by the gateway before insert; rows become
-- eval candidates (reviewed by hand before entering qa.jsonl).
CREATE TABLE feedback (
    id          BIGSERIAL PRIMARY KEY,
    request_id  TEXT NOT NULL,
    user_id     TEXT NOT NULL REFERENCES users(id),
    role        user_role NOT NULL,
    rating      SMALLINT NOT NULL CHECK (rating IN (-1, 1)),
    question    TEXT NOT NULL DEFAULT '',
    answer      TEXT NOT NULL DEFAULT '',
    comment     TEXT NOT NULL DEFAULT '',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- One vote per user per answer; voting again replaces it.
    UNIQUE (request_id, user_id)
);
CREATE INDEX feedback_created_idx ON feedback (created_at DESC);
