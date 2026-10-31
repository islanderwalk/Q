#!/bin/bash
# Q5 回補對真的 Elasticsearch：用官方映像在本機起單節點 ES，跑 make_sample.py 與 demo_real_es.py，最後刪掉容器。
# 用法：PY=<python> bash q5/code/backfill/demo_real_es.sh <scratch 目錄> [ES 映像，預設 elasticsearch:8.19.23] [本機埠，預設 19200]
#   需要 Docker；ES 只綁 127.0.0.1，關掉安全性與 ML（本機示範用）。
#   disk.threshold_enabled=false：雲端容器的磁碟回報為 252 GB 用了 96%（實際可寫的是每個 session 的額度），
#   不關掉的話 ES 依磁碟水位拒絕配置 shard，建 index 後一直是 red。
# 每個指令後面記退出碼（echo "exit=$?"，不經過管線）。
set -u
export PYTHONDONTWRITEBYTECODE=1
PY=${PY:-python3}
W=${1:?usage: demo_real_es.sh <scratch dir> [image] [port]}
IMAGE=${2:-elasticsearch:8.19.23}
PORT=${3:-19200}
CODE=$(cd "$(dirname "$0")" && pwd)
REPO=$(git -C "$CODE" rev-parse --show-toplevel)
mkdir -p "$W"
W=$(cd "$W" && pwd)
case "$W" in "$REPO" | "$REPO"/*) echo "scratch 目錄不能在 repo 裡：$W" >&2; exit 2 ;; esac
NAME=q5-real-es-$PORT
URL=http://127.0.0.1:$PORT

echo "### 0. 版本"
echo "working tree HEAD = $(git -C "$REPO" rev-parse --short HEAD)；本輪程式 = 工作樹的 q5/code/backfill"
$PY --version
docker version --format 'docker server {{.Server.Version}}'
docker pull -q "$IMAGE" >/dev/null; echo "pull exit=$?"
echo "image = $IMAGE  $(docker inspect --format '{{index .RepoDigests 0}}' "$IMAGE")"

echo; echo "### 1. 起 ES（單節點）"
docker rm -f "$NAME" >/dev/null 2>&1
docker run -d --name "$NAME" -p "127.0.0.1:$PORT:9200" -e discovery.type=single-node \
	-e xpack.security.enabled=false -e xpack.ml.enabled=false \
	-e cluster.routing.allocation.disk.threshold_enabled=false \
	-e ES_JAVA_OPTS="-Xms1g -Xmx1g" "$IMAGE" >/dev/null
echo "docker run exit=$?"
for _ in $(seq 1 90); do
	curl -sf -o /dev/null "$URL/" && break
	sleep 2
done
curl -sS "$URL/_cluster/health?filter_path=status,number_of_nodes"; echo

echo; echo "### 2. 產生樣本"
$PY "$CODE/make_sample.py" --out "$W/sample" --posts 2000 --reels 200 --seed 5 >/dev/null; echo "exit=$?"
echo "expected.json sha256[:16] = $(sha256sum "$W/sample/expected.json" | cut -c1-16)"

echo; echo "### 3. demo_real_es.py"
$PY "$CODE/demo_real_es.py" "$W" "$URL"; rc=$?
echo "exit=$rc"

echo; echo "### 4. 收掉容器"
docker rm -f "$NAME" >/dev/null; echo "docker rm exit=$?"
exit $rc
