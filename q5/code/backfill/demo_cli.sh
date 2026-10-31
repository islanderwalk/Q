#!/bin/bash
# Q5 回補命令列示範：產生樣本、跑文件上的指令、重跑比對、壞資料與分區關卡，並和舊版對照。
# 用法：PY=<python> bash q5/code/backfill/demo_cli.sh <scratch 目錄>
#   輸出全部寫在 scratch 目錄（不能在 repo 裡）；舊版程式用 git show 取到 scratch（唯讀，不動工作樹）：
#   V0＝fc0b075 之前的原型（只讀 *.json），V1＝fc0b075（上一輪：整篇一個時間戳、沒有分區關卡）。
# 每個指令後面記退出碼（echo "exit=$?"，不經過管線）。
set -u
export PYTHONDONTWRITEBYTECODE=1
PY=${PY:-python3}
W=${1:?usage: demo_cli.sh <scratch dir>}
CODE=$(cd "$(dirname "$0")" && pwd)
REPO=$(git -C "$CODE" rev-parse --show-toplevel)
V0=${V0:-fc0b075^}
V1=${V1:-fc0b075}
mkdir -p "$W"
W=$(cd "$W" && pwd)
case "$W" in "$REPO" | "$REPO"/*) echo "scratch 目錄不能在 repo 裡：$W" >&2; exit 2 ;; esac
R=$W/run
rm -rf "$R" "$W/v0" "$W/v1"
mkdir -p "$R"
step() { echo; echo "### $*"; }
short() { cut -c1-"${2:-300}" "$1"; }

extract() { # extract <rev> <目錄>：取出那一版的 backfill.py、normalize.py、tests/fake_es.py（那一版沒有就略過）
	mkdir -p "$2/tests"
	for f in backfill.py normalize.py tests/fake_es.py; do
		git -C "$REPO" show "$1:q5/code/backfill/$f" >"$2/$f" 2>/dev/null || rm -f "$2/$f"
	done
	echo "$1 = $(git -C "$REPO" rev-parse --short "$1"): $(cd "$2" && ls backfill.py normalize.py tests/fake_es.py 2>/dev/null | tr '\n' ' ')"
}

step "0. 版本"
echo "working tree HEAD = $(git -C "$REPO" rev-parse --short HEAD)；本輪程式 = 工作樹的 q5/code/backfill"
extract "$V0" "$W/v0"
extract "$V1" "$W/v1"
$PY --version

step "A. 產生樣本"
$PY "$CODE/make_sample.py" --out "$R/sample" --posts 2000 --reels 200 --seed 5; echo "exit=$?"
find "$R/sample/src" -type f | sort | while read -r f; do echo "$(sha256sum "$f" | cut -c1-16)  $(stat -c %s "$f") B  ${f#"$R"/sample/}"; done
echo "expected.json sha256[:16] = $(sha256sum "$R/sample/expected.json" | cut -c1-16)"

step "B. 原型（V0）跑 python.md 記的示範指令（原型的輸入只有 *.json）"
$PY "$W/v0/backfill.py" --src "$R/sample/src" --out "$R/v0_out" --index threads-posts-v2 --batch-docs 1 >"$R/v0_out.log" 2>&1; echo "exit=$?"
short "$R/v0_out.log"; echo "bulk files: $(ls "$R"/v0_out/bulk_*.ndjson | wc -l)"

step "C. 本輪跑同一個指令（--batch-docs 1）"
$PY "$CODE/backfill.py" --src "$R/sample/src" --out "$R/out_b1" --index threads-posts-v2 --batch-docs 1 >"$R/out_b1.log" 2>&1; echo "exit=$?"
short "$R/out_b1.log"; echo "bulk files: $(ls "$R"/out_b1/bulk_*.ndjson | wc -l)"

step "D. 預設批次（500 篇／5 MB）跑兩次到不同目錄，比對每個輸出檔的 sha256"
$PY "$CODE/backfill.py" --src "$R/sample/src" --out "$R/run1" --index threads-posts-v2 >"$R/run1.log" 2>&1; echo "exit=$?"
$PY "$CODE/backfill.py" --src "$R/sample/src" --out "$R/run2" --index threads-posts-v2 >"$R/run2.log" 2>&1; echo "exit=$?"
(cd "$R/run1" && sha256sum -- * | sort -k2) >"$R/run1.sha"
(cd "$R/run2" && sha256sum -- * | sort -k2) >"$R/run2.sha"
diff "$R/run1.sha" "$R/run2.sha" && echo "IDENTICAL outputs: $(wc -l <"$R/run1.sha") files"
cut -c1-16,65- "$R/run1.sha"
cmp "$R/run1.log" "$R/run2.log" && echo "IDENTICAL stdout"
$PY "$CODE/demo_checks.py" ids "$R/run1" "$R/run2" "$R/out_b1"; echo "exit=$?"

step "E. 同一個輸出目錄再跑一次（覆寫），輸出檔 sha256 不變"
$PY "$CODE/backfill.py" --src "$R/sample/src" --out "$R/run1" --index threads-posts-v2 >/dev/null 2>&1; echo "exit=$?"
(cd "$R/run1" && sha256sum -- * | sort -k2) | diff - "$R/run1.sha" && echo "IDENTICAL after rerun in place"

step "F. 與 make_sample 的 expected.json 獨立核對；quality.json 節錄"
$PY "$CODE/demo_checks.py" compare "$R/run1" "$R/sample/expected.json"; echo "exit=$?"
$PY - "$R/run1/quality.json" <<'PY'
import json, sys
q = json.load(open(sys.argv[1], encoding="utf-8"))
print("gate", q["gate"], "gate_failures", q["gate_failures"])
print("counts", json.dumps(q["counts"], ensure_ascii=False))
for p, s in q["partitions"].items():
    print(f"partition {p} records={s['records']} rejected={s['rejected']} skipped={s['skipped']} unknown_rate={s['unknown_rate']}")
print("keys threads/2026-05-11:", sorted(k for k in q["partitions"]["threads/2026-05-11"]["keys"] if "." in k))
PY

step "G. 壞資料：整檔截斷（.json）"
cp -r "$R/sample/src" "$R/src_badfile"
head -c 5000 "$R/src_badfile/threads/2026-05-04/batch_0001.json" >"$R/trunc.json" && mv "$R/trunc.json" "$R/src_badfile/threads/2026-05-04/batch_0001.json"
$PY "$W/v0/backfill.py" --src "$R/src_badfile" --out "$R/v0_badfile" >"$R/v0_badfile.log" 2>&1; echo "V0 exit=$?"; tail -1 "$R/v0_badfile.log"
$PY "$CODE/backfill.py" --src "$R/src_badfile" --out "$R/badfile" >"$R/badfile.log" 2>&1; echo "exit=$?"; short "$R/badfile.log" 200
ls "$R/badfile"; head -3 "$R/badfile/rejects.jsonl"
$PY "$CODE/backfill.py" --src "$R/src_badfile" --out "$R/badfile_ok" --max-bad-files 1 >"$R/badfile_ok.log" 2>&1; echo "--max-bad-files 1 exit=$?"; short "$R/badfile_ok.log" 160

step "H. 壞資料：2026-05-18 分區每 10 行截斷 1 行"
cp -r "$R/sample/src" "$R/src_badlines"
$PY "$CODE/demo_checks.py" truncate-lines "$R/src_badlines/threads/2026-05-18/part-0000.jsonl" --every 10; echo "exit=$?"
$PY "$CODE/backfill.py" --src "$R/src_badlines" --out "$R/badlines" >"$R/badlines.log" 2>&1; echo "exit=$?"; short "$R/badlines.log" 200
$PY "$W/v0/backfill.py" --src "$R/src_badlines" --out "$R/v0_badlines" >"$R/v0_badlines.log" 2>&1; echo "V0 exit=$?"; short "$R/v0_badlines.log" 120

step "I. 參數錯誤"
$PY "$CODE/backfill.py" --src "$R/nope" --out "$R/x" >"$R/argerr.log" 2>&1; echo "exit=$?"; tail -1 "$R/argerr.log"

step "J. IG（platform=instagram，src 指到 instagram prefix）"
$PY "$CODE/backfill.py" --src "$R/sample/src/instagram" --out "$R/ig" --platform instagram >"$R/ig.log" 2>&1; echo "exit=$?"; short "$R/ig.log" 400
$PY "$CODE/demo_checks.py" compare "$R/ig" "$R/sample/expected.json" --platform instagram; echo "exit=$?"

step "K. 分區關卡：2026-05-11 一週的計數欄位改成別名表沒有的名稱（reposts／quotes／reshares）"
cp -r "$R/sample/src" "$R/src_renamed"
$PY "$CODE/demo_checks.py" rename-keys "$R/src_renamed/threads/2026-05-11/batch_0001.json" repost_cnt=reposts quote_cnt=quotes reshare_cnt=reshares; echo "exit=$?"
$PY "$W/v1/backfill.py" --src "$R/src_renamed" --out "$R/v1_renamed" >"$R/v1_renamed.log" 2>&1; echo "V1 exit=$?"
$PY -c 'import json,sys; q=json.load(open(sys.argv[1])); m=json.load(open(sys.argv[2])); print("V1 gate_failures", q["gate_failures"], "skipped_no_known_metric", m["skipped_no_known_metric"], "docs_written", m["total_docs"])' "$R/v1_renamed/quality.json" "$R/v1_renamed/manifest.json"
$PY "$CODE/backfill.py" --src "$R/src_renamed" --out "$R/renamed" >"$R/renamed.log" 2>&1; echo "exit=$?"
$PY -c 'import json,sys; q=json.load(open(sys.argv[1])); s=q["partitions"]["threads/2026-05-11"]; print("gate_failures", q["gate_failures"]); print("threads/2026-05-11 records", s["records"], "unknown_rate", s["unknown_rate"], "count keys", sorted(k for k in s["keys"] if k.startswith("text_post_app_info.")))' "$R/renamed/quality.json"
ls "$R/renamed"
$PY "$CODE/backfill.py" --src "$R/src_renamed" --out "$R/renamed_ok" --max-unknown-rate 1 >"$R/renamed_ok.log" 2>&1; echo "--max-unknown-rate 1 exit=$?"; short "$R/renamed_ok.log" 120

step "L. 送出端遇到單筆 429：原型（V0）與本輪"
$PY "$CODE/demo_checks.py" item429 --code "$W/v0" --fake-es "$CODE/tests/fake_es.py" --work "$R/item429_v0"; echo "V0 exit=$?"
$PY "$CODE/demo_checks.py" item429 --code "$CODE" --fake-es "$CODE/tests/fake_es.py" --work "$R/item429_now"; echo "exit=$?"

step "M. 時間戳：線上 T2 已有 repost 20，回補只有 T1（三欄）與 T3（缺 repost key）：上一輪（V1）與本輪"
$PY "$CODE/demo_checks.py" guard-mixed --code "$W/v1" --fake-es "$W/v1/tests/fake_es.py" --work "$R/guard_v1"; echo "V1 exit=$?"
$PY "$CODE/demo_checks.py" guard-mixed --code "$CODE" --fake-es "$CODE/tests/fake_es.py" --work "$R/guard_now"; echo "exit=$?"
