import os

ES_URL = os.environ.get("ES_URL", "http://es:9200")
INDEX = os.environ.get("ES_INDEX", "posts")
DICT_BASE_URL = os.environ.get("DICT_BASE_URL", "http://dictsrv")
DICT_VERSION = os.environ.get("DICT_VERSION", "current")
PIPELINE = os.environ.get("PIPELINE", "v2")          # v1 = 舊流程（純字典、不清洗）；v2 = 新流程
ALGO = {"v0": 0, "v1": 1, "v2": 2}
PROJECT = os.environ.get("PUBSUB_PROJECT", "qsearch-local")
SUBSCRIPTION = os.environ.get("PUBSUB_SUBSCRIPTION", "parsed-posts-seg-v2")
PG_DSN = os.environ.get("PG_DSN", "postgresql://qsearch:qsearch@pg:5432/qsearch")
RAW_ROOT = os.environ.get("RAW_ROOT", "/data/raw")
METRICS_PORT = int(os.environ.get("METRICS_PORT", "9108"))
COVERAGE_PORT = int(os.environ.get("COVERAGE_PORT", "9110"))   # P0-2：coverage 子命令的 /metrics
RECON_PORT = int(os.environ.get("RECON_PORT", "9112"))         # 完整性四層對帳＋回補燃盡（recon 子命令）的 /metrics
QUALITY_PORT = int(os.environ.get("QUALITY_PORT", "9113"))     # 文字雲品質日報（quality 子命令）的 /metrics
MAX_ATTEMPTS = int(os.environ.get("BACKFILL_MAX_ATTEMPTS", "3"))   # 分區被領超過這個次數就轉 failed，不再反覆重領
SEG_BUILD = os.environ.get("SEG_BUILD", "dev")
# Cloud Run Jobs 內建環境變數：一個 Job 開多個 task 時，用它們把分區切成 slice
TASK_INDEX = int(os.environ.get("CLOUD_RUN_TASK_INDEX", "0"))
TASK_COUNT = int(os.environ.get("CLOUD_RUN_TASK_COUNT", "1"))
# 字典發版控制訊息（control.py）：只有 v2 串流服務設 CONTROL_SUBSCRIPTION；空字串＝不訂閱
CONTROL_TOPIC = os.environ.get("CONTROL_TOPIC", "dict-releases")
CONTROL_SUBSCRIPTION = os.environ.get("CONTROL_SUBSCRIPTION", "")
PUBSUB_EMULATOR_HOST = os.environ.get("PUBSUB_EMULATOR_HOST", "")
