#!/usr/bin/env bash
# PTT Q2 文章內文爬蟲的控制腳本（WSL 用，以 root 身分執行）。
#   用法：wsl -u root -- bash <本腳本的 WSL 路徑> {start|stop|status}
#   start   以 systemd transient service（ptt-q2-shard<i>）啟動 $SHARDS 個 shard；已在跑的不重複啟動。
#           log 寫到 /root/q2ptt-logs/shard<i>.log。crash 後 60 秒自動重啟（Restart=on-failure）。
#           WSL 的 systemd 是 PID 1，所以關掉 Windows 終端、wsl 工作階段結束後行程仍會繼續跑。
#   stop    停止 3 個 shard（SIGTERM；每篇都已 commit，中斷後 start 會接著抓）。
#   status  印出各 shard 狀態、各看板已抓幾篇（依 status）、最近 10 分鐘抓幾篇、每個 shard log 最後 5 行，
#           以及清單收集器狀態。
#   lists-start  每個看板各啟動一支清單收集器 ptt_q2_lists.py（systemd transient service
#                ptt-q2-lists-<board>），平行翻，寫到 /root/q2ptt-lists，
#                log 寫到 /root/q2ptt-logs/lists-<board>.log。
#   lists-stop   停止所有清單收集器（含舊版單支的 ptt-q2-lists；狀態每頁都已存，lists-start 會接著翻）。
set -u

if [ "$(id -u)" != "0" ]; then
  echo "請以 root 執行：wsl -u root -- bash <本腳本路徑> $*" >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY=/root/q2ptt-venv/bin/python
CRAWL="$SCRIPT_DIR/ptt_q2_crawl.py"
LISTS="$SCRIPT_DIR/ptt_q2_lists.py"
LISTS_UNIT_OLD=ptt-q2-lists   # 舊版：一支翻全部看板
LISTS_BOARDS="C_Chat movie joke Japan_Travel NBA BabyMother"   # 每板一支
SHARDS=48            # shard 數（3 → 6 → 12 → 24 → 48，加快速度）
GAP=0.25             # 每個 shard 每次請求前等幾秒（3 → 1.5 → 1.0 → 0.8 → 0.25）
JITTER=0.1           # ± 隨機秒數（1 → 0.5 → 0.3 → 0.2 → 0.1）
LISTS_TARGET=850000  # 新清單（6 板合計）收到幾筆就停（800000 → 850000，加 q6 清單後確保有內文的能湊滿 100 萬）
LISTS_GAP=0.5        # 每支清單收集器每頁間隔（3.5 → 1.5 → 1.0 → 0.5；之後改成每板一支，6 支各自 0.5）
LOG_DIR=/root/q2ptt-logs
DB=q2_ptt
mkdir -p "$LOG_DIR"

unit() { echo "ptt-q2-shard$1"; }

# active / activating（含 Restart 等待中）都算在跑
is_running() {
  case "$(systemctl is-active "$(unit "$1")" 2>/dev/null)" in
    active|activating|reloading) return 0 ;;
    *) return 1 ;;
  esac
}

start() {
  local i u
  for i in $(seq 0 $((SHARDS - 1))); do
    u=$(unit "$i")
    if is_running "$i"; then
      echo "shard$i 已在跑（$u）"
      continue
    fi
    systemctl reset-failed "$u" 2>/dev/null
    if systemd-run --unit="$u" --description="PTT Q2 crawl shard $i/$SHARDS" --collect \
        --property=Restart=on-failure --property=RestartSec=60 \
        --property=StandardOutput="append:$LOG_DIR/shard$i.log" \
        --property=StandardError="append:$LOG_DIR/shard$i.log" \
        --setenv=PYTHONUNBUFFERED=1 \
        "$PY" "$CRAWL" --shard "$i" --shards "$SHARDS" --gap "$GAP" --jitter "$JITTER" >/dev/null 2>&1; then
      echo "shard$i 已啟動（$u，log: $LOG_DIR/shard$i.log）"
    else
      echo "shard$i 啟動失敗，請看 journalctl -u $u" >&2
    fi
  done
}

