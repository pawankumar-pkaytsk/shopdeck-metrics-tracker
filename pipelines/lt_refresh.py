#!/usr/bin/env python3
"""Build lt_data.json for the 'Learning & Training Department' role view.

New GCs = card 11431 (employee roster) rows with designation 'Growth Consultant'
and DOJ >= 2026-01-01. Per GC: Emp ID, DOJ, age (days), joining ISO week/month,
plus performance metrics:
  current snapshots : live/assigned, spend/live, spend3k, bucket health
                      (from gc_data.json + ts_data + 11011 W-1 PNL)
  month-wise        : golives (golive_data via 7753 sellers), HITS target (11322),
                      HITS achieved (hit1 rows via GC mapping),
                      task adherence + SLA and callback compliance + SLA (task_data)

NOTE: task_data holds only the last ~45 days, so task/callback metrics for older
months read as null -> UI shows 'data awaiting'.

Run: cd ~/shopdeck-metrics-site && python3 pipelines/lt_refresh.py
"""
import json, os, sys, subprocess, urllib.request, datetime, re
from collections import defaultdict

REPO = os.path.expanduser(os.environ.get("REPO_DIR", "~/shopdeck-metrics-site"))
OUT = os.path.join(REPO, "lt_data.json")
CRED_CACHE = os.path.expanduser("~/metabase-arr-refresh/.mbcreds")
DESKTOP_CFG = os.path.expanduser("~/Library/Application Support/Claude/claude_desktop_config.json")
LOCAL_SA_KEY = os.path.expanduser("~/Downloads/metrics-tracker-automation-53ad2cdd4b65.json")
STRIKE_SHEET = "1kbLjeYEJWvacK6imqjHTM-alp6eGM9cb9AbV1paNXUM"
STRIKE_RANGE = "'Strikes'!A2:I"
NEW_SINCE = datetime.date(2026, 1, 1)
_norm = lambda v: re.sub(r"\s+", " ", str(v or "").strip())
_key = lambda v: _norm(v).lower()


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


def _sa():
    if os.environ.get("GOOGLE_SA_KEY"):
        return json.loads(os.environ["GOOGLE_SA_KEY"])
    return json.load(open(LOCAL_SA_KEY)) if os.path.exists(LOCAL_SA_KEY) else None


