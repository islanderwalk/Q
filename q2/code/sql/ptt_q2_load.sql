-- PTT 文章 → Q2 粉絲頁貼文表（q2_ptt.fb_posts）
--
-- 來源：q2_ptt.ptt_articles（由 q2/code/ptt_q2_crawl.py 寫入）。本檔只讀 ptt_articles，不改它的結構和資料。
-- 欄位與 sql/01_schema.sql 相同；q2_bigrams() 也與 01_schema.sql 相同。
-- 可重跑：整段包在一個交易裡，先 TRUNCATE fb_posts 再全量重灌（爬蟲跑到一半也能隨時重灌），
-- 索引在灌完後才建（先建再灌慢很多）。失敗時交易回滾，fb_posts 維持原狀。
-- 執行：由 ptt_q2_verify.py 呼叫（它會先跑本檔再量測），也可在 WSL 以 root 直接跑：
--         psql -d q2_ptt -v ON_ERROR_STOP=1 -f q2/code/sql/ptt_q2_load.sql
--
-- 欄位對應（PTT 文章 → 粉絲頁貼文）：
--   page_id          看板名稱的固定 bigint（48 bit，永遠同一個值）：
--                    ('x' || substr(md5(board), 1, 12))::bit(48)::bigint
--                    PTT 沒有「粉絲頁」，這裡把每個看板當成一個粉絲頁。
--   page_name        = board（看板名稱）
--   post_id          = page_id || '_' || 文章代號（網址檔名去掉 .html，例：M.1700000000.A.123）
--   created_time     = published_at（爬蟲由網址裡的 epoch 換算的時間）
--   message          = body（爬蟲取得的內文；推文與 metaline 已被爬蟲移除，頁尾的「※ 發信站」等行仍保留）
--   like_cnt         = push_up（推）
--   angry_cnt        = push_down（噓）
--   love_cnt, wow_cnt, haha_cnt, sad_cnt, care_cnt = 0（PTT 沒有這些心情）
--   reactions_total  = push_count（所有推文則數，含 →）
--   share_cnt        = 0（PTT 沒有分享數）
--
-- 篩選：status = 200 且 body 不是空字串。另外 published_at 為 NULL 的列排除（created_time 是 NOT NULL，
--       且沒有可靠的替代時間）；被排除的數量由 ptt_q2_verify.py 印出。
-- 空值：push_up／push_down／push_count 為 NULL 時當 0。

BEGIN;

CREATE TABLE IF NOT EXISTS fb_posts (
    post_id          text        NOT NULL,  -- "{page_id}_{文章代號}"
    page_id          bigint      NOT NULL,  -- 粉絲頁 ID（btree 索引的對象）
    page_name        text        NOT NULL,  -- 粉絲頁名稱（PTT：看板名稱）
    created_time     timestamptz NOT NULL,  -- 發文時間
    message          text        NOT NULL,  -- 內文
    like_cnt         bigint      NOT NULL,
    love_cnt         bigint      NOT NULL,
    wow_cnt          bigint      NOT NULL,
    haha_cnt         bigint      NOT NULL,
    sad_cnt          bigint      NOT NULL,
    angry_cnt        bigint      NOT NULL,
    care_cnt         bigint      NOT NULL,
    reactions_total  bigint      NOT NULL,  -- 七種心情加總（PTT：推文則數）
    share_cnt        bigint      NOT NULL
);

-- 與 01_schema.sql 相同的兩字詞切法：每相鄰兩字一組，形成 text[]，再用 GIN(array_ops) 建倒排索引。
CREATE OR REPLACE FUNCTION q2_bigrams(t text) RETURNS text[]
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $f$
    SELECT COALESCE(array_agg(substr(t, i, 2)), ARRAY[]::text[])
    FROM generate_series(1, GREATEST(char_length(t) - 1, 0)) AS i
$f$;

TRUNCATE fb_posts;
DROP INDEX IF EXISTS fb_posts_page_id_btree;
DROP INDEX IF EXISTS fb_posts_bigram_gin;

INSERT INTO fb_posts (post_id, page_id, page_name, created_time, message,
                      like_cnt, love_cnt, wow_cnt, haha_cnt, sad_cnt, angry_cnt, care_cnt,
                      reactions_total, share_cnt)
SELECT (('x' || substr(md5(a.board), 1, 12))::bit(48)::bigint)::text
           || '_' || coalesce(substring(a.url FROM '([^/]+)\.html$'), a.url),
       ('x' || substr(md5(a.board), 1, 12))::bit(48)::bigint,
       a.board,
       a.published_at,
       a.body,
       coalesce(a.push_up, 0),
       0, 0, 0, 0,
       coalesce(a.push_down, 0),
       0,
       coalesce(a.push_count, 0),
       0
FROM ptt_articles AS a
WHERE a.status = 200
  AND a.body <> ''
  AND a.published_at IS NOT NULL
ORDER BY a.url;

-- 灌完才建索引：與 sql/03_indexes.sql 的兩個索引相同。
CREATE INDEX fb_posts_page_id_btree ON fb_posts USING btree (page_id);
CREATE INDEX fb_posts_bigram_gin ON fb_posts USING gin (q2_bigrams(message));

ANALYZE fb_posts;

COMMIT;
