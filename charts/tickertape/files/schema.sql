-- Idempotent: safe to re-run (init Job, docker compose init, manual psql).

CREATE TABLE IF NOT EXISTS items (
  id            BIGSERIAL PRIMARY KEY,
  uid           TEXT UNIQUE NOT NULL,        -- sha256(guid or link)
  source        TEXT NOT NULL,
  title         TEXT NOT NULL,
  summary       TEXT,
  link          TEXT,
  published_at  TIMESTAMPTZ,
  status        TEXT NOT NULL DEFAULT 'new', -- new | ner_done | laya_done | error
  error         TEXT,
  attempts      INT NOT NULL DEFAULT 0,
  entities      JSONB,                       -- {"COMPANY": [...], "TICKER": [...], ...}
  focus_ticker  TEXT,
  decisions     JSONB,                       -- raw Laya answers
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  ner_done_at   TIMESTAMPTZ,
  laya_done_at  TIMESTAMPTZ,
  ner_ms        INT,
  laya_ms       INT
);
-- Added after the first deploy: every NER span with its score (floor 0.3), so the 0.5 entity threshold can be
-- tuned offline. Shape: [{"label": "COMPANY", "text": "NVIDIA", "score": 0.991, "start": 0, "end": 6}, ...]
ALTER TABLE items ADD COLUMN IF NOT EXISTS ner_spans JSONB;

CREATE INDEX IF NOT EXISTS items_status_created_idx ON items (status, created_at);

-- Flattened Laya outputs for easy Grafana SQL
CREATE TABLE IF NOT EXISTS decisions (
  item_id     BIGINT REFERENCES items(id),
  question    TEXT,
  answer      TEXT,
  confidence  REAL,
  model_rev   TEXT,
  created_at  TIMESTAMPTZ DEFAULT now(),
  PRIMARY KEY (item_id, question)
);

-- Conditional-GET state for the poller (one row per fetched feed URL key)
CREATE TABLE IF NOT EXISTS feed_state (
  feed_key       TEXT PRIMARY KEY,           -- feed name, or "name:TICKER" for per_ticker feeds
  etag           TEXT,
  last_modified  TEXT,
  updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
