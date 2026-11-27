"""回補 Job 的原文來源（RAW_SOURCE）。

- file（預設，本機對應元件）：RAW_ROOT/<channel>/<yyyy-mm>.jsonl，同一個 post_id 只留第一行。
- bigquery（正式環境）：讀 RAW_BQ_TABLE。只在選了 bigquery 才 import google-cloud-bigquery，本機不用多裝套件。

兩種來源給 job.run_partition 同一個介面：open_partition(channel, month, snapshot) 回一個分區物件，
有 raw_count（不重複 post_id 數，G1 比這個）、raw_lines（原始行數）、iter_posts(start)（從第 start 篇之後依固定順序吐 dict）。

BigQuery 原文表的假設欄位（表不由這裡建立，資料團隊照這個給）：
  post_id STRING, channel STRING, month STRING, sub_type STRING, source STRING, url STRING,
  published_at STRING, fetched_at STRING, author STRING, text STRING, ingested_at TIMESTAMP
  - month 必須等於 published_at 的前 7 碼（和 pipeline.month_of、ES 的 month 同一個規則）。
    不用 published_at 的時間範圍切月：YouTube 是 UTC 'Z'、PTT 是 +08:00，月初那幾小時會被切到別的月。
  - 建議 CLUSTER BY channel, month，每個分區只掃自己那一塊。
  - 重抓會有同一個 post_id 多行：照 ingested_at 先到先贏，和 file 的「第一行贏」一致。

斷點續跑：checkpoint_line 是「依 post_id 排序後的第幾篇」。每次續跑重新查詢，但用 FOR SYSTEM_TIME AS OF 讀同一個時間點
（帳本 started_at），所以中間有新資料進來，順序也不會跑掉。這個時間點超過 BigQuery 的時光回溯期限（預設 7 天）就查不到，
job 會改用現在、從頭重跑（見 job.fresh_snapshot）。
"""
import datetime as dt
import json
import re
from itertools import islice
from pathlib import Path

from . import config

SNAPSHOT_MARGIN_HOURS = 6        # 快到時光回溯期限前就換新時間點，避免跑到一半查不到
# 專案.資料集.表；資料集只能英數底線，表名 BigQuery 另外允許 -（這裡不收空白和其他 Unicode，表名要用反引號包起來拼進 SQL）
# 和 shared/infra/gcp/variables.tf 的 q6_raw_bq_table 檢查同一條；長度上限交給 BigQuery（Terraform 的 RE2 不收 {1,1024}，超過 1000 整條規則會失效）
TABLE_RE = re.compile(r"^[a-z][a-z0-9-]{4,61}[a-z0-9]\.[A-Za-z0-9_]+\.[A-Za-z0-9_-]+$")
FIELDS = ("post_id", "channel", "sub_type", "source", "url", "published_at", "fetched_at", "author", "text")


def load_partition_lines(raw: Path):
    """讀原文分區：同一個 post_id 只留第一行（重抓的重複行會讓 G1 誤判、也會多算 noop）；回 (去重後的行, 原始行數)。"""
    lines, seen, total = [], set(), 0
    for line in raw.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        total += 1
        pid = json.loads(line).get("post_id")
        if pid in seen:
            continue
        seen.add(pid)
        lines.append(line)
    return lines, total


class FilePartition:
    def __init__(self, path: Path):
        self.lines, self.raw_lines = load_partition_lines(path)
        self.raw_count = len(self.lines)

    def iter_posts(self, start: int = 0):
        for line in islice(self.lines, start, None):
            yield json.loads(line)


COUNT_SQL = """SELECT COUNT(*) AS raw_lines, COUNT(DISTINCT post_id) AS raw_count, COUNTIF(post_id IS NULL) AS null_ids
FROM `{table}` FOR SYSTEM_TIME AS OF LEAST(@snap, CURRENT_TIMESTAMP())
WHERE channel = @channel AND month = @month"""

POSTS_SQL = """WITH d AS (
  SELECT t.post_id, t.channel, t.sub_type, t.source, t.url, t.published_at, t.fetched_at, t.author, t.text
  FROM `{table}` AS t FOR SYSTEM_TIME AS OF LEAST(@snap, CURRENT_TIMESTAMP())
  WHERE t.channel = @channel AND t.month = @month AND t.post_id IS NOT NULL
  QUALIFY ROW_NUMBER() OVER (PARTITION BY t.post_id ORDER BY t.ingested_at, FARM_FINGERPRINT(TO_JSON_STRING(t))) = 1
), n AS (
  SELECT *, ROW_NUMBER() OVER (ORDER BY post_id) AS rn FROM d
)
SELECT * FROM n WHERE rn > @start ORDER BY rn"""


