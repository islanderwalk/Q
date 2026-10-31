-- 造資料（重現頁面的方式）：1,000 段不同的 250 字內文，每段連續重複 1,000 則。
-- 同一段內文的 1,000 則在建表時排在一起（頁面 page0 到 page124 即是同一段）。
-- 注意：純量子查詢若不與外層列相關，PG 會當成 InitPlan 只算一次，所以內文一律用
-- GROUP BY 或 LATERAL 逐列產生，避免 1,000 段內文全部相同。
-- 佔位符 __ROWS__ 由 run_q2.py 代入。

SELECT setseed(0.2026);

DROP TABLE IF EXISTS q2_pages;
CREATE TABLE q2_pages AS
SELECT g AS pidx,
       100000000000000 + g * 8388593 AS page_id,       -- 15 位數粉絲頁 ID，共 10,000 頁
       'fanpage-' || lpad(g::text, 5, '0') AS page_name
FROM generate_series(1::bigint, 10000::bigint) AS g;

-- 1,000 段內文，每段 250 字；每個字獨立呼叫 random()，取 CJK 統一漢字 U+4E00..U+9FFF
DROP TABLE IF EXISTS q2_src;
CREATE TABLE q2_src AS
SELECT x.tid, string_agg(x.ch, '' ORDER BY x.k) AS body
FROM (SELECT (k - 1) / 250 + 1 AS tid, k,
             chr(19968 + floor(random() * 20992)::int) AS ch
      FROM generate_series(1, 250000) AS k) AS x
GROUP BY x.tid;

INSERT INTO fb_posts (post_id, page_id, page_name, created_time, message,
                      like_cnt, love_cnt, wow_cnt, haha_cnt, sad_cnt, angry_cnt, care_cnt,
                      reactions_total, share_cnt)
SELECT p.page_id::text || '_' || (100000000000000 + floor(random() * 899999999999999))::bigint::text,
       p.page_id,
       p.page_name,
       timestamptz '2023-01-01 00:00:00+08' + random() * interval '1400 days',
       s.body,
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
JOIN q2_src   AS s ON s.tid  = 1 + ((r.g - 1) / 1000) % 1000
ORDER BY r.g;  -- 依 g 順序寫入，使同一段內文的 1,000 則落在相鄰位置（與頁面一致）

ANALYZE fb_posts;
