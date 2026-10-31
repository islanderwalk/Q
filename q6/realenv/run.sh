#!/usr/bin/env bash
# Q6 真實環境：一步一步重跑。全部在本機 Docker，零成本。用法：./run.sh <步驟>
#   build   下載並驗證 ES 外掛、建字典 artifact r1–r6、建三個映像
#   up      起 ES／Kibana／PostgreSQL／Pub/Sub 模擬器／字典服務／API／監控
#   seed    建索引與訂閱，灌 42.5 萬篇 PTT，寫舊詞表 terms_v1（模擬「現在的線上狀態」）
#   backfill 回補 v2（PM 首月簽核 → 3 個 task）
#   stream  起串流斷詞服務 v1／v2，送六渠道樣本
#   exp     ES 實驗（分析器、聚合、版本守門、延遲、PG 對照）、寫入壓力對查詢延遲
#   ik      IK 遠端字典熱更新＋同義詞（會把字典 current 切到 r5）
#   probe   回補期間在旁邊量 PM 查詢延遲（另開一個終端機跑）
#   shots   Playwright 截圖
# 需要：Docker、Python 3（requests、jieba、psycopg、google-cloud-pubsub）、Node＋Playwright。
# 原始資料（不進 git）：DL=…/dict.txt.big 與 Gossiping-QA-Dataset.txt 所在目錄，見 README.md。
set -euo pipefail
cd "$(dirname "$0")"
DL=${DL:-/tmp/q6dl}
PY=${PY:-python3}
CA=${CA:-}   # 公司代理會攔 TLS 時給 CA 檔，例如 CA=/root/.ccr/ca-bundle.crt
secret=(); [ -n "$CA" ] && secret=(--secret "id=ca,src=$CA")
R=qsearch-q6-2026.10.30
case "${1:-}" in
  build)
    es/fetch_plugins.sh
    $PY dict/build_artifact.py "$DL/dict.txt.big" runtime/dictsrv $R.1 $R.3 $R.4 $R.5 $R.6 $R.2   # 最後一個是 current
    docker build --network host -t qsearch-es:9.5.5 es
    (cd .. && docker build --network host "${secret[@]}" --build-arg SEG_BUILD="$(git rev-parse --short HEAD)" -t qsearch-seg:local -f realenv/seg/Dockerfile .)
    docker build --network host "${secret[@]}" -t qsearch-wordcloud-api:local -f api/Dockerfile .
    ;;
  up)
    docker compose up -d dictsrv es kibana pubsub pg api otelcol prometheus grafana
    ;;
  seed)
    $PY scripts/prepare_raw.py "$DL/Gossiping-QA-Dataset.txt" runtime/raw
    PUBSUB_EMULATOR_HOST=localhost:8085 $PY scripts/setup.py
    for i in 0 1 2; do docker compose --profile job run -d --no-deps --name seed-$i -e CLOUD_RUN_TASK_INDEX=$i -e CLOUD_RUN_TASK_COUNT=3 \
      -e PIPELINE=v1 -e DICT_VERSION=$R.1 seg-job seed; done
    ;;
  backfill)
    V=${V:-$R.3}; T=$((22026103000 + ${V##*.}))   # seg_ver：v2 × 10^10 ＋ 20261030 × 100 ＋ 序號
    docker exec qsearch-q6-pg-1 psql -U qsearch -d qsearch -c \
      "UPDATE backfill_ledger SET dict_version='$V', target_seg_ver=$T WHERE status IN ('pending','failed')"
    docker compose --profile job run --rm -T --no-deps -e DICT_VERSION=$V seg-job baseline
    docker compose --profile job run --rm -T --no-deps -e DICT_VERSION=$V seg-job job          # 首月分區 → awaiting_pm
    docker compose --profile job run --rm -T --no-deps seg-job approve ptt/2026-10 --by PM --note "A/B 抽查通過"
    for i in 0 1 2; do docker compose --profile job run -d --no-deps --name job-$i -e DICT_VERSION=$V -e CLOUD_RUN_TASK_INDEX=$i seg-job job; done
    ;;
  stream)
    DICT_VERSION_V2=${V:-$R.6} docker compose up -d --no-deps seg-v1 seg-v2
    PUBSUB_EMULATOR_HOST=localhost:8085 $PY scripts/publish_channels.py data/channels results/stream/six_channels.json
    ;;
  exp)
    DICT_BASE_URL=http://localhost:8081 $PY scripts/es_experiments.py analyzers guard aggs latency pg
    $PY scripts/write_load_latency.py results/es/write_load_vs_query_latency.json
    ;;
  ik)
    $PY scripts/ik_hot_reload.py runtime/dictsrv $R.2 $R.5 肥宅 results/es/ik_hot_reload.json
    ;;
  probe)
    $PY scripts/latency_probe.py results/latency_probe.jsonl "${SECS:-3600}"
    $PY scripts/summarize_probe.py results/latency_probe.jsonl results/backfill/phases.jsonl results/latency_probe_summary.json
    ;;
  shots)
    node scripts/screenshots.js ../evidence/realenv "$(( ($(date +%s) - 3600) * 1000 ))" "$(( $(date +%s) * 1000 ))"
    ;;
  *) sed -n '2,12p' "$0"; exit 1 ;;
esac
