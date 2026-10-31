import os

ES_URL = os.environ.get("ES_URL", "http://es:9200")
INDEX = os.environ.get("ES_INDEX", "posts")
DICT_BASE_URL = os.environ.get("DICT_BASE_URL", "http://dictsrv")
DICT_VERSION = os.environ.get("DICT_VERSION", "current")
PIPELINE = os.environ.get("PIPELINE", "v2")          # v1 = 舊流程（純字典、不清洗）；v2 = 新流程
ALGO = {"v1": 1, "v2": 2}
PROJECT = os.environ.get("PUBSUB_PROJECT", "qsearch-local")
SUBSCRIPTION = os.environ.get("PUBSUB_SUBSCRIPTION", "parsed-posts-seg-v2")
PG_DSN = os.environ.get("PG_DSN", "postgresql://qsearch:qsearch@pg:5432/qsearch")
RAW_ROOT = os.environ.get("RAW_ROOT", "/data/raw")
METRICS_PORT = int(os.environ.get("METRICS_PORT", "9108"))
SEG_BUILD = os.environ.get("SEG_BUILD", "dev")
# Cloud Run Jobs 內建環境變數：一個 Job 開多個 task 時，用它們把分區切成 slice
TASK_INDEX = int(os.environ.get("CLOUD_RUN_TASK_INDEX", "0"))
TASK_COUNT = int(os.environ.get("CLOUD_RUN_TASK_COUNT", "1"))
