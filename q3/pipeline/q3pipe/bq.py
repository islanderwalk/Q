"""BigQuery：建表、暫存表＋MERGE 寫入、整批可還原（rollback）、各種查詢。

資料表（dataset ptt）
  posts          一篇文章一列，留言是巢狀欄位 comments（REPEATED RECORD），依發文日分區、依文章 ID 叢集。
                 留言跟著文章存：重抓時整篇（含留言）一次 MERGE，不用另一張大表做留言去重的 JOIN，
                 還原時 before-image 也自然包含留言。另存一份留言純文字 comments_text（每則一行）：
                 搜尋先用它找出留言命中的文章，再只取出這些文章裡命中的留言。
  list_slots     列表頁每一格的快照（頁碼、格子、文章 ID 或刪文佔位、推文數），不變量檢查用。
  post_versions  每批寫入前的 before-image（還原用，也是稽核紀錄）。
  batch_log      每批的結局：committed／rolled_back。沒有結局的批次＝中途當掉，下一輪開始前先還原。
  crawl_runs、verify_runs  每一輪抓取、每一次不變量檢查的結果。

寫入協定（每批文章）：
  1. 這批文章（含留言）先寫進專用暫存表；暫存失敗就什麼都沒動。
  2. 把這批文章「寫入前的樣子」存進 post_versions。
  3. MERGE 進 posts：同一篇再抓就更新（留言整串換成最新、每則留言保留第一次看到的時間），404 寫 deleted_at、保留舊內容。
  4. 核對這批是否全部落地，寫 batch_log＝committed；任何一步失敗就照 before-image 還原這批。
整輪的回歸檢查不過，就照批次倒序還原整輪，資料回到這輪開始前的樣子。
沒有依賴多語句交易：本機模擬器的交易不是原子的（實測中途出錯會留下前半段），所以還原做在應用層，
正式 BigQuery 與模擬器跑的是同一套 SQL 與流程。大表之間的 JOIN 也改在程式裡做（模擬器底層是 SQLite，
沒有索引，兩張千列以上的表 JOIN 會慢到不能用）；正式 BigQuery 也一樣只是單表掃描。
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone

from google.api_core import exceptions as gexc
from google.cloud import bigquery

S = bigquery.SchemaField

COMMENT_FIELDS = [
    ("floor", "INT64"), ("tag", "STRING"), ("user_id", "STRING"), ("content", "STRING"), ("ip", "STRING"),
    ("time_text", "STRING"), ("comment_at", "TIMESTAMP"), ("dup_n", "INT64"), ("first_seen_at", "TIMESTAMP"),
]
POST_COLS = [
    ("aid", "STRING"), ("board", "STRING"), ("url", "STRING"), ("title", "STRING"), ("author", "STRING"),
    ("published_at", "TIMESTAMP"), ("content", "STRING"), ("push", "INT64"), ("boo", "INT64"), ("arrow", "INT64"),
    ("comment_count", "INT64"), ("truncated", "BOOL"), ("html_bytes", "INT64"), ("raw_uri", "STRING"),
    ("status", "INT64"), ("comments", "COMMENTS"), ("comments_text", "STRING"), ("deleted_at", "TIMESTAMP"),
    ("first_fetched_at", "TIMESTAMP"),
    ("last_fetched_at", "TIMESTAMP"), ("fetch_count", "INT64"), ("last_run_id", "STRING"), ("last_batch_no", "INT64"),
]
# 暫存表多帶這批的 run_id／batch_no：MERGE 的 INSERT VALUES 不用查詢參數（模擬器不會代入），兩邊都用欄位
POST_STAGE_COLS = POST_COLS[:17] + [("fetched_at", "TIMESTAMP"), ("run_id", "STRING"), ("batch_no", "INT64")]
SLOT_COLS = [
    ("board", "STRING"), ("page_no", "INT64"), ("slot", "INT64"), ("aid", "STRING"), ("ts", "INT64"), ("title", "STRING"),
    ("author", "STRING"), ("nrec", "INT64"), ("deleted", "BOOL"), ("url", "STRING"), ("page_full", "BOOL"),
    ("snapshot_at", "TIMESTAMP"), ("raw_uri", "STRING"),
]
VERSION_COLS = [("run_id", "STRING"), ("batch_no", "INT64"), ("saved_at", "TIMESTAMP"), ("existed", "BOOL")] + POST_COLS
BATCH_LOG_COLS = [("run_id", "STRING"), ("batch_no", "INT64"), ("state", "STRING"), ("logged_at", "TIMESTAMP"), ("posts", "INT64"),
                  ("comments", "INT64"), ("note", "STRING")]
RUN_COLS = [
    ("run_id", "STRING"), ("board", "STRING"), ("started_at", "TIMESTAMP"), ("finished_at", "TIMESTAMP"), ("status", "STRING"),
    ("list_pages", "INT64"), ("new_posts", "INT64"), ("refreshed", "INT64"), ("deleted_found", "INT64"), ("failures", "INT64"),
    ("requests", "INT64"), ("batches", "INT64"), ("rolled_back_batches", "INT64"), ("gate_blocked", "INT64"), ("note", "STRING"),
]
VERIFY_COLS = [
    ("run_at", "TIMESTAMP"), ("board", "STRING"), ("window_start", "TIMESTAMP"), ("pages_checked", "INT64"),
    ("pages_not_20", "INT64"), ("missing_ids", "INT64"), ("out_of_order_pairs", "INT64"), ("nrec_checked", "INT64"),
    ("nrec_agree_ratio", "FLOAT64"), ("ok", "BOOL"), ("details", "STRING"),
]
TABLES = {
    # 名稱: (欄位, 分區欄位, 叢集欄位)
    "posts": (POST_COLS, "published_at", ["aid"]),
    "list_slots": (SLOT_COLS, None, ["board", "page_no"]),
    "post_versions": (VERSION_COLS, "saved_at", ["run_id", "aid"]),
    "batch_log": (BATCH_LOG_COLS, "logged_at", None),  # AT 是 GoogleSQL 保留字，欄位不用 at
    "crawl_runs": (RUN_COLS, "started_at", None),
    "verify_runs": (VERIFY_COLS, "run_at", None),
}
POST_NAMES = [n for n, _ in POST_COLS]


def _field(name, typ, required=False):
    if typ == "COMMENTS":
        return S(name, "RECORD", mode="REPEATED", fields=[S(n, t) for n, t in COMMENT_FIELDS])
    return S(name, typ, mode="REQUIRED" if required else "NULLABLE")


def _schema(cols, required=()):
    return [_field(n, t, n in required) for n, t in cols]


def _jsonable(v):
    if isinstance(v, datetime):
        return v.astimezone(timezone.utc).isoformat()
    if isinstance(v, list):
        return [_jsonable(x) for x in v]
    if isinstance(v, dict):
        return {k: _jsonable(x) for k, x in v.items()}
    return v


def _param(name, v):
    if isinstance(v, (list, tuple)):
        sample = next((x for x in v if x is not None), "")
        typ = "INT64" if isinstance(sample, int) and not isinstance(sample, bool) else "STRING"
        return bigquery.ArrayQueryParameter(name, typ, list(v))
    if isinstance(v, bool):
        return bigquery.ScalarQueryParameter(name, "BOOL", v)
    if isinstance(v, int):
        return bigquery.ScalarQueryParameter(name, "INT64", v)
    if isinstance(v, float):
        return bigquery.ScalarQueryParameter(name, "FLOAT64", v)
    if isinstance(v, datetime):
        return bigquery.ScalarQueryParameter(name, "TIMESTAMP", v)
    return bigquery.ScalarQueryParameter(name, "STRING", v)


def _chunks(payload, max_rows=500, max_bytes=2_000_000):
    """串流寫入每次最多 500 列、約 2 MB。模擬器實測單次請求超過約 3.5 MB 會解析失敗（熱門文留言多，一批幾篇就到），
    正式 BigQuery 的上限是 10 MB；單篇本身超過上限就自己一個請求。"""
    chunk, size = [], 0
    for r in payload:
        n = len(json.dumps(r))
        if chunk and (len(chunk) >= max_rows or size + n > max_bytes):
            yield chunk
            chunk, size = [], 0
        chunk.append(r)
        size += n
    if chunk:
        yield chunk


def page_ranges(pages):
    """[1,2,3,7,8,10] → [(1,3),(7,8),(10,10)]"""
    out = []
    for p in sorted(set(pages)):
        if out and p == out[-1][1] + 1:
            out[-1] = (out[-1][0], p)
        else:
            out.append((p, p))
    return out


def comments_text(comments):
    """留言純文字：每則一行，搜尋用。換行把每則隔開，關鍵字不會跨兩則留言命中。"""
    return "\n".join(c["content"] for c in comments) if comments else None


def comment_key(c):
    """同一則留言的識別：帳號＋時間（含 IP）＋內容＋同一分鐘同一句話的第幾次。"""
    return (c["user_id"], c["time_text"], c["content"], c["dup_n"])


class Warehouse:
    def __init__(self, cfg, client=None):
        self.cfg = cfg
        self.emulator = bool(cfg.bq_endpoint)
        if client is not None:
            self.c = client
        elif self.emulator:  # 本機 BigQuery 模擬器
            from google.api_core.client_options import ClientOptions
            from google.auth.credentials import AnonymousCredentials
            self.c = bigquery.Client(project=cfg.project, credentials=AnonymousCredentials(),
                                     client_options=ClientOptions(api_endpoint=cfg.bq_endpoint))
        else:
            self.c = bigquery.Client(project=cfg.project)
        self.t = cfg.table
        self.bytes_processed = 0
        self._staged = set()

    # ---------- 基本 ----------
    def q(self, sql, **params):
        cfg = bigquery.QueryJobConfig(query_parameters=[_param(k, v) for k, v in params.items()])
        if not self.emulator:
            job = self.c.query(sql, job_config=cfg)
            rows = list(job.result())
            self.bytes_processed += job.total_bytes_processed or 0
            return rows
        # 模擬器會把每個查詢的工作紀錄（連同結果）存起來，用 jobs.insert 送的 SELECT 還會各多建一個存結果的
        # dataset；之後每個請求都先把這些全部讀出來，實測每多 100 個查詢，每個請求就慢約 1 秒，跑一天就慢到不能用。
        # 所以：讀取走 jobs.query（不建結果 dataset）；寫入照樣走 jobs.insert（工作 ID 由用戶端產生，
        # 連線中斷重送也不會重複寫入）；查完記下掃描量，就刪掉這筆工作紀錄。
        if sql.lstrip().upper().startswith(("SELECT", "WITH")):
            it = self.c.query_and_wait(sql, job_config=cfg)
            rows, job_id, scanned = list(it), it.job_id, None
        else:
            job = self.c.query(sql, job_config=cfg)
            rows, job_id, scanned = list(job.result()), job.job_id, job.total_bytes_processed
        if job_id:
            try:
                if scanned is None:
                    scanned = self.c.get_job(job_id).total_bytes_processed
                self.c.delete_job_metadata(job_id, not_found_ok=True)
            except gexc.GoogleAPICallError:
                pass  # 只少記一筆掃描量或留下一筆紀錄，不影響資料；下次啟動時會清掉
        self.bytes_processed += scanned or 0
        return rows

    def purge_jobs(self):
        """模擬器：啟動時清掉之前留下的查詢工作紀錄（例如查到一半程序被停掉），回傳清掉幾筆。"""
        if not self.emulator:
            return 0
        n = 0
        for j in list(self.c.list_jobs(max_results=100000)):
            try:
                self.c.delete_job_metadata(j.job_id, not_found_ok=True)
                n += 1
            except gexc.GoogleAPICallError:
                pass
        return n

    def _create_once(self, get, create):
        """先查再建。模擬器對已存在的 dataset／表回 500（internalError: ... already created）而不是 409，
        exists_ok 接不到，用戶端的預設重試會一直重送到逾時；所以已存在就不送建立請求，萬一撞到也當成已存在。
        已存在就回傳查到的物件，剛建好回傳 None。"""
        try:
            return get()
        except gexc.NotFound:
            pass
        try:
            create()
        except gexc.GoogleAPICallError as e:
            if "already created" not in str(e) and "Already Exists" not in str(e):
                raise
        return None

    def ensure(self):
        ds = f"{self.cfg.project}.{self.cfg.dataset}"
        self._create_once(lambda: self.c.get_dataset(ds),
                          lambda: self.c.create_dataset(ds, exists_ok=True, retry=None))
        for name, (cols, part, cluster) in TABLES.items():
            t = bigquery.Table(self.cfg.table_id(name), schema=_schema(cols, required=("aid",) if name == "posts" else ()))
            if part:
                t.time_partitioning = bigquery.TimePartitioning(
                    type_=bigquery.TimePartitioningType.DAY, field=part,
                    expiration_ms=self.cfg.partition_expire_days * 86400 * 1000)
            if cluster:
                t.clustering_fields = cluster
            have = self._create_once(lambda: self.c.get_table(t.reference),
                                     lambda: self.c.create_table(t, exists_ok=True, retry=None))
            if have is not None:
                self._add_columns(name, have, t.schema)

    def _add_columns(self, name, table, schema):
        """程式改版加了欄位：BigQuery 直接加（新欄位都是 NULLABLE，舊資料讀成 NULL）。
        模擬器的加欄位只改得到表的描述，SQL 看不到新欄位，所以直接停下來說明要清掉重建。"""
        names = {f.name for f in table.schema}
        missing = [f for f in schema if f.name not in names]
        if not missing:
            return
        if self.emulator:
            raise RuntimeError(f"{name} 少了欄位 {', '.join(f.name for f in missing)}；模擬器不支援加欄位，"
                               "請清掉資料重建（docker compose down -v）")
        table.schema = list(table.schema) + missing
        self.c.update_table(table, ["schema"])

    def insert_rows(self, name, rows):
        if rows:
            self._insert(self.cfg.table_id(name), rows)

    def _insert(self, table_id, rows):
        payload = [{k: _jsonable(v) for k, v in r.items()} for r in rows]
        for chunk in _chunks(payload):
            for attempt in range(20):
                try:
                    errors = self.c.insert_rows_json(table_id, chunk)
                    break
                except gexc.NotFound:  # 剛建好的表要等幾秒才收串流寫入
                    time.sleep(1 + attempt)
            else:
                raise RuntimeError(f"寫入 {table_id} 一直找不到表")
            if errors:
                raise RuntimeError(f"寫入 {table_id} 失敗：{errors[:3]}")

    def _stage(self, kind, cols, rows):
        """這批資料先進暫存表 _stage_<kind>（單一 worker，每種一張，重複使用）。
        正式 BigQuery 用載入工作 WRITE_TRUNCATE（免費、整批一次換掉，不經串流緩衝區）；
        模擬器不支援載入工作，改成先清空再串流寫入（模擬器刪表一次要十幾秒，所以不每批建表刪表）。"""
        tid = f"{self.cfg.project}.{self.cfg.dataset}._stage_{kind}"
        data = [{n: r.get(n) for n, _ in cols} for r in rows]
        schema = _schema(cols)
        if self.emulator:
            if kind not in self._staged:
                try:
                    have = [f.name for f in self.c.get_table(tid).schema]
                except gexc.NotFound:
                    have = None
                if have is not None and have != [n for n, _ in cols]:  # 程式改版換了欄位：重建一次
                    self.c.delete_table(tid, not_found_ok=True)
                    have = None
                if have is None:
                    self.c.create_table(bigquery.Table(tid, schema=schema), exists_ok=True)
                self._staged.add(kind)
            self.q(f"DELETE FROM `{tid}` WHERE TRUE")
            self._insert(tid, data)
        else:
            cfg = bigquery.LoadJobConfig(schema=schema, write_disposition="WRITE_TRUNCATE")
            self.c.load_table_from_json([{k: _jsonable(v) for k, v in r.items()} for r in data], tid,
                                        job_config=cfg).result()
        return tid

    # ---------- 列表 ----------
    def page_summary(self, board):
        rows = self.q(f"""SELECT page_no, COUNT(*) AS n, MIN(ts) AS min_ts, MAX(ts) AS max_ts,
                                 MIN(IF(page_full, 1, 0)) AS is_full
                          FROM {self.t('list_slots')} WHERE board = @b GROUP BY page_no""", b=board)
        return {r.page_no: {"n": r.n, "min_ts": r.min_ts, "max_ts": r.max_ts, "full": r.is_full == 1} for r in rows}

    def merge_slots(self, rows):
        """寫入列表頁快照：重讀的頁整頁換成最新的 20 格（先刪這幾頁、再插入）。
        中途斷掉頂多少了幾頁快照，下一輪發現「沒讀過」就會重讀，不會留下半頁。"""
        if not rows:
            return
        board = rows[0]["board"]
        st = self._stage("slots", SLOT_COLS, rows)
        cond = " OR ".join(f"page_no BETWEEN {lo} AND {hi}" for lo, hi in page_ranges(r["page_no"] for r in rows))
        self.q(f"DELETE FROM {self.t('list_slots')} WHERE board = @b AND ({cond})", b=board)
        cols = ", ".join(n for n, _ in SLOT_COLS)
        self.q(f"INSERT INTO {self.t('list_slots')} ({cols}) SELECT {cols} FROM `{st}`")

    def prune_slots(self, board, older_than_ts):
        """30 天前的列表頁快照刪掉。頁碼跟時間同向，所以找出「整頁都比門檻舊」的最大頁碼，刪掉它以前的。"""
        rows = self.q(f"""SELECT MAX(page_no) AS p FROM (SELECT page_no FROM {self.t('list_slots')} WHERE board = @b
                          GROUP BY page_no HAVING MAX(ts) < @old)""", b=board, old=older_than_ts)
        if rows and rows[0].p is not None:
            self.q(f"DELETE FROM {self.t('list_slots')} WHERE board = @b AND page_no <= @p", b=board, p=rows[0].p)

    def _slots_in_window(self, board, since_ts):
        rows = self.q(f"""SELECT aid, ts, url, title, author, page_no, slot FROM {self.t('list_slots')}
                          WHERE board = @b AND NOT deleted AND aid IS NOT NULL AND ts >= @since""", b=board, since=since_ts)
        latest = {}
        for r in rows:  # 同一篇如果出現在兩格（頁面位移），取頁碼大的那格
            if r.aid not in latest or (r.page_no, r.slot) > (latest[r.aid]["page_no"], latest[r.aid]["slot"]):
                latest[r.aid] = dict(r.items())
        return latest

    def _post_aids(self, board, since):
        return {r.aid for r in self.q(f"SELECT aid FROM {self.t('posts')} WHERE board = @b AND published_at >= @since",
                                      b=board, since=since)}

    # ---------- 該抓的 ----------
    def due_new(self, board, since_ts, limit=100000):
        """列表上看到、7 天內、還沒進 posts 的文章（含上一輪失敗或被還原的：自動補抓）。舊的先抓。"""
        slots = self._slots_in_window(board, since_ts)
        have = self._post_aids(board, datetime.fromtimestamp(since_ts, timezone.utc))
        todo = [s for a, s in slots.items() if a not in have]
        return sorted(todo, key=lambda s: s["ts"])[:limit]

    def due_refresh(self, board, now, policy, refresh_after_hours, window_days, limit=100000):
        since = now - timedelta(days=window_days)
        if policy == "once24h":
            cond = ("fetch_count = 1 AND published_at <= @cut24 "
                    "AND first_fetched_at < TIMESTAMP_ADD(published_at, INTERVAL 24 HOUR)")
        else:
            cond = "last_fetched_at < @cutoff"
        rows = self.q(f"""SELECT aid, url, title, author, published_at, comment_count, last_fetched_at
                          FROM {self.t('posts')}
                          WHERE board = @b AND published_at >= @since AND status = 200 AND deleted_at IS NULL AND {cond}
                          ORDER BY last_fetched_at LIMIT @lim""",
                      b=board, since=since, cutoff=now - timedelta(hours=refresh_after_hours),
                      cut24=now - timedelta(hours=24), lim=limit)
        return [dict(r.items()) for r in rows]

    def comment_keys(self, aids):
        """這些文章目前存著的留言：{aid: {留言識別: 第一次看到的時間}}。重抓時拿來算新增了哪些、保留第一次看到的時間。"""
        if not aids:
            return {}
        rows = self.q(f"""SELECT aid, ARRAY(SELECT AS STRUCT c.user_id, c.time_text, c.content, c.dup_n,
                                                  UNIX_MICROS(c.first_seen_at) AS fs FROM UNNEST(comments) c) AS k
                          FROM {self.t('posts')} WHERE aid IN UNNEST(@aids)""", aids=list(aids))
        out = {}
        for r in rows:
            out[r.aid] = {comment_key(c): (datetime.fromtimestamp(c["fs"] / 1e6, timezone.utc) if c["fs"] else None)
                          for c in (r.k or [])}
        return out

    # ---------- 寫入一批（可還原） ----------
    def commit_batch(self, run_id, batch_no, posts, fail_after=None):
        """整批落地回傳 True；任何一步失敗就先還原這一批，再把原本的錯誤往外丟。fail_after 只給測試注入故障用。"""
        stamp = {"run_id": run_id, "batch_no": batch_no}
        sp = self._stage("posts", POST_STAGE_COLS, [{**p, "comments": p.get("comments") or [],
                                                      "comments_text": comments_text(p.get("comments")), **stamp}
                                                     for p in posts])
        aids = sorted({p["aid"] for p in posts})
        n_comments = sum(len(p.get("comments") or []) for p in posts)
        try:
            vcols = [n for n in POST_NAMES if n != "aid"]
            self.q(f"""INSERT INTO {self.t('post_versions')} (run_id, batch_no, saved_at, existed, aid, {', '.join(vcols)})
                       SELECT S.run_id, S.batch_no, CURRENT_TIMESTAMP(), P.aid IS NOT NULL, S.aid, {', '.join('P.' + c for c in vcols)}
                       FROM (SELECT DISTINCT aid, run_id, batch_no FROM `{sp}`) S LEFT JOIN {self.t('posts')} P ON P.aid = S.aid""")
            if fail_after == "versions":
                raise RuntimeError("注入故障：before-image 之後、MERGE 之前")
            self.q(f"""MERGE {self.t('posts')} T USING `{sp}` S ON T.aid = S.aid
                WHEN MATCHED THEN UPDATE SET
                  title = COALESCE(S.title, T.title), author = COALESCE(S.author, T.author),
                  content = COALESCE(S.content, T.content), push = COALESCE(S.push, T.push), boo = COALESCE(S.boo, T.boo),
                  arrow = COALESCE(S.arrow, T.arrow), comment_count = COALESCE(S.comment_count, T.comment_count),
                  truncated = COALESCE(S.truncated, T.truncated), html_bytes = COALESCE(S.html_bytes, T.html_bytes),
                  raw_uri = COALESCE(S.raw_uri, T.raw_uri), status = S.status,
                  comments = IF(S.status = 404, T.comments, S.comments),
                  comments_text = IF(S.status = 404, T.comments_text, S.comments_text),
                  deleted_at = IF(S.status = 404, COALESCE(T.deleted_at, S.fetched_at), NULL),
                  last_fetched_at = S.fetched_at, fetch_count = T.fetch_count + 1,
                  last_run_id = S.run_id, last_batch_no = S.batch_no
                WHEN NOT MATCHED THEN INSERT ({', '.join(POST_NAMES)})
                VALUES (S.aid, S.board, S.url, S.title, S.author, S.published_at, S.content, S.push, S.boo, S.arrow,
                  S.comment_count, S.truncated, S.html_bytes, S.raw_uri, S.status, S.comments, S.comments_text,
                  IF(S.status = 404, S.fetched_at, NULL), S.fetched_at, S.fetched_at, 1, S.run_id, S.batch_no)""")
            if fail_after == "merge":
                raise RuntimeError("注入故障：MERGE 之後、落地核對之前")
            landed = self.q(f"SELECT COUNT(*) AS n FROM {self.t('posts')} WHERE last_run_id = @run AND last_batch_no = @batch",
                            run=run_id, batch=batch_no)[0].n
            if landed != len(aids):
                raise RuntimeError(f"落地核對失敗：文章 {landed}/{len(aids)}")
            self.log_batch(run_id, batch_no, "committed", len(aids), n_comments)
            return True
        except Exception as e:
            try:
                self.rollback_batch(run_id, batch_no, note=str(e)[:200])
            except Exception as re:  # 還原也失敗（例如資料庫斷線）：這批沒有結局，下一輪開始前 recover() 會再還原
                e.add_note(f"rollback 也失敗：{re!r}"[:300])
            raise

    def log_batch(self, run_id, batch_no, state, posts=None, comments=None, note=None):
        self.insert_rows("batch_log", [{"run_id": run_id, "batch_no": batch_no, "state": state,
                                        "logged_at": datetime.now(timezone.utc), "posts": posts, "comments": comments, "note": note}])

    def rollback_batch(self, run_id, batch_no, note=None):
        """還原一批：這批碰過的文章全部換回寫入前的樣子（含留言），原本不存在的就刪掉。可重複執行。"""
        p = dict(run=run_id, batch=batch_no)
        self.q(f"""DELETE FROM {self.t('posts')} WHERE aid IN (SELECT aid FROM {self.t('post_versions')}
                   WHERE run_id = @run AND batch_no = @batch)""", **p)
        self.q(f"""INSERT INTO {self.t('posts')} ({', '.join(POST_NAMES)})
                   SELECT {', '.join(POST_NAMES)} FROM {self.t('post_versions')}
                   WHERE run_id = @run AND batch_no = @batch AND existed""", **p)
        self.log_batch(run_id, batch_no, "rolled_back", note=note)

    def batch_states(self, run_id):
        rows = self.q(f"""SELECT batch_no, ARRAY_AGG(state ORDER BY logged_at DESC LIMIT 1)[OFFSET(0)] AS state
                          FROM {self.t('batch_log')} WHERE run_id = @run GROUP BY batch_no""", run=run_id)
        return {r.batch_no: r.state for r in rows}

    def committed_batches(self, run_id):
        """這一輪最後狀態是 committed 的批次，倒序。"""
        return sorted((b for b, s in self.batch_states(run_id).items() if s == "committed"), reverse=True)

    def rollback_run(self, run_id):
        """整輪 rollback：照批次倒序還原，資料回到這輪開始前。"""
        batches = self.committed_batches(run_id)
        for b in batches:
            self.rollback_batch(run_id, b, note="整輪還原")
        return batches

    def recover(self, exclude_run=None):
        """上一輪中途當掉（程序被殺、資料庫斷線）留下沒有結局的批次：照 before-image 還原。回傳還原的 (run_id, batch_no)。"""
        started = {(r.run_id, r.batch_no) for r in self.q(f"SELECT DISTINCT run_id, batch_no FROM {self.t('post_versions')}")}
        ended = {(r.run_id, r.batch_no) for r in self.q(f"SELECT DISTINCT run_id, batch_no FROM {self.t('batch_log')}")}
        todo = sorted((x for x in started - ended if x[0] != exclude_run), key=lambda x: -x[1])
        for run_id, batch_no in todo:
            self.rollback_batch(run_id, batch_no, note="上一輪中途中斷，開始前還原")
        return todo

    # ---------- 監控用的統計 ----------
    def window_stats(self, board, now, window_days, since=None):
        since = since or now - timedelta(days=window_days)
        since_ts = int(since.timestamp())
        p = self.q(f"""SELECT
              COUNTIF(status = 200 AND deleted_at IS NULL) AS searchable,
              COUNTIF(deleted_at IS NOT NULL) AS deleted,
              COUNTIF(status = 200 AND deleted_at IS NULL AND (content IS NULL OR content = '') AND comment_count = 0) AS empty,
              SUM(IF(status = 200 AND deleted_at IS NULL, comment_count, 0)) AS comment_sum,
              COUNTIF(status = 200 AND deleted_at IS NULL AND truncated) AS truncated,
              MIN(IF(status = 200 AND deleted_at IS NULL, last_fetched_at, NULL)) AS stalest
            FROM {self.t('posts')} WHERE board = @b AND published_at >= @since""", b=board, since=since)[0]
        slots = self._slots_in_window(board, since_ts)
        have = self._post_aids(board, since)
        waiting = [s["ts"] for a, s in slots.items() if a not in have]
        oldest = min(waiting) if waiting else None
        return {
            "searchable": p.searchable or 0, "deleted": p.deleted or 0, "empty": p.empty or 0,
            # 可搜尋文章的 comment_count 就是存下的留言數，不必再數巢狀陣列
            "comment_sum": p.comment_sum or 0, "comments": p.comment_sum or 0, "truncated": p.truncated or 0,
            "unsearchable": len(waiting),
            "unsearchable_oldest_min": (now - datetime.fromtimestamp(oldest, timezone.utc)).total_seconds() / 60 if oldest else 0.0,
            "staleness_max_h": (now - p.stalest).total_seconds() / 3600 if p.stalest else 0.0,
        }

    def last_run_status(self, board):
        rows = self.q(f"SELECT status FROM {self.t('crawl_runs')} WHERE board = @b ORDER BY started_at DESC LIMIT 1", b=board)
        return rows[0].status if rows else None

    def last_verify_at(self, board):
        rows = self.q(f"SELECT MAX(run_at) AS t FROM {self.t('verify_runs')} WHERE board = @b", b=board)
        return rows[0].t if rows else None

    def last_run(self, board):
        rows = self.q(f"""SELECT status, started_at, finished_at FROM {self.t('crawl_runs')}
                          WHERE board = @b ORDER BY started_at DESC LIMIT 1""", b=board)
        return dict(rows[0].items()) if rows else None

    def last_verify(self, board):
        rows = self.q(f"""SELECT run_at, pages_checked, pages_not_20, missing_ids, out_of_order_pairs, nrec_checked,
                                 nrec_agree_ratio, ok FROM {self.t('verify_runs')}
                          WHERE board = @b ORDER BY run_at DESC LIMIT 1""", b=board)
        return dict(rows[0].items()) if rows else None

    # ---------- 不變量 ----------
    def verify_rows(self, board, since_ts, same_time_s=1800):
        """7 天內各列表頁的每一格（最新頁不算），附上 posts 裡有沒有這篇。
        same_time：列表快照和文章最近一次抓取相差不到 same_time_s 秒，推文數才拿來比（否則比的是兩個時間點的數字）。"""
        slots = self.q(f"""SELECT page_no, slot, aid, ts, deleted, nrec, snapshot_at FROM {self.t('list_slots')}
                           WHERE board = @b""", b=board)
        if not slots:
            return []
        newest = max(r.page_no for r in slots)
        page_max = {}
        for r in slots:
            if r.ts is not None:
                page_max[r.page_no] = max(page_max.get(r.page_no, 0), r.ts)
        pages = {p for p, mx in page_max.items() if mx >= since_ts and p < newest}
        since = datetime.fromtimestamp(since_ts, timezone.utc) - timedelta(days=1)  # 起點那頁前半是更早的文
        posts = {r.aid: r for r in self.q(f"""SELECT aid, status, push, boo, last_fetched_at FROM {self.t('posts')}
                                              WHERE board = @b AND published_at >= @since""", b=board, since=since)}
        out = []
        for r in sorted((r for r in slots if r.page_no in pages), key=lambda r: (r.page_no, r.slot)):
            p = posts.get(r.aid) if r.aid else None
            same = bool(p and p.last_fetched_at and r.snapshot_at
                        and abs((p.last_fetched_at - r.snapshot_at).total_seconds()) <= same_time_s)
            out.append({"page_no": r.page_no, "slot": r.slot, "aid": r.aid, "ts": r.ts, "deleted": r.deleted, "nrec": r.nrec,
                        "in_posts": p is not None, "status": p.status if p else None,
                        "push": p.push if p else None, "boo": p.boo if p else None, "same_time": same})
        return out

    # ---------- 搜尋（貼文和留言分開查，再依文章合併） ----------
    def search(self, board, kw, since, limit):
        # 摘要用 LEFT：模擬器的 SUBSTR 以位元組計，會把中文字切成半個；LEFT 在兩邊都以字元計
        cols = "aid, title, author, published_at, url, push, boo, arrow, comment_count, LEFT(content, 160) AS snippet"
        live = "board = @b AND published_at >= @since AND status = 200 AND deleted_at IS NULL"
        p = dict(b=board, since=since, kw=kw, lim=limit)
        posts = self.q(f"""SELECT {cols} FROM {self.t('posts')}
                           WHERE {live} AND (STRPOS(title, @kw) > 0 OR STRPOS(content, @kw) > 0)
                           ORDER BY published_at DESC LIMIT @lim""", **p)
        # 留言分兩步。先用 comments_text（留言接成的純文字）找出留言命中的最新幾篇，只掃一個字串欄位；
        # 再只把這幾篇的留言展開，取出命中的那幾則。展開留言要放在第二步：寫在同一個查詢裡，
        # 排序前每一篇命中的文章都會展開一次（模擬器上 7 天約 1,000 篇命中，要 3.3 秒；分兩步約 1 秒）。
        by_comment = self.q(f"""SELECT {cols} FROM {self.t('posts')}
                                WHERE {live} AND STRPOS(comments_text, @kw) > 0
                                ORDER BY published_at DESC LIMIT @lim""", **p)
        hits = {}
        if by_comment:
            for r in self.q(f"""SELECT aid, ARRAY(SELECT AS STRUCT c.floor, c.tag, c.user_id, c.content,
                                                          UNIX_MICROS(c.comment_at) AS at_us
                                                   FROM UNNEST(comments) c WHERE STRPOS(c.content, @kw) > 0) AS hits
                                FROM {self.t('posts')} WHERE {live} AND aid IN UNNEST(@aids)""",
                            b=board, since=since, kw=kw, aids=[r.aid for r in by_comment]):
                hits[r.aid] = r.hits
        seen = {r.aid for r in posts}
        more, comments = [], []
        for r in by_comment:
            d = dict(r.items())
            for c in hits.get(r.aid) or []:
                at = c["at_us"]
                comments.append({"aid": r.aid, "floor": c["floor"], "tag": c["tag"], "user_id": c["user_id"],
                                 "content": c["content"],
                                 "comment_at": datetime.fromtimestamp(at / 1e6, timezone.utc) if at is not None else None})
            if r.aid not in seen:
                more.append(d)
        oldest = datetime.min.replace(tzinfo=timezone.utc)
        comments.sort(key=lambda c: c["comment_at"] or oldest, reverse=True)
        return [dict(r.items()) for r in posts], more, comments
