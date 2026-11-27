"""Watchdog for the historical crawlers and the crawlobs monitoring stack.   pythonw watchdog.py   (one instance; Startup folder)

Every LOOP_S seconds:
  1. Stack: Prometheus / Loki / Pushgateway / Grafana ready?  If not, `docker compose -p crawlobs up -d` inside WSL, wait for
     ready, then backfill.py over the whole Loki window, so minutes the stack missed come back at their original timestamps.
  2. Crawlers: a live process (same script + same CRAWL_WORKDIR) is adopted; a missing one is started again.
     crash -> back off 5, 10, 20 .. 60 min; clean exit -> cooldown (PTT and Meta 30 min, YouTube until the quota resets);
     telemetry silent > HUNG_S while the process lives and the network is up -> hung: kill the tree, restart.
     Meta platforms stopped for a session reason (logout / checkpoint ...) are left out until login_meta.py logs them in
     again; the crawler never tries to get past a login wall and neither does this.
  3. Every new crawler event that means trouble becomes a pitfall: pitfalls.jsonl + a dashboard screenshot (at most one per
     crawler and kind every SHOT_EVERY_S).
  4. Every BACKFILL_EVERY_S: backfill the last 3 h, so short sink blips heal without a stack restart.
Q3's containers (Grafana 3300, Prometheus 9390, Pushgateway 9391, ...) are a different compose project and are never touched.
"""
import json, os, pathlib, subprocess, sys, time, traceback
from datetime import datetime, timedelta, timezone
import psutil
import requests

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from telem import TW, Telemetry, net_up  # noqa: E402
import backfill  # noqa: E402

ROOT = pathlib.Path(os.environ.get("CRAWL_ROOT") or HERE.parent.parent / "crawl")  # q6dl/runtime/history-crawl -> q6dl/crawl
YT_KEY = os.environ.get("YOUTUBE_API_KEY_FILE") or str(HERE.parent.parent / "youtube_api_key.txt")
WD = ROOT / "watchdog"
LOGS = ROOT / "logs"
PY = pathlib.Path(sys.executable).with_name("python.exe")  # pythonw for us, python (no window) for the crawlers
LOOP_S, HUNG_S, SHOT_EVERY_S, BACKFILL_EVERY_S, AFTER_RECOVERY_S = 120, 3600, 1800, 1800, 300
# 127.0.0.1, not localhost: Windows tries ::1 first and WSL only forwards IPv4.
STACK = {"prometheus": "http://127.0.0.1:9090/-/ready", "loki": "http://127.0.0.1:3100/ready",
         "pushgateway": "http://127.0.0.1:9091/-/ready", "grafana": "http://127.0.0.1:3000/api/health"}
PGW = "http://127.0.0.1:9091/metrics/job"
STATE_CODE = {"running": 1, "done": 2}  # crawler_up: 1 running, 2 finished and waiting for its next round, 0 anything else
_obs = str(HERE / "observability")
OBS_WSL = "/mnt/" + _obs[0].lower() + _obs[2:].replace("\\", "/")  # C:\a\b -> /mnt/c/a/b, for docker compose inside WSL
PT = timezone(timedelta(hours=-8))  # the crawler's quota day (crawl_youtube.QDAY)
SESSION_OK = {"all_targets_done"}  # a PLATFORM_STOPPED reason that is a finish, not a lost session
# Event kinds that are recorded as pitfalls (the crawler already slowed itself down; this is the paper trail).
TROUBLE = {"anomaly", "network_down", "channel_skipped", "crash", "stopped", "PLATFORM_STOPPED", "step_error", "push_failed",
           "shot_error", "template_missing_fallback_scroll", "conn_error_network_up", "upload_budget_reached",
           "total_budget_reached", "channel_daily_cap", "head_daily_cap", "session_cookie_rotated"}
