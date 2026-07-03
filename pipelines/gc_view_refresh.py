#!/usr/bin/env python3
"""Build gc_data.json for the 'View as Growth Consultant' role view.

Per GC (card 7753 growth_consultant_name, col 10), assemble the sellers assigned to
that GC plus per-GC task buckets and headline metrics, from:
  - 7753  seller -> GC / GM / GL           (assignment)
  - golive_data.json  a2h/golive per seller (live / not-live-yet)
  - ts_data.json      s7 spend, last T/S    (spend>3k, pending T/S eligibility, names)
  - scaling_data.json meta/google yest spend (spending, spend/live)
  - 3539  FB account balance               (funds-addition cases: prepaid & balance<2000)
  - 11286 disabled ad accounts             (ad-account-blocked cases)
  - task_data.json    GC-bucket tasks       (pending tasks / callbacks / SOS)
  - 11322 GC HIT targets (current month)    (hits target)
  - hit1_data.json    HIT1 records          (hits achieved, mapped to GC via 7753)

GAPS (need a source from the user): seller contact, website/store link, a complete
seller-name map, and "strikes". Those render as pending until wired.

Run: cd ~/shopdeck-metrics-site && python3 ~/metabase-arr-refresh/gc_view_refresh.py --push
"""
import json, os, sys, subprocess, urllib.request, datetime, re
from collections import defaultdict

REPO = os.path.expanduser(os.environ.get("REPO_DIR", "~/shopdeck-metrics-site"))
OUT  = os.path.join(REPO, "gc_data.json")
CRED_CACHE = os.path.expanduser("~/metabase-arr-refresh/.mbcreds")
DESKTOP_CFG = os.path.expanduser("~/Library/Application Support/Claude/claude_desktop_config.json")
_norm = lambda v: re.sub(r"\s+", " ", str(v or "").strip())
FUNDS_THRESHOLD = 2000
SPEND3K = 3540


def creds():
    if os.environ.get("METABASE_URL"):
        return os.environ["METABASE_URL"].rstrip("/"), os.environ["METABASE_USER_EMAIL"], os.environ["METABASE_PASSWORD"]
    e = json.load(open(CRED_CACHE)) if os.path.exists(CRED_CACHE) else json.load(open(DESKTOP_CFG))["mcpServers"]["metabase"]["env"]
    return e["METABASE_URL"].rstrip("/"), e["METABASE_USER_EMAIL"], e["METABASE_PASSWORD"]


def req(url, method="GET", body=None, H=None):
    import time as _t
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, method=method, headers=H or {})
    last = None
    for attempt in range(4):
        try:
            with urllib.request.urlopen(r, timeout=600) as resp:
                return json.loads(resp.read().decode())
        except Exception as e:
            last = e
            _t.sleep(3 * (attempt + 1))
    raise last


def load_json(name, dflt):
    p = os.path.join(REPO, name)
    return json.load(open(p)) if os.path.exists(p) else dflt


def num(v):
    try: return float(v)
    except (TypeError, ValueError): return 0.0


