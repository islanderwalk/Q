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
  attempts       integer NOT NULL DEFAULT 0,
  raw_lines      integer                            -- 原文檔原始行數；raw_count 是不重複 post_id 數（G1 比這個）
);
CREATE TABLE IF NOT EXISTS backfill_control (
  id int PRIMARY KEY DEFAULT 1,
  max_docs_per_sec integer NOT NULL DEFAULT 3000,     -- 回補限速；告警後由人或控制程式調低（筆記 §13.7）
  paused boolean NOT NULL DEFAULT false,
  updated_at timestamptz NOT NULL DEFAULT now(),
  reason text,
  canary_baseline_ms double precision,               -- 發版前、沒有回補時量的探針 p95；G3 跟它比，不跟「剛剛」比（剛剛可能也在回補）
  ceiling_docs_per_sec integer NOT NULL DEFAULT 3000 -- G3 自動降速後允許回升的上限（PM 頁手動設限速時一起設）
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
CREATE INDEX IF NOT EXISTS backfill_batches_at_brin ON backfill_batches USING brin (at);   -- 吞吐面板按時間切，不要全表掃
-- 完整性四層對帳（seg/qseg/recon.py 每小時寫；day＋channel＋month 一列，當天覆蓋）
CREATE TABLE IF NOT EXISTS recon_daily (
  day date NOT NULL,
  channel text NOT NULL,
  month text NOT NULL,
  raw_docs integer,                                  -- L1 原文湖不重複 post_id（_manifest.json；沒有這個分區＝NULL）
  es_docs integer NOT NULL,                          -- L2 ES 篇數（含 tombstone）
  es_with_v2 integer NOT NULL,                       -- L3 有 terms_v2
  es_at_latest_v2 integer NOT NULL,                  -- L4 在最新 seg_ver_v2
  es_deleted integer NOT NULL DEFAULT 0,
  ledger_written integer,
  ledger_noop integer,
  ledger_status text,
  latest_seg_ver_v2 bigint,
  missing_ratio numeric,                             -- max(0, raw −（es − 已刪）) ÷ raw
  extra_docs integer,                                -- max(0,（es − 已刪）− raw)
  computed_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (day, channel, month)
);
-- 文字雲品質日報（seg/qseg/quality.py 每天寫；channel='(all)' 一列放整詞召回、金標、McNemar、新詞暴增）
CREATE TABLE IF NOT EXISTS quality_daily (
  day date NOT NULL,
  channel text NOT NULL,
  dict_version text,
  sample_window text,                                -- 抽樣視窗（QUALITY_WINDOW，例如 1d）；window 是 PostgreSQL 保留字
  docs_sampled integer,
  leak_url numeric, leak_phone numeric, leak_number numeric, leak_stopword numeric, must_keep_split numeric,
  place_docs integer, place_split integer, place_split_ratio numeric,
  whole_word_recall numeric, whole_word_words integer, whole_word_worst jsonb,
  gold_recall jsonb, mcnemar jsonb, surges jsonb,
  top_v2 jsonb, top_v1 jsonb, rbo_prev numeric, js_prev numeric, rbo_v1v2 numeric,
  computed_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (day, channel)
);
CREATE TABLE IF NOT EXISTS dict_releases (
  dict_version text PRIMARY KEY,
  seg_ver bigint NOT NULL,
  added_words text[],
  affected_estimate integer,
  rerun_scope text,
  released_at timestamptz NOT NULL DEFAULT now()
);
-- P0-3：回補帳本的對帳缺口（passed 分區應為 0；gap≠0 代表上游原文數與 ES 寫入（written＋noop）對不上；raw_unknown＝上游原文數未記錄，gap 為 NULL）
CREATE OR REPLACE VIEW backfill_gap AS
  SELECT partition_id, channel, month, status, raw_count, written, noop, raw_count - (written + noop) AS gap,
         (raw_count IS NULL) AS raw_unknown
  FROM backfill_ledger;
-- P0-5：實際重跑寫入數（rerun.py）、加詞影響範圍的地面真值漏抓數（impact.py --record）
-- 注意：initdb.d 只在第一次建立 volume 時執行；既有資料庫請跑 pg/migrate_p0.sql（可重複執行）
ALTER TABLE dict_releases ADD COLUMN IF NOT EXISTS rerun_written integer;
ALTER TABLE dict_releases ADD COLUMN IF NOT EXISTS bigram_audit_missed integer;
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
