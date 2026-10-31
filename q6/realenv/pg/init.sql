-- 回補帳本（checkpoint 要可靠地另外存，不能只靠日誌；筆記 §13.5）與字典發版、影子比對紀錄。
-- GCP 上可放 Cloud SQL for PostgreSQL；本機用官方 postgres:16 映像。
CREATE TABLE IF NOT EXISTS backfill_ledger (
  partition_id   text PRIMARY KEY,            -- <channel>/<yyyy-mm>
  channel        text NOT NULL,
  month          text NOT NULL,
  target_seg_ver bigint NOT NULL,
  dict_version   text NOT NULL,
  status         text NOT NULL DEFAULT 'pending',   -- pending/running/awaiting_pm/passed/failed
  raw_count      integer,
  checkpoint_line integer NOT NULL DEFAULT 0,       -- 已確認寫進 ES 的最後一行（斷點續跑從這裡接）
  written        integer NOT NULL DEFAULT 0,
  noop           integer NOT NULL DEFAULT 0,
  errors         integer NOT NULL DEFAULT 0,
  worker         text,
  heartbeat_at   timestamptz,
  started_at     timestamptz,
  finished_at    timestamptz,
  docs_per_sec   numeric,
  gates          jsonb,
  attempts       integer NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS backfill_control (
  id int PRIMARY KEY DEFAULT 1,
  max_docs_per_sec integer NOT NULL DEFAULT 3000,     -- 回補限速；告警後由人或控制程式調低（筆記 §13.7）
  paused boolean NOT NULL DEFAULT false,
  updated_at timestamptz NOT NULL DEFAULT now(),
  reason text,
  canary_baseline_ms double precision                -- 發版前、沒有回補時量的探針 p95；G3 跟它比，不跟「剛剛」比（剛剛可能也在回補）
);
INSERT INTO backfill_control (id) VALUES (1) ON CONFLICT DO NOTHING;
CREATE TABLE IF NOT EXISTS backfill_batches (       -- 每個 bulk 一列：畫吞吐曲線用（日誌行數 ≠ 處理篇數）
  id bigserial PRIMARY KEY,
  partition_id text NOT NULL,
  task_index int NOT NULL,
  at timestamptz NOT NULL DEFAULT now(),
  docs int NOT NULL,
  written int NOT NULL,
  noop int NOT NULL,
  errors int NOT NULL,
  rejected int NOT NULL,
  seg_ms numeric NOT NULL,
  bulk_ms numeric NOT NULL
);
CREATE TABLE IF NOT EXISTS dict_releases (
  dict_version text PRIMARY KEY,
  seg_ver bigint NOT NULL,
  added_words text[],
  affected_estimate integer,
  rerun_scope text,
  released_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS shadow_checks (
  id bigserial PRIMARY KEY,
  at timestamptz NOT NULL DEFAULT now(),
  dict_version text NOT NULL,
  sampled int NOT NULL,
  mismatched int NOT NULL,
  examples jsonb
);
-- 與 ES 對照用：同一批 terms_v2 放進 PostgreSQL text[] + GIN（筆記 §8：PG 也有倒排索引）
CREATE TABLE IF NOT EXISTS posts_terms (
  post_id text PRIMARY KEY,
  channel text NOT NULL,
  published_at timestamptz,
  terms text[] NOT NULL
);
CREATE INDEX IF NOT EXISTS posts_terms_gin ON posts_terms USING gin (terms);
CREATE INDEX IF NOT EXISTS posts_terms_ch_time ON posts_terms (channel, published_at);
