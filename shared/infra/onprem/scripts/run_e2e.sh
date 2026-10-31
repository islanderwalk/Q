#!/usr/bin/env bash
# 地端 stack 端到端驗證（VERIFY.md 的數字都來自這支腳本的輸出）。
#
#   bash shared/infra/onprem/scripts/run_e2e.sh [輸出目錄]
#
# 步驟：下載 pg_bigm → down -v（確保全新）→ build → up --wait → smoke test
#       → 記錄 docker stats / 映像 digest / RabbitMQ 佇列 / 容器日誌 → down -v（KEEP_UP=1 則保留）
# 環境變數：
#   ENV_FILE           預設 ../.env.example（示範帳密）
#   KEEP_UP=1          測完不關
#   BUILD_CA_BUNDLE    只有出口走 TLS 攔截 proxy 時需要（同時設 BUILDX_BAKE_ENTITLEMENTS_FS=0）
set -uo pipefail

HERE="$(cd "$(dirname "$0")/.." && pwd)"
OUT="${1:-${TMPDIR:-/tmp}/qsearch-onprem-verify-$(date +%Y%m%d-%H%M%S)}"
PROJECT=qsearch-onprem
ENV_FILE="${ENV_FILE:-$HERE/.env.example}"
mkdir -p "$OUT"
chmod 777 "$OUT"   # smoke 容器以 uid 10001 寫 smoke_summary.json
: > "$OUT/steps.tsv"

dc() { docker compose -p "$PROJECT" --env-file "$ENV_FILE" -f "$HERE/docker-compose.yml" "$@"; }

step() {
  local name=$1; shift
  local t0 t1 rc
  t0=$(date +%s.%N)
  "$@" > "$OUT/$name.log" 2>&1
  rc=$?
  t1=$(date +%s.%N)
  printf '%s\texit=%s\tseconds=%s\t%s\n' "$name" "$rc" "$(awk -v a="$t0" -v b="$t1" 'BEGIN{printf "%.1f", b-a}')" "$*" \
    | tee -a "$OUT/steps.tsv"
  return $rc
}

overall=0
step 01_fetch_pg_bigm python3 -I "$HERE/postgres-bigm/fetch_pg_bigm.py" || overall=1
step 02_down_pre dc down -v --remove-orphans
step 03_build dc --progress plain build || overall=1
step 04_up_wait dc up -d --wait --wait-timeout 300 || overall=1
step 05_ps dc ps -a
step 06_smoke dc run --rm -v "$OUT:/results" smoke || overall=1
step 07_stats docker stats --no-stream --format 'table {{.Name}}\t{{.MemUsage}}\t{{.MemPerc}}\t{{.CPUPerc}}' \
  $(dc ps -q)
images() {
  local i
  for i in $(dc config --images | sort -u); do
    printf '%s\t%s\t%s bytes\n' "$i" "$(docker image inspect "$i" --format '{{.Id}}')" \
      "$(docker image inspect "$i" --format '{{.Size}}')"
  done
}
step 08_images images
step 09_rabbit_queues dc exec -T rabbitmq rabbitmqctl -q list_queues name type messages arguments
step 10_pg_indexes dc exec -T postgres sh -c 'psql -U "$POSTGRES_USER" -d crawler -c "\\d ptt_posts" -c "SELECT extname, extversion FROM pg_extension"'
step 11_logs dc logs --no-color
if [ "${KEEP_UP:-0}" != "1" ]; then
  step 12_down dc down -v
fi
echo "overall=$overall out=$OUT"
exit $overall