CRAWLERS = {
    "ptt": {"script": "crawl_ptt.py", "env": {"CRAWL_WORKDIR": ROOT}, "job": "ptt_crawl", "platforms": ["ptt"],
            "events": ["ptt/telemetry/events.jsonl"], "telemetry": ["ptt/telemetry"], "cooldown": "30m",
            "done_mark": "PTT_DONE"},
    "youtube": {"script": "crawl_youtube.py", "env": {"CRAWL_WORKDIR": ROOT, "YOUTUBE_API_KEY_FILE": YT_KEY}, "job": "youtube_api",
                "platforms": ["youtube"], "events": ["youtube/telemetry/events.jsonl"], "telemetry": ["youtube/telemetry"],
                "cooldown": "quota", "done_mark": "YT_DONE"},
    "deep": {"script": "crawl_deep.py", "env": {"CRAWL_WORKDIR": ROOT / "metalogin", "META_WORKDIR": ROOT / "metalogin"},
             "job": "meta_crawl", "platforms": ["facebook", "instagram", "threads"],
             "events": [f"metalogin/deep/telemetry/{p}/events.jsonl" for p in ("facebook", "instagram", "threads")],
             "telemetry": [f"metalogin/deep/telemetry/{p}" for p in ("facebook", "instagram", "threads")], "cooldown": "30m",
             "done_mark": "ALL_DONE"},
}


def now_iso():
    return datetime.now(TW).isoformat(timespec="seconds")


def log(*a):
    print(now_iso(), *a, flush=True)


