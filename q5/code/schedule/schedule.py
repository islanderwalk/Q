#!/usr/bin/env python3
"""Q5 (b) 工時、關鍵路徑、日曆天的重算（q5/answer/b-c-d.md 的數字由這支程式產生）。

模型：每個角色一個人，同一人手上的工作依序做；工作要等依賴完成才開始（finish-to-start），
同一人有多件可做時先做編號小的。每日 8 小時；先算純工時，再乘審查修正係數、加緩衝天數。

依賴有兩種讀法（b-c-d.md 表格的「依賴」欄）：
- strict：4 要等 1 做完、7 要等 5 做完。
- contract：4 只要欄位名（開工當天定案）、7 只要 5 的 API 契約（5 開工時定案）。

用法：python schedule.py [--mode contract|strict] [--start-weekday 0..6]
"""
import argparse
import math

HOURS_PER_DAY = 8
REVIEW_FACTOR = 1.3  # 審查與修正
BUFFER_DAYS = 1

# 編號: (甘特圖標籤, 角色, 工時, strict 依賴, contract 依賴)；依賴寫成 (編號, "end" 或 "start")
TASKS = {
    1: ("mapping", "DE", 6, [], []),                                     # ES mapping 更新
    2: ("crawler", "DE", 12, [(1, "end")], [(1, "end")]),                # 爬蟲寫入
    3: ("backfill", "DE", 16, [(1, "end")], [(1, "end")]),               # 歷史回補：12 開發＋4 執行核對
    4: ("data-layer", "BE", 8, [(1, "end")], []),                        # Data Layer API
    5: ("analytics", "BE", 10, [(4, "end")], [(4, "end")]),              # Backend Analytics API
    6: ("export", "BE", 6, [(5, "end")], [(5, "end")]),                  # Export Service
    7: ("frontend", "FE", 10, [(5, "end")], [(5, "start")]),             # 前端顯示
    8: ("integrate", "DE", 8, [(2, "end"), (3, "end"), (7, "end")],
        [(2, "end"), (3, "end"), (7, "end")]),                           # 端到端整合＋SLA 監控
    9: ("accept", "ALL", 4, [(8, "end")], [(8, "end")]),                 # 驗收：與 PM 確認定義
}
ROLES = ("DE", "BE", "FE")


def schedule(mode="contract", tasks=TASKS):
    """回傳 {編號: (開始小時, 結束小時)}。ALL 表示三個角色同時參加。"""
    dep_idx = 3 if mode == "strict" else 4
    free = {r: 0 for r in ROLES}
    times, left = {}, set(tasks)
    while left:
        ready = sorted(t for t in left if all(d in times for d, _ in tasks[t][dep_idx]))
        t = ready[0]
        _name, role, hours, *_ = tasks[t]
        dep_time = max((times[d][0 if kind == "start" else 1] for d, kind in tasks[t][dep_idx]), default=0)
        roles = ROLES if role == "ALL" else (role,)
        start = max([dep_time] + [free[r] for r in roles])
        times[t] = (start, start + hours)
        for r in roles:
            free[r] = start + hours
        left.discard(t)
    return times


def role_hours(tasks=TASKS):
    out = {r: 0 for r in ROLES + ("ALL",)}
    for _name, role, hours, *_ in tasks.values():
        out[role] += hours
    return out


def critical_chain(times, mode="contract", tasks=TASKS):
    """從最後完成的工作往回找：每一步取「讓它不能更早開始」的前一件（依賴或同一人的前一件工作）。"""
    dep_idx = 3 if mode == "strict" else 4
    t = max(times, key=lambda k: times[k][1])
    chain = [t]
    while times[t][0] > 0:
        start = times[t][0]
        cands = [d for d, kind in tasks[t][dep_idx] if times[d][0 if kind == "start" else 1] == start]
        role = tasks[t][1]
        cands += [k for k in times if k != t and times[k][1] == start
                  and (role == "ALL" or tasks[k][1] in (role, "ALL"))]
        t = min(cands)
        chain.append(t)
    return chain[::-1]


