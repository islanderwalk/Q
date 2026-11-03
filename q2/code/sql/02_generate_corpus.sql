-- 造資料（中文語料）：內文來自 q2_corpus（run_q2.py 以 COPY 從 prepare_corpus.py 的輸出載入，一行一則）。
-- 其餘欄位與 repeat／distinct 相同。列數多於語料則數時循環使用（RESULTS.md 會註明）。
-- 佔位符 __ROWS__、__CORPUS_ROWS__ 由 run_q2.py 代入。

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
JOIN q2_pages  AS p ON p.pidx = 1 + (r.g * 7919) % 10000
JOIN q2_corpus AS s ON s.tid  = 1 + (r.g - 1) % __CORPUS_ROWS__
ORDER BY r.g;  -- 依 g 順序寫入；語料檔已先打散，相鄰列不會來自同一篇來源

ANALYZE fb_posts;