class Watchdog:
    def __init__(self):
        for d in (WD, LOGS, WD / "shots"):
            d.mkdir(parents=True, exist_ok=True)
        self.state_f = WD / "state.json"
        st = json.loads(self.state_f.read_text(encoding="utf-8")) if self.state_f.exists() else {}
        self.offsets = st.get("offsets", {})  # events file -> byte offset already read
        self.c = {n: {"proc": None, "pid": None, "started": 0.0, "fails": 0, "not_before": 0.0, "status": "unknown",
                      **st.get("crawlers", {}).get(n, {})} for n in CRAWLERS}
        for n in self.c:
            self.c[n]["proc"] = None
        self.shot_at = st.get("shot_at", {})
        self.last_backfill = st.get("last_backfill", 0.0)
        self.stack_down_since = None
        self.tel = Telemetry("watchdog", WD / "telemetry", "watchdog")
        for f in self._event_files():  # first run: only what happens from now on is news
            self.offsets.setdefault(str(f), f.stat().st_size if f.exists() else 0)

    # ---- bookkeeping
    def save(self):
        st = {"t": now_iso(), "offsets": self.offsets, "shot_at": self.shot_at, "last_backfill": self.last_backfill,
              "crawlers": {n: {k: v for k, v in c.items() if k != "proc"} for n, c in self.c.items()}}
        tmp = self.state_f.with_suffix(".tmp")
        tmp.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.state_f)

    def pitfall(self, crawler, kind, detail, action, shoot=True):
        """One line in pitfalls.jsonl (+ watchdog event in Loki) and, rate-limited, a dashboard screenshot."""
        rec = {"t": now_iso(), "crawler": crawler, "kind": kind, "detail": detail, "action": action, "shot": None}
        key = f"{crawler}:{kind}"
        if shoot and time.time() - self.shot_at.get(key, 0) >= SHOT_EVERY_S and self.stack_ok():
            rec["shot"] = self.shoot(f"{datetime.now(TW):%Y%m%d_%H%M%S}_{crawler}_{kind}")
            if rec["shot"]:
                self.shot_at[key] = time.time()
        with open(WD / "pitfalls.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        self.tel.event("pitfall", platform=crawler, trouble=kind, detail=str(detail)[:300], action=action, shot=rec["shot"])
        log("PITFALL", crawler, kind, action, str(detail)[:200])

    def shoot(self, prefix):
        try:
            r = subprocess.run([str(PY), str(HERE / "observability" / "shot.py"), str(WD / "shots"), prefix, "6h"],
                               capture_output=True, text=True, timeout=180, creationflags=subprocess.CREATE_NO_WINDOW)
            out = r.stdout.strip().splitlines()
            return out[-1] if r.returncode == 0 and out else None
        except Exception as e:
            log("shot failed", repr(e))
            return None

    # ---- 1. monitoring stack
    def stack_down(self):
        bad = []
        for name, url in STACK.items():
            try:
                if requests.get(url, timeout=5).status_code != 200:
                    bad.append(name)
            except requests.RequestException:
                bad.append(name)
        return bad

    def stack_ok(self):
        return not self.stack_down()

    def check_stack(self):
        bad = self.stack_down()
        if not bad:
            return True
        self.stack_down_since = self.stack_down_since or time.time()
        self.pitfall("stack", "stack_down", {"down": bad}, "docker compose up -d, then backfill", shoot=False)
        cmd = ["wsl", "-d", "Ubuntu", "-u", "root", "--", "bash", "-lc", f"cd '{OBS_WSL}' && docker compose -p crawlobs up -d"]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=600, creationflags=subprocess.CREATE_NO_WINDOW)
            log("compose up", r.returncode, (r.stdout + r.stderr)[-400:])
        except Exception as e:
            log("compose up failed", repr(e))
        for _ in range(60):
            if not self.stack_down():
                break
            time.sleep(5)
        bad = self.stack_down()
        if bad:
            self.pitfall("stack", "stack_still_down", {"down": bad}, "retry next loop", shoot=False)
            return False
        down_s = round(time.time() - self.stack_down_since)
        self.stack_down_since = None
        res = self.backfill(backfill.LOKI_MAX_AGE_H)
        # Crawlers keep writing push_failed lines until their next push gets through, after this backfill ran; a second
        # 3 h pass AFTER_RECOVERY_S later picks those up (seen 2026-11-03: 4 lines left over after an 86 s outage).
        self.last_backfill = time.time() - BACKFILL_EVERY_S + AFTER_RECOVERY_S
        self.pitfall("stack", "stack_recovered", {"down_s": down_s, "backfill": res}, "backfilled the gap")
        return True

    def backfill(self, hours):
        try:
            res = backfill.run([str(ROOT), str(ROOT / "metalogin")], hours)
            self.last_backfill = time.time()
            summ = {"loki_pushed": {k: v["pushed"] for k, v in res["loki"].items()},
                    "prom_written": {k: v["written"] for k, v in res["prometheus"].items()}}
            log("backfill", json.dumps(summ, ensure_ascii=False)[:400])
            with open(WD / "backfill.jsonl", "a", encoding="utf-8") as f:
                f.write(json.dumps(res, ensure_ascii=False) + "\n")
            return summ
        except Exception as e:
            self.pitfall("stack", "backfill_error", repr(e)[:300], "retry next round", shoot=False)
            return None

    # ---- 2. crawler processes
    def find(self, name):
        spec, want = CRAWLERS[name], str(CRAWLERS[name]["env"]["CRAWL_WORKDIR"]).lower()
        for p in psutil.process_iter(["name", "cmdline"]):
            try:
                cl = p.info["cmdline"] or []
                if "python" in (p.info["name"] or "").lower() and any(a.endswith(spec["script"]) for a in cl):
                    if (p.environ().get("CRAWL_WORKDIR") or "").lower().rstrip("\\") == want.rstrip("\\"):
                        return p
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        return None

    def kill_tree(self, p):
        try:
            kids = p.children(recursive=True)
        except psutil.NoSuchProcess:
            return
        for x in [p] + kids:
            try:
                x.kill()
            except psutil.NoSuchProcess:
                pass
        psutil.wait_procs([p] + kids, timeout=30)

    def idle_pushgateway(self, name):
        """A dead crawler's last push stays in Pushgateway forever and Grafana draws it as a live flat line; zero the rates."""
        spec = CRAWLERS[name]
        for p in spec["platforms"]:
            body = "crawl_requests_per_min 0\ncrawl_new_posts_per_min 0\ncrawl_running 0\n"
            try:
                requests.post(f"{PGW}/{spec['job']}/platform/{p}", data=body.encode(), timeout=5)
            except requests.RequestException:
                pass

    def push_status(self):
        """One gauge per crawler plus a heartbeat, so "No data" on the board can be told apart from "crawler stopped" or "watchdog gone"."""
        lines = ["# TYPE crawler_up gauge"]
        lines += [f'crawler_up{{crawler="{n}"}} {STATE_CODE.get(self.c[n]["status"], 0)}' for n in CRAWLERS]
        lines += ["# TYPE watchdog_heartbeat_seconds gauge", f"watchdog_heartbeat_seconds {time.time():.0f}"]
        try:
            requests.put(f"{PGW}/watchdog", data=("\n".join(lines) + "\n").encode(), timeout=5)
        except requests.RequestException:
            pass

    def deep_platforms(self):
        """Meta platforms worth starting: everything except ones whose last stop was a lost session not since logged in again."""
        status_f = ROOT / "metalogin" / "login_status.json"
        logged = json.loads(status_f.read_text(encoding="utf-8")).get("logged_in", {}) if status_f.exists() else {}
        keep, out = [], {}
        for p in CRAWLERS["deep"]["platforms"]:
            last = None
            f = ROOT / "metalogin" / "deep" / "telemetry" / p / "events.jsonl"
            if f.exists():
                for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
                    if '"PLATFORM_STOPPED"' in line:
                        try:
                            last = json.loads(line)
                        except ValueError:
                            pass
            relog = (logged.get(p) or {}).get("detected_at") or ""
            if last and last.get("reason") not in SESSION_OK and relog < last["t"][:19]:
                out[p] = last.get("reason")
            else:
                keep.append(p)
        return keep, out

    def start(self, name):
        spec, c = CRAWLERS[name], self.c[name]
        args = [str(PY), str(HERE / spec["script"])]
        if name == "deep":
            plats, blocked = self.deep_platforms()
            if blocked and c.get("blocked") != blocked:
                self.pitfall("deep", "needs_login", blocked, "left out until login_meta.py logs in again")
            c["blocked"] = blocked
            if not plats:
                c["not_before"] = time.time() + 1800
                return
            if set(plats) != set(spec["platforms"]):
                args.append(",".join(plats))
        env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1", **{k: str(v) for k, v in spec["env"].items()}}
        out = open(LOGS / f"{name}.log", "a", encoding="utf-8")
        out.write(f"\n===== {now_iso()} watchdog start {' '.join(args[1:])}\n")
        out.flush()
        c["proc"] = subprocess.Popen(args, cwd=str(HERE), env=env, stdout=out, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                     creationflags=subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP)
        out.close()
        c.update(pid=c["proc"].pid, started=time.time(), status="running")
        self.tel.event("crawler_start", platform=name, pid=c["pid"], args=args[2:])
        log("start", name, c["pid"], args[2:])

    def last_events(self, name, since):
        """Events the crawler wrote since its process started (for an adopted process we have no exit code)."""
        lo = datetime.fromtimestamp(since, TW).isoformat(timespec="seconds")
        ev = []
        for rel in CRAWLERS[name]["events"]:
            f = ROOT / rel
            if f.exists():
                with open(f, encoding="utf-8", errors="replace") as fh:
                    fh.seek(max(0, f.stat().st_size - 200_000))
                    for line in fh:
                        try:
                            r = json.loads(line)
                        except ValueError:
                            continue
                        if r.get("t", "") >= lo:
                            ev.append(r.get("event"))
        return ev

    def telemetry_age(self, name):
        ts = [f.stat().st_mtime for rel in CRAWLERS[name]["telemetry"] for f in (ROOT / rel).glob("*.jsonl")]
        return time.time() - max(ts) if ts else None

    def cooldown_until(self, name):
        if CRAWLERS[name]["cooldown"] == "quota":
            nxt = (datetime.now(PT) + timedelta(days=1)).replace(hour=0, minute=5, second=0, microsecond=0)
            return nxt.timestamp()
        return time.time() + 1800

    def check_crawler(self, name):
        c = self.c[name]
        p = None
        if c["proc"] is not None:
            rc = c["proc"].poll()
            if rc is None:
                p = psutil.Process(c["proc"].pid)
            else:
                self.on_exit(name, rc)
        else:
            p = self.find(name)
            if p and c.get("pid") != p.pid:
                c.update(pid=p.pid, started=p.create_time(), status="running")
                self.tel.event("crawler_adopted", platform=name, pid=p.pid)
                log("adopt", name, p.pid)
            elif not p and c["status"] == "running":
                self.on_exit(name, None)
        if p:
            age = self.telemetry_age(name)
            if age is not None and age > HUNG_S and time.time() - c["started"] > HUNG_S:
                if not net_up():
                    return  # NetGuard is waiting for the network; nothing to fix here
                self.pitfall(name, "hung", {"silent_s": round(age), "pid": p.pid}, "killed the process tree, restart")
                self.kill_tree(p)
                c.update(proc=None, status="stopped", not_before=time.time() + 60)
                self.idle_pushgateway(name)
            return
        if time.time() >= c["not_before"]:
            self.start(name)

    def printed_done(self, name):
        """Whether the crawler's last run (the part of its log after the newest watchdog start line) printed its done line."""
        f = LOGS / f"{name}.log"
        if not f.exists():
            return False
        tail = f.read_text(encoding="utf-8", errors="replace")[-400_000:]
        return CRAWLERS[name]["done_mark"] in tail.rsplit("\n===== ", 1)[-1]

    def on_exit(self, name, rc):
        c = self.c[name]
        ev = self.last_events(name, c["started"] or time.time() - 86400)
        crashed = (rc not in (0, None)) or (rc is None and "crash" in ev)
        if rc is None and not crashed and not self.printed_done(name):
            # An adopted process has no exit code. Gone without its done line means it was killed from outside (seen
            # 2026-11-03: YouTube killed mid-run was taken as finished and left waiting for tomorrow's quota reset).
            c.update(proc=None, status="vanished", not_before=time.time() + 300)
            self.idle_pushgateway(name)
            self.pitfall(name, "crawler_vanished", {"events_since_start": ev[-5:]}, "restart in 5 min")
            return
        c.update(proc=None, status="crashed" if crashed else "done")
        self.idle_pushgateway(name)
        if crashed:
            c["fails"] = c.get("fails", 0) + 1
            wait = min(3600, 300 * 2 ** (c["fails"] - 1))
            c["not_before"] = time.time() + wait
            tail = (LOGS / f"{name}.log").read_text(encoding="utf-8", errors="replace")[-600:] if (LOGS / f"{name}.log").exists() else ""
            self.pitfall(name, "crawler_crashed", {"rc": rc, "tail": tail}, f"restart in {wait // 60} min")
        else:
            c["fails"] = 0
            c["not_before"] = self.cooldown_until(name)
            self.tel.event("crawler_done", platform=name, rc=rc,
                           restart_at=datetime.fromtimestamp(c["not_before"], TW).isoformat(timespec="seconds"))
            log("done", name, rc, "next", datetime.fromtimestamp(c["not_before"], TW))

    # ---- 3. crawler events -> pitfalls
    def _event_files(self):
        return [ROOT / rel for spec in CRAWLERS.values() for rel in spec["events"]]

    def scan_events(self):
        for name, spec in CRAWLERS.items():
            for rel in spec["events"]:
                f = ROOT / rel
                if not f.exists():
                    continue
                off = self.offsets.get(str(f), 0)
                size = f.stat().st_size
                if size < off:
                    off = 0  # file replaced
                with open(f, "rb") as fh:
                    fh.seek(off)
                    data = fh.read()
                end = data.rfind(b"\n") + 1  # leave a half-written last line for the next round
                self.offsets[str(f)] = off + end
                for line in data[:end].decode("utf-8", errors="replace").splitlines():
                    try:
                        r = json.loads(line)
                    except ValueError:
                        continue
                    if r.get("event") in TROUBLE:
                        detail = {k: v for k, v in r.items() if k not in ("job", "tb")}
                        try:  # one bad record must not stop the scan: offsets already moved past it
                            self.pitfall(name, r["event"], detail, "crawler slowed itself down / stopped; recorded")
                        except Exception as e:
                            log("pitfall failed", name, r.get("event"), repr(e))

    # ---- main loop
    def loop_once(self):
        stack = self.check_stack()
        for name in CRAWLERS:
            try:
                self.check_crawler(name)
            except Exception as e:
                log("check", name, repr(e), traceback.format_exc()[-400:])
        self.scan_events()
        if stack:
            self.push_status()
        if stack and time.time() - self.last_backfill >= BACKFILL_EVERY_S:
            self.backfill(3)
        self.save()


