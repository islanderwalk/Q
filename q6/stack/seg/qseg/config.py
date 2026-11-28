import os

ES_URL = os.environ.get("ES_URL", "http://es:9200")
INDEX = os.environ.get("ES_INDEX", "posts")
DICT_BASE_URL = os.environ.get("DICT_BASE_URL", "http://dictsrv")
DICT_VERSION = os.environ.get("DICT_VERSION", "current")
PIPELINE = os.environ.get("PIPELINE", "v2")          # v1 = 舊流程（純字典、不清洗）；v2 = 新流程
ALGO = {"v0": 0, "v1": 1, "v2": 2}
PROJECT = os.environ.get("PUBSUB_PROJECT", "argus-local")
SUBSCRIPTION = os.environ.get("PUBSUB_SUBSCRIPTION", "parsed-posts-seg-v2")
PG_DSN = os.environ.get("PG_DSN", "postgresql://argus:argus@pg:5432/argus")
RAW_ROOT = os.environ.get("RAW_ROOT", "/data/raw")
# 回補 Job 的原文來源（rawsrc.py）：file＝本機對應元件，讀 RAW_ROOT；bigquery＝正式環境，讀 RAW_BQ_TABLE（專案.資料集.表）
RAW_SOURCE = os.environ.get("RAW_SOURCE", "file")
RAW_BQ_TABLE = os.environ.get("RAW_BQ_TABLE", "")
RAW_BQ_PROJECT = os.environ.get("RAW_BQ_PROJECT", "")             # 查詢記帳的專案；空＝用執行環境預設
BQ_TIME_TRAVEL_HOURS = int(os.environ.get("BQ_TIME_TRAVEL_HOURS", "168"))   # 原文表所在資料集的時光回溯期限（預設 7 天）
METRICS_PORT = int(os.environ.get("METRICS_PORT", "9108"))
COVERAGE_PORT = int(os.environ.get("COVERAGE_PORT", "9110"))   # P0-2：coverage 子命令的 /metrics
RECON_PORT = int(os.environ.get("RECON_PORT", "9112"))         # 完整性四層對帳＋回補燃盡（recon 子命令）的 /metrics
QUALITY_PORT = int(os.environ.get("QUALITY_PORT", "9113"))     # 文字雲品質日報（quality 子命令）的 /metrics
LEDGER_INDEX = os.environ.get("LEDGER_INDEX", "backfill_ledger")   # 回補帳本：ES 索引（ledger.py），取代原本 PostgreSQL 的 backfill_ledger 表
MAX_ATTEMPTS = int(os.environ.get("BACKFILL_MAX_ATTEMPTS", "3"))   # 分區被領超過這個次數就轉 failed，不再反覆重領
SEG_BUILD = os.environ.get("SEG_BUILD", "dev")
# Cloud Run Jobs 內建環境變數：一個 Job 開多個 task 時，用它們把分區切成 slice
TASK_INDEX = int(os.environ.get("CLOUD_RUN_TASK_INDEX", "0"))
TASK_COUNT = int(os.environ.get("CLOUD_RUN_TASK_COUNT", "1"))
# 字典發版控制訊息（control.py）：只有 v2 串流服務設 CONTROL_SUBSCRIPTION；空字串＝不訂閱
CONTROL_TOPIC = os.environ.get("CONTROL_TOPIC", "dict-releases")
CONTROL_SUBSCRIPTION = os.environ.get("CONTROL_SUBSCRIPTION", "")
PUBSUB_EMULATOR_HOST = os.environ.get("PUBSUB_EMULATOR_HOST", "")
