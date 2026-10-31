-- 三種索引。run_q2.py 以 "-- @step <名稱>" 切段，逐段計時、逐段建立。
-- bigram 放最後：它最慢，若超過 --bigram-timeout-min 就被取消（記錄為超時）。

-- @step btree_page_id
CREATE INDEX fb_posts_page_id_btree ON fb_posts USING btree (page_id);

-- @step simple_tsv
CREATE INDEX fb_posts_simple_gin ON fb_posts USING gin (to_tsvector('simple', message));

-- @step trgm
CREATE INDEX fb_posts_trgm_gin ON fb_posts USING gin (message gin_trgm_ops);

-- @step bigram
CREATE INDEX fb_posts_bigram_gin ON fb_posts USING gin (q2_bigrams(message));