def main():
    url, email, pw = creds()
    tok = req(url + "/api/session", "POST", {"username": email, "password": pw}, {"Content-Type": "application/json"})["id"]
    H = {"Content-Type": "application/json", "X-Metabase-Session": tok}

    # ---- 7753: seller -> GC (+ gm, gl) ----
    gc_of, gm_of = {}, {}
    for r in req(f"{url}/api/card/7753/query/json", "POST", {}, H):
        sid = str(r.get("seller_id") or "").strip()
        if not sid:
            continue
        gc = _norm(r.get("growth_consultant_name"))
        if gc in ("", "-") or "dummy" in gc.lower():
            continue
        gc_of[sid] = gc
        gm_of[sid] = _norm(r.get("growth_manager_name"))
    print(f"[gc] 7753: {len(gc_of)} sellers under a GC")

    # ---- local data files ----
    golive = (load_json("golive_data.json", {}) or {}).get("sellers", {})
    ts = load_json("ts_data.json", {}) or {}
    ts_sellers = ts.get("sellers", {})
    scaling = (load_json("scaling_data.json", {}) or {}).get("sellers", {})
    hit1 = (load_json("hit1_data.json", {}) or {}).get("rows", [])

    # names from ts_data (partial; the only broad-ish source we have today)
    name_of = {sid: _norm(v.get("n")) for sid, v in ts_sellers.items() if _norm(v.get("n"))}

    # ---- 3539: funds-addition (prepaid & balance < 2000) ----
    funds_low = set()
    for r in req(f"{url}/api/card/3539/query/json", "POST", {}, H):
        sid = str(r.get("seller_id") or "").strip()
        if not sid or sid not in gc_of:
            continue
        if bool(r.get("is_prepay_account")) and num(r.get("balance")) < FUNDS_THRESHOLD:
            funds_low.add(sid)
    print(f"[gc] 3539: {len(funds_low)} funds-addition cases under GCs")

    # ---- 11286: disabled ad accounts ----
    ad_blocked = set()
    for r in req(f"{url}/api/card/11286/query/json", "POST", {}, H):
        sid = str(r.get("seller_id") or "").strip()
        if sid in gc_of:
            ad_blocked.add(sid)
    print(f"[gc] 11286: {len(ad_blocked)} ad-blocked cases under GCs")

    # ---- 11322: GC HIT targets (current month) ----
    today = datetime.date.today()
    cur_m, cur_y = today.month, today.year
    hits_target = {}
    for r in req(f"{url}/api/card/11322/query/json", "POST", {}, H):
        if str(r.get("Role") or "").strip().upper() != "GC":
            continue
        if int(r.get("Target_Month") or 0) == cur_m and int(r.get("Target_Year") or 0) == cur_y:
            nm = _norm(r.get("Name"))
            if nm and r.get("HITS_Target") is not None:
                hits_target[nm.lower()] = num(r.get("HITS_Target"))

    # ---- hits achieved (hit1 rows this month, mapped to GC via 7753) ----
    hits_ach = defaultdict(int)
    for x in hit1:
        if int(x.get("hm") or 0) == cur_m and int(x.get("hy") or 0) == cur_y:
            gc = gc_of.get(str(x.get("id") or "").strip())
            if gc:
                hits_ach[gc.lower()] += 1

    # ---- task_data: pending tasks / callbacks / SOS per GC ----
    tasks = (load_json("task_data.json", {}) or {}).get("tasks", [])
    SOS_TYPES = {"leadership_support_escalation", "internal_seller_escalation"}
    tk_pending = defaultdict(list)   # exclude callbacks
    tk_callback = defaultdict(list)
    tk_sos = defaultdict(list)
    for t in tasks:
        if str(t.get("status") or "").lower() != "pending":
            continue
        gc = _norm(t.get("gc"))
        if gc.lower() not in {g.lower() for g in set(gc_of.values())}:
            pass  # keep by name even if not in 7753 map
        ty = str(t.get("ty") or "").lower()
        rec = {"id": t.get("id"), "s": t.get("s"), "ty": t.get("ty"), "st": t.get("st"), "du": t.get("du")}
        key = gc.lower()
        if ty == "callback":
            tk_callback[key].append(rec)
        else:
            tk_pending[key].append(rec)
        if ty in SOS_TYPES:
            tk_sos[key].append(rec)

    # ---- assemble per GC ----
    by_gc = {}
    gcs = sorted(set(gc_of.values()))
    # group sellers by GC
    sellers_by_gc = defaultdict(list)
    for sid, gc in gc_of.items():
        sellers_by_gc[gc].append(sid)

    for gc in gcs:
        sids = sellers_by_gc[gc]
        sellers = []
        live = spending = spend3k = notlive = 0
        pending_ts = []
        for sid in sids:
            gv = golive.get(sid) or {}
            a2h, gol = gv.get("a") or "", gv.get("g") or ""
            is_live = bool(gol)
            nl = bool(a2h and not gol)
            sc = scaling.get(sid) or {}
            my, gy = num(sc.get("my")), num(sc.get("gy"))
            is_spending = (my > 1) or (gy > 10)
            t = ts_sellers.get(sid) or {}
            s7 = num(t.get("s7"))
            over3k = s7 > SPEND3K
            # pending T/S eligibility: spend>3540 AND (no last-TS or >7d)
            d = t.get("d") or ""
            ds = None
            if d:
                try:
                    ds = (today - datetime.date.fromisoformat(d[:10])).days
                except ValueError:
                    ds = None
            elig = over3k and (ds is None or ds > 7)
            if is_live: live += 1
            if is_spending: spending += 1
            if over3k: spend3k += 1
            if nl: notlive += 1
            if elig: pending_ts.append(sid)
            sellers.append({
                "id": sid, "name": name_of.get(sid, ""),
                "contact": "", "website": "",              # PENDING source
                "live": is_live, "notLiveYet": nl, "spending": is_spending,
                "adBlocked": sid in ad_blocked, "fundsLow": sid in funds_low,
            })
        assigned = len(sids)
        metrics = {
            "assigned": assigned, "live": live, "spending": spending, "spend3k": spend3k,
            "spendLivePct": round(spending / live * 100, 1) if live else None,
            "liveAssignedPct": round(live / assigned * 100, 1) if assigned else None,
            "spend3kLivePct": round(spend3k / live * 100, 1) if live else None,
            "hitsTarget": hits_target.get(gc.lower()),
            "hitsAchieved": hits_ach.get(gc.lower(), 0),
        }
        by_gc[gc] = {
            "sellers": sellers,
            "metrics": metrics,
            "golive": [s["id"] for s in sellers if s["notLiveYet"]],
            "funds": [s["id"] for s in sellers if s["fundsLow"]],
            "adBlocked": [s["id"] for s in sellers if s["adBlocked"]],
            "pendingTS": pending_ts,
            "pendingTasks": tk_pending.get(gc.lower(), []),
            "callbacks": tk_callback.get(gc.lower(), []),
            "sos": tk_sos.get(gc.lower(), []),
            "strikes": [],   # PENDING logic
        }

    out = {
        "generatedAt": datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "asOfMonth": today.strftime("%Y-%m"),
        "gcs": gcs,
        "byGC": by_gc,
    }
    json.dump(out, open(OUT, "w"), separators=(",", ":"))
    print(f"[out] {OUT} ({os.path.getsize(OUT)} bytes) · {len(gcs)} GCs")

    if "--push" in sys.argv:
        subprocess.run(["git", "-C", REPO, "add", "gc_data.json"], check=True)
        r = subprocess.run(["git", "-C", REPO, "commit", "-m", "Refresh GC view data"], capture_output=True, text=True)
        print(r.stdout.strip() or r.stderr.strip())
        if r.returncode == 0:
            subprocess.run(["git", "-C", REPO, "push", "origin", "main"], check=True)
            print("[push] deployed")


if __name__ == "__main__":
    main()
