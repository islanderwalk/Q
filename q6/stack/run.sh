#!/usr/bin/env bash
# Q6 真實環境：一步一步重跑。全部在本機 Docker，零成本。用法：./run.sh <步驟>
#   build   下載並驗證 ES 外掛、建字典 artifact r1–r6、建三個映像
#   up      起 ES／Kibana／PostgreSQL／Pub/Sub 模擬器／字典服務／API／監控
#   seed    建索引與訂閱，灌 PTT 真實文章（真實發文時間：標題 + 兩個公開語料），寫舊詞表 terms_v1（「現在的線上狀態」）
#   backfill 回補 v2：首月跑完等 PM 簽核；AUTO_APPROVE=1（預設）用指令簽核並接著開 3 個 task，AUTO_APPROVE=0 到 PM 頁簽核後再跑 backfill-tasks
#   backfill-tasks 開 3 個回補 task（PM 簽核過之後）
#   live    字典 current 切到最新版，起串流斷詞 v1／v2、API（PM 頁 http://127.0.0.1:8080）、真實來源採集器
#   stream  送六渠道樣本（含重送冪等、壞訊息進 DLQ 的實測）
#   exp     ES 實驗（分析器、聚合、版本守門、延遲、PG 對照）、寫入壓力對查詢延遲
#   ik      IK 遠端字典熱更新＋同義詞（會把字典 current 切到 r5）
#   probe   回補期間在旁邊量 PM 查詢延遲（另開一個終端機跑）
#   shots   Playwright 截圖
#   slo     用 Sloth 從 monitoring/slo/*.yml 重新產生 monitoring/rules/slo.yml（燃燒率規則）；promtool 檢查全部規則
# 需要：Docker、Python 3（requests、jieba、psycopg、google-cloud-pubsub）、Node＋Playwright。
# 原始資料（不進 git）：DL=… 放 dict.txt.big、ptt_pretrain.parquet、ptt_gossiping_100k.parquet（下載方式見 README.md）。
# 測試機縮小規模：SINCE=2020-01、MAX_PER_SOURCE=50000（都是真實文章，只是少收一點）。
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
    $PY dict/build_legacy.py runtime/dictsrv qsearch-q6-legacy-2019.12.31.1 --moe "$DL/moe_revised_words.txt" --jieba-tw "$DL/jieba_tw_dict.txt"   # v0 舊系統替身字典
    docker build --network host -t qsearch-es:9.5.5 es
    (cd .. && docker build --network host "${secret[@]}" --build-arg SEG_BUILD="$(git rev-parse --short HEAD)" -t qsearch-seg:local -f stack/seg/Dockerfile .)
    docker build --network host "${secret[@]}" -t qsearch-wordcloud-api:local -f api/Dockerfile .
    ;;
  up)
    docker compose up -d dictsrv es kibana pubsub pg api otelcol es-exporter alertmanager prometheus loki alloy grafana
    ;;
  seed)
    args=(--out runtime/raw)
    [ -f "$DL/ptt_pretrain.parquet" ] && args+=(--pretrain "$DL/ptt_pretrain.parquet")
    [ -f "$DL/ptt_gossiping_100k.parquet" ] && args+=(--gossip100k "$DL/ptt_gossiping_100k.parquet")
    [ -n "${SINCE:-}" ] && args+=(--since "$SINCE")
    [ -n "${MAX_PER_SOURCE:-}" ] && args+=(--max-per-source "$MAX_PER_SOURCE")
    $PY scripts/prepare_raw.py "${args[@]}"
    PUBSUB_EMULATOR_HOST=localhost:8085 $PY scripts/setup.py
    for i in 0 1 2; do docker compose --profile job run -d --no-deps --name seed-$i -e CLOUD_RUN_TASK_INDEX=$i -e CLOUD_RUN_TASK_COUNT=3 \
      -e PIPELINE=v1 -e DICT_VERSION=$R.1 seg-job seed; done
    # v0 舊系統替身：同一批原文再用凍結 2019 字典寫 terms_v0（「上線前的舊文字雲」）
    for i in 0 1 2; do docker compose --profile job run -d --no-deps --name seed-v0-$i -e CLOUD_RUN_TASK_INDEX=$i -e CLOUD_RUN_TASK_COUNT=3 \
      -e PIPELINE=v0 -e DICT_VERSION=qsearch-q6-legacy-2019.12.31.1 seg-job seed; done
    ;;
  backfill)
    V=${V:-$R.3}; T=$($PY -c "import sys; sys.path.insert(0, 'api'); import ops; print(ops.seg_ver('v2', '$V'))")   # seg_ver：v2 × 10^10 ＋ 日期序號
    docker exec qsearch-q6-pg-1 psql -U qsearch -d qsearch -c \
      "UPDATE backfill_ledger SET dict_version='$V', target_seg_ver=$T WHERE status IN ('pending','failed')"
    docker compose --profile job run --rm -T --no-deps -e DICT_VERSION=$V seg-job baseline
    docker compose --profile job run --rm -T --no-deps -e DICT_VERSION=$V seg-job job          # 首月分區 → awaiting_pm
    if [ "${AUTO_APPROVE:-1}" = 1 ]; then
      docker compose --profile job run --rm -T --no-deps seg-job approve ptt/2026-10 --by PM --note "A/B 抽查通過"
      "$0" backfill-tasks
    else
      echo "首月分區在等 PM 簽核：到 PM 頁（http://127.0.0.1:8080）的「回補帳本」按『簽核通過』，然後跑 ./run.sh backfill-tasks"
    fi
    ;;
  backfill-tasks)
    V=${V:-$R.3}
    for i in 0 1 2; do docker compose --profile job run -d --no-deps --name job-$i -e DICT_VERSION=$V -e CLOUD_RUN_TASK_INDEX=$i seg-job job; done
    ;;
  live)
    $PY scripts/set_current.py runtime/dictsrv "${V:-latest}"
    COLLECT_SKIP="${COLLECT_SKIP:-}" docker compose up -d --no-deps seg-v0 seg-v1 seg-v2 api collector seg-coverage seg-recon seg-quality
    echo "PM 頁：http://127.0.0.1:8080（WSL mirrored 網路請用 127.0.0.1，不要用 localhost）"
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
    node scripts/screenshots.js ../evidence/stack "$(( ($(date +%s) - 3600) * 1000 ))" "$(( $(date +%s) * 1000 ))"
    ;;
  slo)
    docker run --rm -v "$PWD/monitoring:/w:ro" ghcr.io/slok/sloth:v0.11.0 generate --default-slo-period 28d -i /w/slo/wordcloud-api.yml > monitoring/rules/slo.yml   # 容器非 root，寫不進掛載目錄，改接 stdout
    docker run --rm -v "$PWD/monitoring:/w:ro" --entrypoint sh quay.io/prometheus/prometheus:v3.5.0 -c "promtool check rules /w/rules/*.yml"
    docker run --rm -v "$PWD/monitoring:/w:ro" --entrypoint amtool quay.io/prometheus/alertmanager:v0.28.1 check-config /w/alertmanager/alertmanager.yml
    ;;
  *) sed -n '2,12p' "$0"; exit 1 ;;
esac
