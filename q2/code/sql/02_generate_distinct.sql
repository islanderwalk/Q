-- 造資料（敏感度測試）：每一列都是獨立的隨機 250 字內文，沒有重複。
-- 頁面的實測用的是「1,000 段內文重複」，GIN 只要記連續位址的差值就能壓縮，
-- 所以真實資料（每則內文都不同）的索引體積應該比頁面數字大。這份腳本量那個情況。
-- 內文用 LATERAL 子查詢，其中引用外層列 r.g，使每一列都重新呼叫 random()（不會被快取成同一段）。
-- 佔位符 __ROWS__ 由 run_q2.py 代入。

SELECT setseed(0.2026);

DROP TABLE IF EXISTS q2_pages;
CREATE TABLE q2_pages AS
SELECT g AS pidx,
       100000000000000 + g * 8388593 AS page_id,
       'fanpage-' || lpad(g::text, 5, '0') AS page_name
FROM generate_series(1::bigint, 10000::bigint) AS g;

INSERT INTO fb_posts (post_id, page_id, page_name, created_time, message,
                      like_cnt, love_cnt, wow_cnt, haha_cnt, sad_cnt, angry_cnt, care_cnt,
                      reactions_total, share_cnt)
SELECT p.page_id::text || '_' || (100000000000000 + floor(random() * 899999999999999))::bigint::text,
       p.page_id,
       p.page_name,
       timestamptz '2023-01-01 00:00:00+08' + random() * interval '1400 days',
       m.body,
       r.like_cnt, r.love_cnt, r.wow_cnt, r.haha_cnt, r.sad_cnt, r.angry_cnt, r.care_cnt,
       r.like_cnt + r.love_cnt + r.wow_cnt + r.haha_cnt + r.sad_cnt + r.angry_cnt + r.care_cnt,
       floor(random() * 500)::bigint
FROM (SELECT g,
             floor(random() * 3000)::bigint AS like_cnt,
             floor(random() * 300)::bigint  AS love_cnt,
             floor(random() * 100)::bigint  AS wow_cnt,
             floor(random() * 80)::bigint   AS haha_cnt,
             floor(random() * 40)::bigint   AS sad_cnt,
             floor(random() * 30)::bigint   AS angry_cnt,
             floor(random() * 20)::bigint   AS care_cnt
      FROM generate_series(1::bigint, __ROWS__::bigint) AS g) AS r
JOIN q2_pages AS p ON p.pidx = 1 + (r.g * 7919) % 10000
CROSS JOIN LATERAL (
    SELECT string_agg(chr(19968 + floor(random() * 20992)::int), '') AS body
    FROM generate_series(1, 250 + 0 * r.g::int) AS k
) AS m
ORDER BY r.g;  -- 依 g 順序寫入

ANALYZE fb_posts;
