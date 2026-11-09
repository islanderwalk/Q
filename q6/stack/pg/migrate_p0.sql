-- P0 既有資料庫的遷移：pg/init.sql 只在第一次建立 volume 時由 initdb.d 執行，舊庫要手動套用這支。可重複執行。
-- 用法（未驗證：本次未啟動 docker）：docker compose exec -T pg psql -U qsearch -d qsearch -v ON_ERROR_STOP=1 < pg/migrate_p0.sql
-- 內容必須與 init.sql 的 P0 段落一致（測試 test_migrate_p0_sql_is_idempotent_and_matches_init_view 會檢查關鍵字）。
BEGIN;
ALTER TABLE backfill_ledger ADD COLUMN IF NOT EXISTS raw_lines integer;
ALTER TABLE backfill_control ADD COLUMN IF NOT EXISTS ceiling_docs_per_sec integer NOT NULL DEFAULT 3000;
CREATE INDEX IF NOT EXISTS backfill_batches_at_brin ON backfill_batches USING brin (at);
CREATE TABLE IF NOT EXISTS recon_daily (
  day date NOT NULL, channel text NOT NULL, month text NOT NULL,
  raw_docs integer, es_docs integer NOT NULL, es_with_v2 integer NOT NULL, es_at_latest_v2 integer NOT NULL, es_deleted integer NOT NULL DEFAULT 0,
  ledger_written integer, ledger_noop integer, ledger_status text, latest_seg_ver_v2 bigint, missing_ratio numeric, extra_docs integer,
  computed_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY (day, channel, month)
);
CREATE TABLE IF NOT EXISTS quality_daily (
  day date NOT NULL, channel text NOT NULL, dict_version text, sample_window text, docs_sampled integer,
  leak_url numeric, leak_phone numeric, leak_number numeric, leak_stopword numeric, must_keep_split numeric,
  place_docs integer, place_split integer, place_split_ratio numeric,
  whole_word_recall numeric, whole_word_words integer, whole_word_worst jsonb, gold_recall jsonb, mcnemar jsonb, surges jsonb,
  top_v2 jsonb, top_v1 jsonb, rbo_prev numeric, js_prev numeric, rbo_v1v2 numeric,
  computed_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY (day, channel)
);
ALTER TABLE quality_daily ADD COLUMN IF NOT EXISTS split_examples jsonb;
ALTER TABLE dict_releases ADD COLUMN IF NOT EXISTS rerun_written integer;
ALTER TABLE dict_releases ADD COLUMN IF NOT EXISTS bigram_audit_missed integer;
CREATE OR REPLACE VIEW backfill_gap AS
  SELECT partition_id, channel, month, status, raw_count, written, noop, raw_count - (written + noop) AS gap,
         (raw_count IS NULL) AS raw_unknown
  FROM backfill_ledger;
COMMIT;
