#!/bin/bash
# Q2 真的 pg_bigm：用 repo 的 shared/infra/onprem/postgres-bigm/Dockerfile 在官方 PostgreSQL 16.15 映像上編 pg_bigm，
# 起容器後跑 run_q2.py --pg-bigm（其餘參數照傳），最後刪掉容器。
# 用法：PY=<python> bash q2/code/run_pg_bigm.sh <scratch 目錄> <run_q2.py 的參數…>
#   例：bash q2/code/run_pg_bigm.sh /tmp/q2bigm --rows 1000000 --mode repeat --skip-bigram --out /tmp/q2bigm/repeat.json
#   需要 Docker；PostgreSQL 只綁 127.0.0.1:$PORT（預設 55433）。KEEP=1 時不刪容器（之後可以再跑 query_bench.py）。
#   出口走 TLS 攔截 proxy 的環境，CA bundle 放在 $BUILD_CA（預設 /root/.ccr/ca-bundle.crt，檔案不存在就不帶）。
# 每個指令後面記退出碼（echo "exit=$?"，不經過管線）。
set -u
export PYTHONDONTWRITEBYTECODE=1
PY=${PY:-python3}
W=${1:?usage: run_pg_bigm.sh <scratch dir> <run_q2.py args...>}
shift
PORT=${PORT:-55433}
PG_BASE=${PG_BASE:-mirror.gcr.io/library/postgres:16.15-bookworm@sha256:e8a07d2c7579b038938cdba6c539e580e5001caf9ea203d397b5cc83e76bd938}
IMAGE=qsearch-q2-pg16-bigm:local
NAME=q2bigm-$PORT
CODE=$(cd "$(dirname "$0")" && pwd -P)
REPO=$(cd "$CODE/../.." && pwd -P)
BIGM=$REPO/shared/infra/onprem/postgres-bigm
mkdir -p "$W"
W=$(cd "$W" && pwd -P)
case "$W" in "$REPO" | "$REPO"/*) echo "scratch 目錄不能在 repo 裡：$W" >&2; exit 2 ;; esac

echo "### 0. 版本"
echo "working tree HEAD = $(git -C "$REPO" rev-parse --short HEAD 2>/dev/null || echo '（不是 git checkout）')"
$PY --version
docker version --format 'docker server {{.Server.Version}}'

echo; echo "### 1. 建映像（pg_bigm 原始碼的 sha256 固定在 fetch_pg_bigm.py 與 Dockerfile）"
rm -rf "$W/ctx"
mkdir -p "$W/ctx"
cp "$BIGM/Dockerfile" "$W/ctx/"
python3 -I "$BIGM/fetch_pg_bigm.py" "$W/ctx/vendor"; rc=$?
echo "fetch exit=$rc"
[ "$rc" = 0 ] || exit "$rc"
SECRET=()
CA=${BUILD_CA:-/root/.ccr/ca-bundle.crt}
[ -s "$CA" ] && SECRET=(--secret "id=build_ca,src=$CA")
DOCKER_BUILDKIT=1 docker build --network host --build-arg PG_BASE="$PG_BASE" \
	--build-arg https_proxy="${HTTPS_PROXY:-}" --build-arg HTTPS_PROXY="${HTTPS_PROXY:-}" ${SECRET[@]+"${SECRET[@]}"} \
	-t "$IMAGE" "$W/ctx" > "$W/build.log" 2>&1
rc=$?
echo "build exit=$rc（log：$W/build.log）"
# 建失敗就停，不然會拿舊的同名映像去量
[ "$rc" = 0 ] || exit "$rc"

echo; echo "### 2. 起容器"
docker rm -f "$NAME" >/dev/null 2>&1
docker run -d --name "$NAME" --shm-size=1g -p "127.0.0.1:$PORT:5432" -e POSTGRES_HOST_AUTH_METHOD=trust "$IMAGE" >/dev/null
rc=$?
echo "docker run exit=$rc"
# 起不來（例如埠被別的 PostgreSQL 佔了）就停：run_q2.py 會 DROP TABLE fb_posts，不能連到別的伺服器
[ "$rc" = 0 ] || { docker rm -f "$NAME" >/dev/null 2>&1; exit "$rc"; }
# 官方映像先用只聽 unix socket 的暫時伺服器初始化，TCP 連得上才是正式伺服器
ready=
for _ in $(seq 1 90); do
	docker exec "$NAME" pg_isready -q -h 127.0.0.1 -U postgres && { ready=1; break; }
	sleep 1
done
[ -n "$ready" ] || { echo "PostgreSQL 90 秒內沒有起來" >&2; docker rm -f "$NAME" >/dev/null; exit 1; }
docker exec "$NAME" psql -U postgres -Atc "SELECT version()"

echo; echo "### 3. run_q2.py --pg-bigm"
$PY "$CODE/run_q2.py" --port "$PORT" --pg-bigm "$@"; rc=$?
echo "exit=$rc"

echo; echo "### 4. 收掉容器"
if [ "${KEEP:-0}" = 1 ]; then
	echo "KEEP=1：容器 $NAME 還在 127.0.0.1:$PORT（用完 docker rm -f $NAME）"
else
	docker rm -f "$NAME" >/dev/null; echo "docker rm exit=$?"
fi
exit $rc