def total_float(times, mode="contract", tasks=TASKS):
    """總浮時（小時）：在不延後整個專案的前提下，每件工作最多能晚多久完成。

    倒推時把依賴和「同一人的下一件工作」都當成先後關係；7 依賴 5 的開始時，5 的最晚完成＝7 的最晚開始＋5 的工時。
    """
    dep_idx = 3 if mode == "strict" else 4
    project = max(e for _, e in times.values())
    succ = {t: [] for t in times}
    for k, spec in tasks.items():
        for d, kind in spec[dep_idx]:
            succ[d].append((k, kind))
    for t in times:  # 同一人的下一件工作
        role = tasks[t][1]
        later = [k for k in times if k != t and times[k][0] >= times[t][1]
                 and (role == "ALL" or tasks[k][1] in (role, "ALL"))]
        if later:
            succ[t].append((min(later, key=lambda k: times[k][0]), "end"))
    latest_finish = {}
    while len(latest_finish) < len(times):  # 後繼都算完的先算（倒推的拓撲順序）
        t = min(k for k in times if k not in latest_finish and all(s in latest_finish for s, _ in succ[k]))
        lf = project
        for k, kind in succ[t]:
            ls_k = latest_finish[k] - tasks[k][2]
            lf = min(lf, ls_k if kind == "end" else ls_k + tasks[t][2])
        latest_finish[t] = lf
    return {t: latest_finish[t] - times[t][1] for t in times}


def working_days(project_hours):
    raw = project_hours / HOURS_PER_DAY
    with_review = raw * REVIEW_FACTOR
    total = math.ceil(with_review) + BUFFER_DAYS
    return raw, with_review, total


def calendar_days(work_days, start_weekday=0):
    """從 start_weekday（0＝週一）開工，第 work_days 個工作天收工，含頭尾共幾個日曆天（週六日不上班）。"""
    day, done, wd = 0, 0, start_weekday
    while True:
        day += 1
        if wd < 5:
            done += 1
            if done == work_days:
                return day
        wd = (wd + 1) % 7


def gantt(times, tasks=TASKS, cell=2):
    """文字甘特圖：每格 cell 小時，■＝工作，每 8 小時一條 | 分隔。"""
    end = max(e for _, e in times.values())
    cells = math.ceil(end / cell)
    per_day = HOURS_PER_DAY // cell
    head = "".join(f"D{d + 1}".ljust(per_day + 1) for d in range(math.ceil(cells / per_day)))
    rows = [" " * 22 + head.rstrip()]
    for t in sorted(times):
        s, e = times[t]
        line = ""
        for c in range(cells):
            if c and c % per_day == 0:
                line += "|"
            line += "■" if s <= c * cell < e else "·"
        rows.append(f"{tasks[t][1]:<3} {t} {tasks[t][0]:<16}"[:22].ljust(22) + line + f"  {s}-{e}h")
    return "\n".join(rows)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Q5 (b) 工時與日程重算")
    ap.add_argument("--mode", choices=("contract", "strict"), default="contract")
    ap.add_argument("--start-weekday", type=int, default=0, help="0＝週一 … 4＝週五")
    args = ap.parse_args(argv)
    times = schedule(args.mode)
    hours = role_hours()
    project = max(e for _, e in times.values())
    raw, with_review, total = working_days(project)
    chain = critical_chain(times, args.mode)
    print(f"mode={args.mode}")
    print(f"role_hours DE={hours['DE']} BE={hours['BE']} FE={hours['FE']} ALL={hours['ALL']}")
    print(f"project_hours={project} critical_chain={'→'.join(map(str, chain))} "
          f"chain_hours={sum(TASKS[t][2] for t in chain)}")
    floats = total_float(times, args.mode)
    print("total_float_h " + " ".join(f"{t}:{f}" for t, f in sorted(floats.items())))
    print("zero_float " + ",".join(str(t) for t, f in sorted(floats.items()) if f == 0))
    print(f"working_days raw={raw:.2f} x{REVIEW_FACTOR}={with_review:.3f} ceil+{BUFFER_DAYS}={total}")
    print("calendar_days_by_start " + " ".join(f"{'一二三四五'[w]}={calendar_days(total, w)}" for w in range(5)))
    print(gantt(times))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
