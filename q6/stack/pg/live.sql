-- 即時操作（PM 頁的「資料工程端」）：字典發版 → 斷詞服務重載 → 定向重跑的進度、各斷詞服務目前載入的字典、操作事件流。
-- 首次建庫由 docker-entrypoint-initdb.d 載入；既有資料庫由 API 與斷詞服務啟動時再執行一次（全部 IF NOT EXISTS）。
CREATE TABLE IF NOT EXISTS rerun_runs (
  id bigserial PRIMARY KEY,
  dict_version text NOT NULL,
  seg_ver bigint NOT NULL,
  words text[] NOT NULL,
  requested_by text,
  status text NOT NULL DEFAULT 'queued',      -- queued / loading / running / done / failed / skipped
  worker text,
  candidates integer,                         -- ES 片語比對估出的候選篇數
  scanned integer NOT NULL DEFAULT 0,
  terms_changed integer NOT NULL DEFAULT 0,
  written integer NOT NULL DEFAULT 0,
  noop integer NOT NULL DEFAULT 0,
  errors integer NOT NULL DEFAULT 0,
  seconds numeric,
  examples jsonb,
  error text,
  queued_at timestamptz NOT NULL DEFAULT now(),
  started_at timestamptz,
  finished_at timestamptz,
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS service_state (
  service text PRIMARY KEY,                   -- <pipeline>@<hostname>
  pipeline text NOT NULL,
  dict_version text NOT NULL,
  seg_ver bigint NOT NULL,
  build text,
  loaded_at timestamptz NOT NULL DEFAULT now(),
  heartbeat_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS release_outbox (     -- 發版控制訊息的 outbox：先寫這裡再切 current；發不出去背景每 10 秒重送
  id bigserial PRIMARY KEY,
  dict_version text NOT NULL,
  run_id bigint,
  payload jsonb NOT NULL,
  status text NOT NULL DEFAULT 'pending',     -- pending / sent
  attempts integer NOT NULL DEFAULT 0,
  last_error text,
  message_id text,
  created_at timestamptz NOT NULL DEFAULT now(),
  sent_at timestamptz
);
CREATE TABLE IF NOT EXISTS live_events (
  id bigserial PRIMARY KEY,
  at timestamptz NOT NULL DEFAULT now(),
  kind text NOT NULL,                         -- release / approve / waive / publish / collect / …
  actor text,
  detail jsonb
);
CREATE INDEX IF NOT EXISTS live_events_at ON live_events (at DESC);
-- 真實來源採集器（PTT 看板 Atom、YouTube 頻道 RSS、中央社／公視 RSS）：每個 feed 的最近一次抓取狀態與條件式請求標頭
CREATE TABLE IF NOT EXISTS collector_state (
  feed text PRIMARY KEY,                      -- feeds.json 的 name，例如 ptt/Gossiping
  channel text NOT NULL,
  source text,
  url text NOT NULL,
  last_poll_at timestamptz,
  last_status integer,                        -- HTTP 狀態碼（304 = 沒變）
  items_seen integer,
  last_new integer NOT NULL DEFAULT 0,
  total_new integer NOT NULL DEFAULT 0,
  etag text,
  last_modified text,
  error text,
  next_poll_at timestamptz                    -- 被 429／5xx 退避時往後推
);
-- 計畫書 P0 第 3 項：成功時間、退避等級、outcome 與延遲要留在表裡（失敗不再更新 last_ok_at；重啟後接著退避）
ALTER TABLE collector_state ADD COLUMN IF NOT EXISTS last_ok_at timestamptz;
ALTER TABLE collector_state ADD COLUMN IF NOT EXISTS backoff_level integer NOT NULL DEFAULT 0;
ALTER TABLE collector_state ADD COLUMN IF NOT EXISTS outcome text;              -- ok／empty／not_modified／login_wall／content_unavailable／rate_limited／forbidden／server_error／network_error／parse_error／skipped
ALTER TABLE collector_state ADD COLUMN IF NOT EXISTS final_url text;            -- 有轉址才有（登入牆看這裡）
ALTER TABLE collector_state ADD COLUMN IF NOT EXISTS posts_parsed integer;
ALTER TABLE collector_state ADD COLUMN IF NOT EXISTS ttfb_ms numeric;
ALTER TABLE collector_state ADD COLUMN IF NOT EXISTS total_ms numeric;
ALTER TABLE collector_state ADD COLUMN IF NOT EXISTS robots text;               -- allow／disallow／unknown（只看 User-agent: * 的 Disallow）
CREATE TABLE IF NOT EXISTS collector_robots (
  host text PRIMARY KEY,
  sha text NOT NULL,                          -- robots.txt 內容 sha256 前 16 碼；"none"＝沒有 robots.txt
  fetched_at timestamptz NOT NULL DEFAULT now(),
  changed_at timestamptz
);
CREATE TABLE IF NOT EXISTS collector_control (
  id int PRIMARY KEY DEFAULT 1,
  run_now boolean NOT NULL DEFAULT false,     -- PM 頁「立刻抓一次」
  paused boolean NOT NULL DEFAULT false,
  poll_sec integer NOT NULL DEFAULT 300,
  updated_at timestamptz NOT NULL DEFAULT now()
);
INSERT INTO collector_control (id) VALUES (1) ON CONFLICT DO NOTHING;
