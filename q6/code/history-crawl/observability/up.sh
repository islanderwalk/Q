# Starts the crawl observability stack (Pushgateway, Prometheus, Loki, Grafana) and waits until each one is ready.
# Ports are bound to 127.0.0.1 only (see docker-compose.yml). Dashboard: http://localhost:3000/d/crawler-monitor
cd "$(dirname "$0")"
docker compose up -d 2>&1 | tail -4
ready() {  # name url pattern
  for i in $(seq 1 60); do
    curl -s "$2" | grep -qi "$3" && echo "$1 ready" && return 0
    sleep 2
  done
  echo "$1 NOT READY" && return 1
}
ready pushgateway localhost:9091/-/ready OK
ready prometheus localhost:9090/-/ready Ready
ready loki localhost:3100/ready ready
ready grafana localhost:3000/api/health '"database": *"ok"'