def _sheet(sheet_id, rng):
    sa = _sa()
    if not sa:
        return []
    from google.oauth2 import service_account
    import google.auth.transport.requests as gtr
    import urllib.parse
    c = service_account.Credentials.from_service_account_info(sa, scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"])
    c.refresh(gtr.Request())
    u = "https://sheets.googleapis.com/v4/spreadsheets/%s/values/%s" % (sheet_id, urllib.parse.quote(rng))
    return json.loads(urllib.request.urlopen(urllib.request.Request(u, headers={"Authorization": "Bearer " + c.token}), timeout=180).read()).get("values", [])


def parse_strike_date(s):
    for fmt in ("%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d", "%d-%b-%y"):
        try:
            return datetime.datetime.strptime(str(s).strip(), fmt).date()
        except (ValueError, TypeError):
            continue
    return None


def parse_doj(s):
    for fmt in ("%d-%b-%y", "%d-%b-%Y", "%Y-%m-%d"):
        try:
            return datetime.datetime.strptime(str(s).strip(), fmt).date()
        except (ValueError, TypeError):
            continue
    return None


def main():
    url, email, pw = creds()
    tok = req(url + "/api/session", "POST", {"username": email, "password": pw}, {"Content-Type": "application/json"})["id"]
    H = {"Content-Type": "application/json", "X-Metabase-Session": tok}
    today = datetime.date.today()

    # ---- 11431: new GCs ----
    new_gcs = []
    for r in req(f"{url}/api/card/11431/query/json", "POST", {}, H):
        if "growth consultant" not in _key(r.get("designation")):
            continue
        doj = parse_doj(r.get("doj"))
        if not doj or doj < NEW_SINCE:
            continue
        iso = doj.isocalendar()
        new_gcs.append({
            "empId": _norm(r.get("emp_id")), "name": _norm(r.get("emp_name")),
            "doj": doj.isoformat(), "ageDays": (today - doj).days,
            "joinWeek": f"{iso[0]}-W{str(iso[1]).zfill(2)}", "joinMonth": doj.strftime("%Y-%m"),
            "status": _norm(r.get("current_status")) or "Active",
        })
    print(f"[lt] card 11431: {len(new_gcs)} new GCs (DOJ >= {NEW_SINCE})")

    # ---- local data ----
    gc_data = load_json("gc_data.json", {}) or {}
    by_gc = gc_data.get("byGC", {})
    gc_key = {_key(g): g for g in by_gc}
    detail = (load_json("gc_detail_data.json", {}) or {}).get("detail", {})
    ts_sellers = (load_json("ts_data.json", {}) or {}).get("sellers", {})
    golive = (load_json("golive_data.json", {}) or {}).get("sellers", {})
    hit1 = (load_json("hit1_data.json", {}) or {}).get("rows", [])
    tasks = (load_json("task_data.json", {}) or {}).get("tasks", [])
    task_window_cutoff = (load_json("task_data.json", {}) or {}).get("cutoff", "")

    # HIT targets per GC per month (11322)
    tgt = defaultdict(dict)  # gc_key -> 'YYYY-MM' -> target
    for r in req(f"{url}/api/card/11322/query/json", "POST", {}, H):
        if str(r.get("Role") or "").strip().upper() != "GC":
            continue
        nm, mth, yr = _key(r.get("Name")), r.get("Target_Month"), r.get("Target_Year")
        if nm and mth is not None and yr is not None and r.get("HITS_Target") is not None:
            tgt[nm][f"{int(yr)}-{str(int(mth)).zfill(2)}"] = num(r.get("HITS_Target"))

    # sellers per GC + hit1 achieved per GC per month (via current 7753 assignment in gc_data)
    hit1_by_sid = defaultdict(list)
    for x in hit1:
        hit1_by_sid[str(x.get("id") or "").strip()].append((int(x.get("hy") or 0), int(x.get("hm") or 0)))

    # tasks grouped by GC name (task rows carry gc from card 10181; normalize)
    t_by_gc = defaultdict(list)
    for t in tasks:
        t_by_gc[_key(t.get("gc"))].append(t)

    # ---- Strikes (Google Sheet) grouped by strike-recipient name + month ----
    strk_by_gc = defaultdict(lambda: defaultdict(list))  # gc_key -> 'YYYY-MM' -> [rows]
    strk_rows = 0
    for r in _sheet(STRIKE_SHEET, STRIKE_RANGE):
        who = _key(r[2]) if len(r) > 2 else ""
        if not who:
            continue
        d = parse_strike_date(r[1]) if len(r) > 1 else None
        mo = d.strftime("%Y-%m") if d else None
        if not mo:  # fall back to Strike Month/Year text columns
            mon_txt, yr_txt = (_norm(r[7]) if len(r) > 7 else ""), (_norm(r[8]) if len(r) > 8 else "")
            try:
                mo = datetime.datetime.strptime(mon_txt[:3], "%b").strftime("%m") if mon_txt else None
                mo = f"{int(yr_txt)}-{mo}" if (mo and yr_txt) else None
            except (ValueError, TypeError):
                mo = None
        if not mo:
            continue
        strk_by_gc[who][mo].append({
            "date": d.isoformat() if d else _norm(r[1]) if len(r) > 1 else "",
            "type": _norm(r[4]) if len(r) > 4 else "",
            "issue": _norm(r[5]) if len(r) > 5 else "",
            "detail": _norm(r[6]) if len(r) > 6 else "",
            "by": _norm(r[3]) if len(r) > 3 else "",
        })
        strk_rows += 1
    print(f"[lt] strikes sheet: {strk_rows} strike rows across {len(strk_by_gc)} people")

    months = sorted({f"2026-{str(m).zfill(2)}" for m in range(1, today.month + 1)}, reverse=True)

    out_gcs = []
    for g in sorted(new_gcs, key=lambda x: x["doj"], reverse=True):
        k = _key(g["name"])
        canon = gc_key.get(k)
        rec = dict(g)
        rec["matched"] = bool(canon)
        cur = None
        by_month = {}
        if canon:
            v = by_gc[canon]
            m = v.get("metrics", {})
            sids = [s["id"] for s in v.get("sellers", [])]
            # bucket health: den = sellers spend>3540 (s7); num = of those, W-1 PNL > -20 (11011 via detail)
            den = 0; bh_num = 0
            for sid in sids:
                s7 = num((ts_sellers.get(sid) or {}).get("s7"))
                if s7 > 3540:
                    den += 1
                    p1 = (((detail.get(sid) or {}).get("pnl") or [{}])[0] or {}).get("pnl")
                    if p1 is not None and p1 > -20:
                        bh_num += 1
            cur = {
                "assigned": m.get("assigned"), "live": m.get("live"), "spending": m.get("spending"),
                "liveAssignedPct": m.get("liveAssignedPct"), "spendLivePct": m.get("spendLivePct"),
                "spend3kLivePct": m.get("spend3kLivePct"),
                "bucketHealthPct": round(bh_num / den * 100, 1) if den else None,
                "bhNum": bh_num, "bhDen": den,
            }
            for mo in months:
                gol = sum(1 for sid in sids if str((golive.get(sid) or {}).get("g") or "")[:7] == mo)
                yr, mth = int(mo[:4]), int(mo[5:7])
                ach = sum(1 for sid in sids for (hy, hm) in hit1_by_sid.get(sid, []) if hy == yr and hm == mth)
                # tasks by created month
                gts = [t for t in t_by_gc.get(k, []) if str(t.get("cr") or "")[:7] == mo]
                reg = [t for t in gts if str(t.get("ty") or "").lower() != "callback"]
                cbs = [t for t in gts if str(t.get("ty") or "").lower() == "callback"]
                def agg(lst):
                    tot = len(lst)
                    done = [t for t in lst if str(t.get("status") or "").lower() in ("completed", "closed")]
                    sla = sum(1 for t in done if t.get("tat") is not None and t.get("sla") is not None and t["tat"] <= t["sla"])
                    return {"tot": tot, "done": len(done), "sla": sla}
                in_window = task_window_cutoff and (mo >= task_window_cutoff[:7])
                by_month[mo] = {
                    "golive": gol,
                    "hitsTarget": tgt.get(k, {}).get(mo),
                    "hitsAch": ach,
                    "task": agg(reg) if in_window else None,     # None => data awaiting (outside 45d window)
                    "callback": agg(cbs) if in_window else None,
                }
        # strikes: keyed on GC name, available even without a seller book
        for mo in months:
            srows = strk_by_gc.get(k, {}).get(mo, [])
            by_month.setdefault(mo, {"golive": 0, "hitsTarget": None, "hitsAch": 0, "task": None, "callback": None})
            by_month[mo]["strikes"] = len(srows)
            by_month[mo]["strikeRows"] = srows
        rec["strikesTotal"] = sum(len(v) for v in strk_by_gc.get(k, {}).values())
        rec["cur"] = cur
        rec["byMonth"] = by_month
        out_gcs.append(rec)

    # ---- Weekly snapshot (snapshot-forward): store this ISO week's per-GC metrics, accumulating ----
    # History builds from when tracking started (stored in GitHub as lt_weekly.json).
    ci = today.isocalendar()
    cur_yw = "%d%02d" % (ci[0], ci[1])
    cur_mo = today.strftime("%Y-%m")
    def _wk_of(dstr):
        try:
            y, w, _ = datetime.date.fromisoformat(str(dstr)[:10]).isocalendar(); return "%d%02d" % (y, w)
        except (ValueError, TypeError):
            return None
    weekly = load_json("lt_weekly.json", {"weeks": {}}) or {"weeks": {}}
    weekly.setdefault("weeks", {})
    snap = {}
    for g in out_gcs:
        if not g.get("matched") or not g.get("cur"):
            continue
        k = _key(g["name"]); c = g["cur"]; bm = g["byMonth"].get(cur_mo, {})
        # this-week task / callback SLA
        gts = [t for t in t_by_gc.get(k, []) if _wk_of(t.get("cr")) == cur_yw]
        reg = [t for t in gts if str(t.get("ty") or "").lower() != "callback"]
        cbs = [t for t in gts if str(t.get("ty") or "").lower() == "callback"]
        def _sla(lst):
            done = [t for t in lst if str(t.get("status") or "").lower() in ("completed", "closed")]
            sla = sum(1 for t in done if t.get("tat") is not None and t.get("sla") is not None and t["tat"] <= t["sla"])
            return {"tot": len(lst), "done": len(done), "sla": sla}
        canon = gc_key.get(k); sids = [s["id"] for s in by_gc.get(canon, {}).get("sellers", [])] if canon else []
        golw = sum(1 for sid in sids if _wk_of((golive.get(sid) or {}).get("g")) == cur_yw)
        snap[g["empId"] or k] = {
            "name": g["name"], "empId": g["empId"], "doj": g["doj"],
            "assigned": c.get("assigned"), "live": c.get("live"), "spending": c.get("spending"),
            "spendLivePct": c.get("spendLivePct"), "liveAssignedPct": c.get("liveAssignedPct"),
            "bucketHealthPct": c.get("bucketHealthPct"),
            "task": _sla(reg), "callback": _sla(cbs), "golives": golw,
            "hitsTarget": bm.get("hitsTarget"), "hitsAch": bm.get("hitsAch", 0),
        }
    weekly["weeks"][cur_yw] = snap
    weekly["generatedAt"] = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    json.dump(weekly, open(os.path.join(REPO, "lt_weekly.json"), "w"), separators=(",", ":"))
    print(f"[lt] weekly snapshot {cur_yw}: {len(snap)} GCs · total weeks stored={len(weekly['weeks'])}")

    out = {
        "generatedAt": datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "newSince": NEW_SINCE.isoformat(),
        "taskWindowCutoff": task_window_cutoff,
        "months": months,
        "gcs": out_gcs,
    }
    json.dump(out, open(OUT, "w"), separators=(",", ":"))
    matched = sum(1 for x in out_gcs if x["matched"])
    print(f"[out] {OUT} ({os.path.getsize(OUT)} bytes) · {len(out_gcs)} new GCs · {matched} matched to seller books · months={months}")

    if "--push" in sys.argv:
        subprocess.run(["git", "-C", REPO, "add", "lt_data.json"], check=True)
        r = subprocess.run(["git", "-C", REPO, "commit", "-m", "Refresh L&T data"], capture_output=True, text=True)
        print(r.stdout.strip() or r.stderr.strip())
        if r.returncode == 0:
            subprocess.run(["git", "-C", REPO, "push", "origin", "main"], check=True)
            print("[push] deployed")


if __name__ == "__main__":
    main()
