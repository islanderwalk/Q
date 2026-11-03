#!/bin/bash
# 容器實測（需要 Docker 與 curl；pytest 不會跑它）。用法：smoke.sh <映像> <本機 port>，例如 smoke.sh qsearch-parser:current 18091
# 啟動容器、送幾種 push 請求、docker stop，逐項和「修正後」的預期比對；全部相符退出 0，有不符退出 1。
# 修正前的映像跑這支會出現不符，這正是要呈現的差異。結果以印出的每一行為準。
set -u
image=$1; port=$2; name=parser-smoke-$port; fail=0
post() { curl -s --noproxy '*' -o /dev/null -w '%{http_code}' -H 'content-type: application/json' -d "$1" "http://127.0.0.1:$port$2"; }
push() { post "{\"message\":{\"data\":\"$(printf '%s' "$1" | base64 -w0)\",\"messageId\":\"$2\"}}" /events/crawl-fetched; }
check() {  # check <項目> <實際> <預期>
    if [ "$2" = "$3" ]; then echo "$1: $2 ok"; else echo "$1: $2 MISMATCH (expected $3)"; fail=1; fi
}
GOOD='{"event_id":"evt-0001","schema_version":1,"job_id":"job-1","platform":"ptt","event_type":"crawl.fetched","raw_data_uri":"gs://demo-raw-scraped/ptt/a.html"}'

docker rm -f "$name" >/dev/null 2>&1
docker run -d --name "$name" -p "127.0.0.1:$port:8080" "$image" >/dev/null || { echo "docker run failed"; exit 1; }
for _ in $(seq 1 40); do curl -s --noproxy '*' -o /dev/null "http://127.0.0.1:$port/health" && break; sleep 0.25; done

pid1=$(docker exec "$name" cat /proc/1/cmdline | tr '\0' ' ')
echo "pid1: $pid1"
case "$pid1" in sh\ *|/bin/sh\ *) check "pid1 is uvicorn" no yes ;; *uvicorn*) check "pid1 is uvicorn" yes yes ;; *) check "pid1 is uvicorn" no yes ;; esac
check "GET /health" "$(curl -s --noproxy '*' -o /dev/null -w '%{http_code}' "http://127.0.0.1:$port/health")" 200
check "valid (1st)" "$(push "$GOOD" m1)" 204
check "valid (dup)" "$(push "$GOOD" m1)" 204
# 這兩項 Avro 不收，經過有 schema 的主題時在發佈端就被擋；parser 拒收是與 .avsc 對齊的防線。
check "no schema_version (Avro rejects)" "$(push "${GOOD/\"schema_version\":1,/}" m2)" 422
check "schema_version \"1\" (Avro rejects)" "$(push "${GOOD/\"schema_version\":1/\"schema_version\":\"1\"}" m3)" 422
# 這項 Avro 合法，會送到 parser。
check "uri gs://bucket (Avro accepts)" "$(push "${GOOD/gs:\/\/demo-raw-scraped\/ptt\/a.html/gs://bucket}" m4)" 422
check "bad base64" "$(post '{"message":{"data":"!!!","messageId":"m5"}}' /events/crawl-fetched)" 422
check "unwrapped body" "$(post "$GOOD" /events/crawl-fetched)" 422
check "POST /push" "$(post '{}' /push)" 404

s=$(date +%s%N); docker stop -t 10 "$name" >/dev/null; e=$(date +%s%N)
ms=$(( (e - s) / 1000000 )); code=$(docker inspect -f '{{.State.ExitCode}}' "$name")
echo "docker stop -t 10: $((ms / 1000)).$(printf '%03d' $((ms % 1000))) s"
check "stopped within 5 s" "$([ "$ms" -lt 5000 ] && echo yes || echo no)" yes
check "container exit code" "$code" 0
docker logs "$name" 2>&1 | grep -E "Shutting down|Finished server process" | sed 's/^/log: /'
docker rm "$name" >/dev/null
echo "result: $([ "$fail" = 0 ] && echo all ok || echo MISMATCH)"
exit "$fail"