def _plain(v):
    """BigQuery 回的 TIMESTAMP／DATETIME 轉成 ISO 字串，和 jsonl 原文一樣（base_doc 直接寫進 ES）。"""
    return v.isoformat() if isinstance(v, (dt.datetime, dt.date)) else v


def row_to_post(row) -> dict:
    get = row.get if hasattr(row, "get") else (lambda k, d=None: getattr(row, k, d))
    if isinstance(get("published_at"), dt.datetime):
        # TIMESTAMP 回來一律是 UTC，原本的 +08:00 不見了：PTT 11/1 00:30 會變 10/31 16:30，ES 的 month 切錯月、G1 對不上
        raise ValueError("原文表的 published_at 必須是 STRING（保留原本時區），不能是 TIMESTAMP")
    post = {k: _plain(get(k)) for k in FIELDS}
    if post["text"] is None:
        post["text"] = ""
    return post


class BigQueryPartition:
    def __init__(self, client, table: str, channel: str, month: str, snapshot: dt.datetime, page_size: int = 500):
        self.client, self.table, self.channel, self.month, self.snapshot, self.page_size = client, table, channel, month, snapshot, page_size
        row = next(iter(self._query(COUNT_SQL).result()))
        self.raw_lines, self.raw_count, self.null_ids = int(row["raw_lines"]), int(row["raw_count"]), int(row["null_ids"])

    def _query(self, sql: str, start: int = None):
        from google.cloud import bigquery
        params = [bigquery.ScalarQueryParameter("snap", "TIMESTAMP", self.snapshot),
                  bigquery.ScalarQueryParameter("channel", "STRING", self.channel),
                  bigquery.ScalarQueryParameter("month", "STRING", self.month)]
        if start is not None:
            params.append(bigquery.ScalarQueryParameter("start", "INT64", int(start)))
        return self.client.query(sql.format(table=self.table), job_config=bigquery.QueryJobConfig(query_parameters=params))

    def iter_posts(self, start: int = 0):
        # 一頁一頁拉（page_size 篇），不把整個分區（平均約 180 萬篇）載進記憶體
        for row in self._query(POSTS_SQL, start).result(page_size=self.page_size):
            yield row_to_post(row)


def check_config():
    """設定錯就在領分區之前停下來，不要領了才失敗（白白用掉一次 attempts）。"""
    if config.RAW_SOURCE not in ("file", "bigquery"):
        raise SystemExit(f"RAW_SOURCE 只能是 file 或 bigquery，拿到 {config.RAW_SOURCE!r}")
    if config.RAW_SOURCE == "bigquery" and not TABLE_RE.match(config.RAW_BQ_TABLE or ""):
        raise SystemExit(f"RAW_SOURCE=bigquery 要設 RAW_BQ_TABLE=專案.資料集.表，拿到 {config.RAW_BQ_TABLE!r}")


_client = None


def bq_client():
    global _client
    if _client is None:
        from google.cloud import bigquery
        _client = bigquery.Client(project=config.RAW_BQ_PROJECT or None)
    return _client


PARTITIONS_SQL = """SELECT channel, month FROM `{table}`
WHERE post_id IS NOT NULL
GROUP BY channel, month ORDER BY month DESC, channel"""


def list_partitions():
    """這一輪要回補的分區（渠道, 月），建帳本用。bigquery：原文表裡有資料的渠道 × 月；file：RAW_ROOT/<channel>/<yyyy-mm>.jsonl。"""
    if config.RAW_SOURCE == "bigquery":
        check_config()
        rows = bq_client().query(PARTITIONS_SQL.format(table=config.RAW_BQ_TABLE)).result()
        return [(r["channel"], r["month"]) for r in rows]
    return [(f.parent.name, f.stem) for f in sorted(Path(config.RAW_ROOT).glob("*/*.jsonl"))]


def open_partition(channel: str, month: str, snapshot: dt.datetime = None, page_size: int = 500):
    if config.RAW_SOURCE == "bigquery":
        check_config()
        if snapshot is None:
            raise ValueError("bigquery 來源要給 snapshot（帳本 started_at）")
        return BigQueryPartition(bq_client(), config.RAW_BQ_TABLE, channel, month, snapshot, page_size)
    return FilePartition(Path(config.RAW_ROOT) / channel / f"{month}.jsonl")