def single_instance():
    lock = WD / "watchdog.pid"
    WD.mkdir(parents=True, exist_ok=True)
    if lock.exists():
        try:
            old = psutil.Process(int(lock.read_text()))
            if "watchdog.py" in " ".join(old.cmdline()):
                sys.exit(f"watchdog already running (pid {old.pid})")
        except (ValueError, psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    lock.write_text(str(os.getpid()))


if __name__ == "__main__":
    # Seen 2026-11-03: `python watchdog.py --help` from the repo copy ran the full loop with ROOT = <repo>/q6/crawl, where its
    # pid file could not see the deployed instance, and started a second PTT crawler. Run only the deployed copy.
    if set(sys.argv[1:]) - {"--once"} or not ROOT.is_dir():
        sys.exit(f"usage: pythonw watchdog.py [--once]   (needs the crawl workdir {ROOT}; set CRAWL_ROOT or run the deployed copy)")
    single_instance()
    if sys.stdout is None or "pythonw" in sys.executable.lower():  # pythonw: no console, keep a log instead
        sys.stdout = sys.stderr = open(WD / "watchdog.log", "a", encoding="utf-8", buffering=1)
    w = Watchdog()
    w.tel.event("watchdog_start", platform="watchdog", pid=os.getpid(), root=str(ROOT))
    log("watchdog start", os.getpid(), ROOT)
    once = "--once" in sys.argv
    while True:
        try:
            w.loop_once()
        except Exception as e:
            log("loop error", repr(e), traceback.format_exc()[-600:])
        if once:
            break
        time.sleep(LOOP_S)