stop() {
  local i u
  for i in $(seq 0 $((SHARDS - 1))); do
    u=$(unit "$i")
    if is_running "$i"; then
      systemctl stop "$u" && echo "shard$i 已停止（$u）"
    else
      echo "shard$i 本來就沒在跑"
    fi
  done
}

status() {
  local i u
  echo "== shard 行程"
  for i in $(seq 0 $((SHARDS - 1))); do
    u=$(unit "$i")
    if is_running "$i"; then
      echo "shard$i: 執行中（$u, $(systemctl show "$u" -p MainPID --value 2>/dev/null) pid）"
    else
      echo "shard$i: 未執行（$(systemctl is-active "$u" 2>/dev/null)）"
    fi
  done
  echo
  echo "== 各看板已抓（status 200=有內文, 404=已刪）"
  psql -d "$DB" -c "SELECT board, status, count(*) AS n FROM ptt_articles GROUP BY 1, 2 ORDER BY 1, 2"
  psql -d "$DB" -c "SELECT count(*) AS total, count(*) FILTER (WHERE fetched_at > now() - interval '10 minutes') AS last_10min FROM ptt_articles"
  echo
  echo "== 各 shard log 最後 5 行"
  for i in $(seq 0 $((SHARDS - 1))); do
    echo "--- shard$i"
    if [ -f "$LOG_DIR/shard$i.log" ]; then tail -n 5 "$LOG_DIR/shard$i.log"; else echo "(尚無 log)"; fi
  done
}

lists_unit() { echo "ptt-q2-lists-$1"; }

unit_running() {
  case "$(systemctl is-active "$1" 2>/dev/null)" in
    active|activating|reloading) return 0 ;;
    *) return 1 ;;
  esac
}

lists_start() {
  local b u
  if unit_running "$LISTS_UNIT_OLD"; then
    echo "舊版單支清單收集器（$LISTS_UNIT_OLD）還在跑，請先 lists-stop" >&2
    return 1
  fi
  for b in $LISTS_BOARDS; do
    u=$(lists_unit "$b")
    if unit_running "$u"; then
      echo "$b 清單收集器已在跑（$u）"
      continue
    fi
    systemctl reset-failed "$u" 2>/dev/null
    if systemd-run --unit="$u" --description="PTT Q2 list collector $b" --collect \
        --property=Restart=on-failure --property=RestartSec=60 \
        --property=StandardOutput="append:$LOG_DIR/lists-$b.log" \
        --property=StandardError="append:$LOG_DIR/lists-$b.log" \
        --setenv=PYTHONUNBUFFERED=1 \
        "$PY" "$LISTS" --boards "$b" --target "$LISTS_TARGET" --gap "$LISTS_GAP" --jitter "$JITTER" >/dev/null 2>&1; then
      echo "$b 清單收集器已啟動（$u，log: $LOG_DIR/lists-$b.log）"
    else
      echo "$b 清單收集器啟動失敗，請看 journalctl -u $u" >&2
    fi
  done
}

lists_stop() {
  local b u
  for u in "$LISTS_UNIT_OLD" $(for b in $LISTS_BOARDS; do lists_unit "$b"; done); do
    if unit_running "$u"; then
      systemctl stop "$u" && echo "已停止 $u"
    else
      echo "$u 本來就沒在跑"
    fi
  done
}

lists_status() {
  local b
  echo
  echo "== 清單收集器"
  for b in $LISTS_BOARDS; do
    echo "$b: $(systemctl is-active "$(lists_unit "$b")" 2>/dev/null)"
  done
  if ls /root/q2ptt-lists/*.jsonl >/dev/null 2>&1; then wc -l /root/q2ptt-lists/*.jsonl; fi
  for b in $LISTS_BOARDS; do
    if [ -f "$LOG_DIR/lists-$b.log" ]; then tail -n 1 "$LOG_DIR/lists-$b.log"; fi
  done
}

case "${1:-status}" in
  start) start ;;
  stop) stop ;;
  status) status; lists_status ;;
  lists-start) lists_start ;;
  lists-stop) lists_stop ;;
  *) echo "用法：$0 {start|stop|status|lists-start|lists-stop}" >&2; exit 2 ;;
esac
