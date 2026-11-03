-- QSearch Q2：粉絲頁貼文表（Postgres 16）
-- 欄位依題目：粉絲頁 ID、粉絲頁名稱、發文時間、內文（平均 250 字中文）、心情互動統計。
-- 欄位型別選 bigint 讓每列約 920 B，與 q2/answer/fb_q2_posts_table.html 的 lp_len 918 B 同級，
-- 每頁 8 列（8 KB page），和頁面的 125,000 頁一致。

DROP TABLE IF EXISTS fb_posts;

CREATE TABLE fb_posts (
    post_id          text        NOT NULL,  -- "{page_id}_{15 位隨機數}"，31 字元（見 02_generate_*.sql）
    page_id          bigint      NOT NULL,  -- 粉絲頁 ID（btree 索引的對象）
    page_name        text        NOT NULL,  -- 粉絲頁名稱
    created_time     timestamptz NOT NULL,  -- 發文時間
    message          text        NOT NULL,  -- 內文，平均 250 個中文字
    like_cnt         bigint      NOT NULL,
    love_cnt         bigint      NOT NULL,
    wow_cnt          bigint      NOT NULL,
    haha_cnt         bigint      NOT NULL,
    sad_cnt          bigint      NOT NULL,
    angry_cnt        bigint      NOT NULL,
    care_cnt         bigint      NOT NULL,
    reactions_total  bigint      NOT NULL,  -- 七種心情加總
    share_cnt        bigint      NOT NULL
);

-- 兩字詞切法（bigram）：內文每相鄰兩字一組，形成 text[]，再用 GIN 建倒排索引。
-- pg_bigm 不在 EDB 的 Windows 發行版內，這裡以 GIN(array_ops) 重現相同的索引結構：
-- key = 兩字詞，posting list = 含該詞的列位址。
CREATE OR REPLACE FUNCTION q2_bigrams(t text) RETURNS text[]
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $f$
    SELECT COALESCE(array_agg(substr(t, i, 2)), ARRAY[]::text[])
    FROM generate_series(1, GREATEST(char_length(t) - 1, 0)) AS i
$f$;
