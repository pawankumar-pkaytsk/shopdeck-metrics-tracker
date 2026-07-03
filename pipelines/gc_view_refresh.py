#!/usr/bin/env python3
"""Build gc_data.json + gc_detail_data.json for the 'View as Growth Consultant' role view.

GC assignment: card 7753 growth_consultant_name. A GC whose 7753 live book is < 50% of
its Daily-Plan assigned book is flagged "on leave" (accounts temporarily redistributed).

Per-seller detail (click-through): spend (today/yest/lifetime, first/last spend date),
ad-account type + remaining funds (3539), PNL W-1/-2/-3 (bucket_data), ad-block reason +
resolution (11286 + 11036), total T/S, ICP (6302), PQ (10773), paused/experimental, people,
tasks & scheduled calls, cases.

Run: cd ~/shopdeck-metrics-site && python3 ~/metabase-arr-refresh/gc_view_refresh.py --push
"""
import json, os, sys, subprocess, urllib.request, urllib.parse, datetime, re
from collections import defaultdict

REPO = os.path.expanduser(os.environ.get("REPO_DIR", "~/shopdeck-metrics-site"))
OUT  = os.path.join(REPO, "gc_data.json")
DETAIL_OUT = os.path.join(REPO, "gc_detail_data.json")
CRED_CACHE = os.path.expanduser("~/metabase-arr-refresh/.mbcreds")
DESKTOP_CFG = os.path.expanduser("~/Library/Application Support/Claude/claude_desktop_config.json")
LOCAL_SA_KEY = os.path.expanduser("~/Downloads/metrics-tracker-automation-53ad2cdd4b65.json")
ESC_SHEET = "1eIbQU-odVp6lwBnawIIdSbpVHIrywy98Ib4RsZhEPgk"
ESC_RANGE = "'Raw_Suggested'!A2:P"
DP_SHEET = "1QCdVIkKa_4yMb1NZHSkIt50x4qoKaFlw2WXpQZnL6KM"
DP_RANGE = "'Daily Plan'!A2:AK"
DP_ACTIVE = {"assigned", "scheduled seller"}
HEX24 = re.compile(r"^[0-9a-f]{24}$", re.I)
FUNDS_THRESHOLD = 2000
SPEND3K = 3540
_norm = lambda v: re.sub(r"\s+", " ", str(v or "").strip())


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
    c = service_account.Credentials.from_service_account_info(sa, scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"])
    c.refresh(gtr.Request())
    u = "https://sheets.googleapis.com/v4/spreadsheets/%s/values/%s" % (sheet_id, urllib.parse.quote(rng))
    return json.loads(urllib.request.urlopen(urllib.request.Request(u, headers={"Authorization": "Bearer " + c.token}), timeout=180).read()).get("values", [])


def try_card(url, cid, H, on_row, label, fallback=None):
    """Fetch a card; call on_row(r) per row. On failure run fallback()."""
    try:
        for r in req(f"{url}/api/card/{cid}/query/json", "POST", {}, H):
            on_row(r)
        print(f"[gc] card {cid} ok ({label})")
    except Exception as e:
        print(f"[gc] card {cid} FAILED ({label}): {e}")
        if fallback:
            fallback()


def main():
    url, email, pw = creds()
    tok = req(url + "/api/session", "POST", {"username": email, "password": pw}, {"Content-Type": "application/json"})["id"]
    H = {"Content-Type": "application/json", "X-Metabase-Session": tok}

    prev = load_json("gc_data.json", {}) or {}
    prev_detail = (load_json("gc_detail_data.json", {}) or {}).get("detail", {})
    prev_sellers = {}
    for _g, _v in (prev.get("byGC") or {}).items():
        for _s in _v.get("sellers", []):
            prev_sellers[_s["id"]] = _s

    # ---- 7753: GC assignment (growth_consultant_name) + roles ----
    gc_of, roles_of = {}, {}
    for r in req(f"{url}/api/card/7753/query/json", "POST", {}, H):
        sid = str(r.get("seller_id") or "").strip()
        if not sid:
            continue
        roles_of[sid] = {
            "GM": _norm(r.get("growth_manager_name")), "GL": _norm(r.get("growth_lead_name")),
            "KAM": _norm(r.get("key_account_manager_name")), "KAE": _norm(r.get("key_account_executive_name")),
            "AM": _norm(r.get("assistant_manager_name")), "Golive POC": _norm(r.get("golive_poc_name")),
            "Onboarding POC": _norm(r.get("onboarding_poc_name")), "Profitability AM": _norm(r.get("profitability_associate_manager_name")),
        }
        gc = _norm(r.get("growth_consultant_name"))
        if gc not in ("", "-") and "dummy" not in gc.lower():
            gc_of[sid] = gc
    print(f"[gc] 7753: {len(gc_of)} sellers under a GC")

    # ---- Daily Plan assigned count per GC (for the 'on leave' flag only) ----
    dp_set = defaultdict(set)
    for r in _sheet(DP_SHEET, DP_RANGE):
        if len(r) < 8:
            continue
        sid = str(r[4]).strip() if len(r) > 4 else ""
        if not HEX24.match(sid) or _norm(r[6]).lower() not in DP_ACTIVE:
            continue
        g = _norm(r[7])
        if g and "dummy" not in g.lower():
            dp_set[g].add(sid)
    dp_count = {g: len(v) for g, v in dp_set.items()}

    # ---- 10352 info (fallback: previous) ----
    info = {}
    try_card(url, 10352, H, lambda r: info.__setitem__(str(r.get("seller_id") or "").strip(), {"name": _norm(r.get("company")), "website": _norm(r.get("website")), "contact": _norm(r.get("seller_contact"))}) if str(r.get("seller_id") or "").strip() in gc_of else None, "seller info",
             fallback=lambda: [info.__setitem__(sid, {"name": s.get("name", ""), "website": s.get("website", ""), "contact": s.get("contact", "")}) for sid, s in prev_sellers.items()])

    # ---- 10065 first + last spend date ----
    first_spend, last_spend = {}, {}
    def _sp(r):
        sid = str(r.get("seller id") or r.get("seller_id") or "").strip()
        if sid in gc_of:
            if r.get("first spend date"): first_spend[sid] = str(r.get("first spend date"))[:10]
            if r.get("last spend date"): last_spend[sid] = str(r.get("last spend date"))[:10]
    try_card(url, 10065, H, _sp, "spend dates",
             fallback=lambda: [(_ for _ in ()).throw(StopIteration)] if False else [last_spend.__setitem__(sid, dd["pauseDate"]) for sid, dd in prev_detail.items() if dd.get("pauseDate")])

    # ---- 6302 ICP ----
    icp_of = {}
    try_card(url, 6302, H, lambda r: icp_of.__setitem__(str(r.get("seller_id") or "").strip(), round(num(r.get("f0_")), 2)) if str(r.get("seller_id") or "").strip() in gc_of and r.get("f0_") is not None else None, "ICP",
             fallback=lambda: [icp_of.__setitem__(sid, dd.get("icp")) for sid, dd in prev_detail.items() if dd.get("icp") is not None])

    # ---- 8684 experimental ----
    experimental = set()
    try_card(url, 8684, H, lambda r: experimental.add(str(r.get("seller_id") or (list(r.values())[0] if r else "") or "").strip()) if str(r.get("seller_id") or (list(r.values())[0] if r else "") or "").strip() in gc_of else None, "experimental",
             fallback=lambda: experimental.update(sid for sid, s in prev_sellers.items() if s.get("experimental")))

    # ---- 3539 funds: type + remaining balance + funds-low ----
    funds_low, acct_type, remaining = set(), {}, {}
    def _funds(r):
        sid = str(r.get("seller_id") or "").strip()
        if sid not in gc_of:
            return
        bal = num(r.get("balance"))
        remaining[sid] = remaining.get(sid, 0.0) + bal
        ft = _norm(r.get("funding_source_type"))
        if ft and ft.lower() != "none" and sid not in acct_type:
            acct_type[sid] = ft
        if bool(r.get("is_prepay_account")) and bal < FUNDS_THRESHOLD:
            funds_low.add(sid)
    try_card(url, 3539, H, _funds, "funds",
             fallback=lambda: (funds_low.update(sid for sid, s in prev_sellers.items() if s.get("fundsLow")),
                               [(acct_type.__setitem__(sid, dd.get("adAccountType", "")), remaining.__setitem__(sid, dd.get("remainingFunds", 0))) for sid, dd in prev_detail.items() if dd.get("adAccountType")]))

    # ---- 11286 ad blocked: reason + block date ----
    ad_blocked, ab_reason, ab_date = set(), {}, {}
    def _ab(r):
        sid = str(r.get("seller_id") or "").strip()
        if sid not in gc_of:
            return
        ad_blocked.add(sid)
        if sid not in ab_reason:
            ab_reason[sid] = _norm(r.get("detailed_reason_text")) or _norm(r.get("reason"))
            ab_date[sid] = str(r.get("created_at") or "")[:10]
    try_card(url, 11286, H, _ab, "ad-blocked",
             fallback=lambda: ad_blocked.update(sid for sid, s in prev_sellers.items() if s.get("adBlocked")))

    # ---- 11036 ad-block tickets (resolution) ----
    ab_ticket = {}   # sid -> {resolved, created, resolutionDate}
    def _tkt(r):
        if _norm(r.get("sub_type")).lower() != "ad_account_blocked":
            return
        sid = str(r.get("seller_id") or "").strip()
        if sid not in gc_of:
            return
        resolved = _norm(r.get("status")).lower() in ("completed", "closed", "resolved")
        cur = ab_ticket.get(sid)
        # prefer an unresolved ticket; else keep latest
        if cur is None or (not resolved and cur.get("resolved")):
            ab_ticket[sid] = {"resolved": resolved, "created": str(r.get("task_created_at") or "")[:10], "resolutionDate": str(r.get("completion_date") or "")[:10]}
    try_card(url, 11036, H, _tkt, "ad-block tickets")

    # ---- local data ----
    golive = (load_json("golive_data.json", {}) or {}).get("sellers", {})
    ts_sellers = (load_json("ts_data.json", {}) or {}).get("sellers", {})
    scaling = (load_json("scaling_data.json", {}) or {}).get("sellers", {})
    hit1 = (load_json("hit1_data.json", {}) or {}).get("rows", [])
    bucket = (load_json("bucket_data.json", {}) or {}).get("sellers", {})

    # ---- 11011 PNL W-1/-2/-3 (pnl% + spend) + best source ----
    pnl11011 = {}
    def _wk(p, sp):
        return {"pnl": round(num(p), 2) if p is not None else None, "spend": round(num(sp)) if sp is not None else None}
    def _pnl(r):
        sid = str(r.get("seller_id") or "").strip()
        if sid in gc_of:
            pnl11011[sid] = {"w": [_wk(r.get("w1_pnl"), r.get("w1_spend")), _wk(r.get("w2_pnl"), r.get("w2_spend")), _wk(r.get("w3_pnl"), r.get("w3_spend"))], "src": _norm(r.get("best_source"))}
    try_card(url, 11011, H, _pnl, "PNL 11011",
             fallback=lambda: [pnl11011.__setitem__(sid, {"w": dd.get("pnl"), "src": dd.get("pnlSource", "")}) for sid, dd in prev_detail.items() if dd.get("pnl")])

    # ---- 10773 PQ (latest row per seller) ----
    pq_of, _pqdt = {}, {}
    def _pq(r):
        sid = str(r.get("seller_id") or "").strip()
        if sid not in gc_of:
            return
        d = str(r.get("date") or "")[:10]
        if sid not in _pqdt or d > _pqdt[sid]:
            _pqdt[sid] = d
            lv, dv = r.get("lifetime_avg_pq"), r.get("last_15d_avg_pd")
            pq_of[sid] = {"life": round(num(lv), 2) if lv is not None else None, "d15": round(num(dv), 2) if dv is not None else None}
    try_card(url, 10773, H, _pq, "PQ",
             fallback=lambda: [pq_of.__setitem__(sid, {"life": dd.get("pqLifetime"), "d15": dd.get("pq15")}) for sid, dd in prev_detail.items() if dd.get("pqLifetime") is not None or dd.get("pq15") is not None])

    # ---- 2787 spend today/yest/lifetime ----
    spend_of = {}
    def _spend(r):
        sid = str(r.get("seller_id") or "").strip()
        if sid not in gc_of:
            return
        cur = spend_of.setdefault(sid, {"today": 0.0, "yest": 0.0, "life": 0.0})
        cur["today"] += num(r.get("today_spend")); cur["yest"] += num(r.get("yesterday_spend")); cur["life"] += num(r.get("lifetime_spend"))
    try_card(url, 2787, H, _spend, "spend")

    # ---- 10206 calls + calls after pause ----
    calls_total, calls_after = defaultdict(int), defaultdict(int)
    def _calls(r):
        sid = str(r.get("seller_id") or "").strip()
        if sid not in gc_of:
            return
        calls_total[sid] += 1
        cd = str(r.get("call_date") or "")[:10]; ls = last_spend.get(sid)
        if cd and ls and cd > ls:
            calls_after[sid] += 1
    try_card(url, 10206, H, _calls, "calls",
             fallback=lambda: [(calls_total.__setitem__(sid, dd.get("totalCalls", 0)), calls_after.__setitem__(sid, dd.get("callsAfterPaused") or 0)) for sid, dd in prev_detail.items()])

    # ---- task_data ----
    tasks = (load_json("task_data.json", {}) or {}).get("tasks", [])
    t_pending, t_done, c_pending, c_done = defaultdict(list), defaultdict(list), defaultdict(list), defaultdict(list)
    for t in tasks:
        sid = str(t.get("s") or "").strip()
        if sid not in gc_of:
            continue
        rec = {"id": t.get("id"), "ty": t.get("ty"), "st": t.get("st"), "du": t.get("du"), "cp": t.get("cp"), "status": t.get("status")}
        is_cb = str(t.get("ty") or "").lower() == "callback"
        done = str(t.get("status") or "").lower() in ("completed", "closed")
        (c_done if done else c_pending)[sid].append(rec) if is_cb else (t_done if done else t_pending)[sid].append(rec)

    # ---- escalation (SOS/strikes) ----
    esc = defaultdict(list)
    for r in _sheet(ESC_SHEET, ESC_RANGE):
        r = r + [""] * (16 - len(r))
        if _norm(r[0]).upper() != "SOS":
            continue
        esc[_norm(r[12]).lower()].append({"s": _norm(r[1]), "date": _norm(r[2])[:10], "voc": _norm(r[4]), "status": _norm(r[5]).lower(), "gm": _norm(r[13]), "cl": _norm(r[14])})

    # ---- HIT targets + achieved ----
    today = datetime.date.today()
    yday_iso = (today - datetime.timedelta(days=1)).isoformat()
    cur_m, cur_y = today.month, today.year
    hits_target = {}
    try_card(url, 11322, H, lambda r: hits_target.__setitem__(_norm(r.get("Name")).lower(), num(r.get("HITS_Target"))) if str(r.get("Role") or "").strip().upper() == "GC" and int(r.get("Target_Month") or 0) == cur_m and int(r.get("Target_Year") or 0) == cur_y and _norm(r.get("Name")) and r.get("HITS_Target") is not None else None, "hit targets")
    hits_ach = defaultdict(int)
    for x in hit1:
        if int(x.get("hm") or 0) == cur_m and int(x.get("hy") or 0) == cur_y:
            gc = gc_of.get(str(x.get("id") or "").strip())
            if gc:
                hits_ach[gc.lower()] += 1

    def pnl_weeks(sid):
        b = bucket.get(sid) or {}
        p, s = b.get("p", [None, None, None]), b.get("s", [None, None, None])
        return [{"pnl": p[i] if i < len(p) else None, "spend": s[i] if i < len(s) else None} for i in range(3)]

    # ---- assemble ----
    sellers_by_gc = defaultdict(list)
    for sid, gc in gc_of.items():
        sellers_by_gc[gc].append(sid)
    gcs = sorted(set(gc_of.values()))
    by_gc, detail = {}, {}

    for gc in gcs:
        sids = sellers_by_gc[gc]
        sellers = []
        live = spending = spend3k = 0
        pending_ts = []
        for sid in sids:
            gv = golive.get(sid) or {}
            a2h, gol = gv.get("a") or "", gv.get("g") or ""
            is_live = bool(gol); nl = bool(a2h and not gol)
            sc = scaling.get(sid) or {}
            is_spending = num(sc.get("my")) > 1 or num(sc.get("gy")) > 10
            t = ts_sellers.get(sid) or {}
            s7 = num(t.get("s7")); over3k = s7 > SPEND3K
            d = t.get("d") or ""
            ds = None
            if d:
                try: ds = (today - datetime.date.fromisoformat(d[:10])).days
                except ValueError: ds = None
            elig = over3k and (ds is None or ds > 7)
            if is_live: live += 1
            if is_spending: spending += 1
            if over3k: spend3k += 1
            if elig: pending_ts.append(sid)
            ls = last_spend.get(sid, "")
            paused = bool(ls and ls < yday_iso)
            is_exp = sid in experimental
            icp = icp_of.get(sid)
            icp_flag = "no_icp" if icp is None else ("low_icp" if icp < 7 else "high_icp")
            blocked = sid in ad_blocked
            tkt = ab_ticket.get(sid)
            nfo = info.get(sid, {})
            sellers.append({
                "id": sid, "name": nfo.get("name") or _norm(t.get("n")), "contact": nfo.get("contact", ""), "website": nfo.get("website", ""),
                "live": is_live, "notLiveYet": nl, "spending": is_spending,
                "adBlocked": blocked, "fundsLow": sid in funds_low,
                "paused": paused, "experimental": is_exp, "hypercare": False, "icpFlag": icp_flag,
            })
            cases = []
            if nl: cases.append("Golive pending")
            if blocked: cases.append("Ad account blocked")
            if sid in funds_low: cases.append("Funds addition (low balance)")
            if elig: cases.append("Troubleshoot due")
            if paused: cases.append("Account paused")
            ab = None
            if blocked:
                resolved = bool(tkt and tkt.get("resolved"))
                bd = ab_date.get(sid, "")
                pend_days = None
                if not resolved and bd:
                    try: pend_days = (today - datetime.date.fromisoformat(bd)).days
                    except ValueError: pend_days = None
                ab = {"reason": ab_reason.get(sid, ""), "blockDate": bd, "ticketRaised": bool(tkt),
                      "resolved": resolved, "resolutionDate": (tkt or {}).get("resolutionDate", ""), "pendingDays": pend_days}
            detail[sid] = {
                "spend": {"today": round(spend_of.get(sid, {}).get("today", 0)), "yest": round(spend_of.get(sid, {}).get("yest", 0)), "life": round(spend_of.get(sid, {}).get("life", 0))},
                "firstSpendDate": first_spend.get(sid, ""), "lastSpendDate": ls, "pauseDate": ls,
                "adAccountType": acct_type.get(sid, ""), "remainingFunds": round(remaining.get(sid, 0)),
                "pnl": (pnl11011.get(sid) or {}).get("w") or [{"pnl": None, "spend": None}, {"pnl": None, "spend": None}, {"pnl": None, "spend": None}], "pnlSource": (pnl11011.get(sid) or {}).get("src", ""), "totalTS": (t.get("t") if t.get("t") is not None else 0),
                "icp": icp, "icpFlag": icp_flag, "pqLifetime": (pq_of.get(sid) or {}).get("life"), "pq15": (pq_of.get(sid) or {}).get("d15"),
                "people": {k: v for k, v in (people_per(sid, gc, roles_of)).items() if v and v != "-"},
                "lastTsDate": d, "lastTsActions": _norm(t.get("a")),
                "totalCalls": calls_total.get(sid, 0), "callsAfterPaused": calls_after.get(sid, 0),
                "tasksPending": t_pending.get(sid, []), "tasksDone": t_done.get(sid, []),
                "callsPending": c_pending.get(sid, []), "callsDone": c_done.get(sid, []),
                "adBlocked": blocked, "adBlock": ab, "fundsLow": sid in funds_low,
                "live": is_live, "notLiveYet": nl, "spending": is_spending,
                "paused": paused, "experimental": is_exp, "hypercare": False, "cases": cases,
            }
        assigned = len(sids)
        dpc = dp_count.get(gc, 0)
        on_leave = dpc > 0 and assigned < 0.5 * dpc
        eg = esc.get(gc.lower(), [])
        by_gc[gc] = {
            "sellers": sellers,
            "metrics": {
                "assigned": assigned, "live": live, "spending": spending, "spend3k": spend3k,
                "spendLivePct": round(spending / live * 100, 1) if live else None,
                "liveAssignedPct": round(live / assigned * 100, 1) if assigned else None,
                "spend3kLivePct": round(spend3k / live * 100, 1) if live else None,
                "hitsTarget": hits_target.get(gc.lower()), "hitsAchieved": hits_ach.get(gc.lower(), 0),
                "onLeave": on_leave, "dpAssigned": dpc,
            },
            "golive": [s["id"] for s in sellers if s["notLiveYet"]],
            "funds": [s["id"] for s in sellers if s["fundsLow"]],
            "adBlocked": [s["id"] for s in sellers if s["adBlocked"]],
            "hypercare": [s["id"] for s in sellers if s.get("hypercare")],
            "pendingTS": pending_ts,
            "pendingTasks": [x for sid in sids for x in t_pending.get(sid, [])],
            "callbacks": [x for sid in sids for x in c_pending.get(sid, [])],
            "sos": [e for e in eg if e["status"] == "pending"],
            "strikes": eg,
        }

    ts_gen = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    json.dump({"generatedAt": ts_gen, "asOfMonth": today.strftime("%Y-%m"), "gcs": gcs, "byGC": by_gc}, open(OUT, "w"), separators=(",", ":"))
    json.dump({"generatedAt": ts_gen, "detail": detail}, open(DETAIL_OUT, "w"), separators=(",", ":"))
    print(f"[out] {OUT} ({os.path.getsize(OUT)} bytes) · {len(gcs)} GCs")
    print(f"[out] {DETAIL_OUT} ({os.path.getsize(DETAIL_OUT)} bytes) · {len(detail)} details")
    print(f"[gc] on-leave GCs: {sum(1 for g in by_gc.values() if g['metrics']['onLeave'])}")

    if "--push" in sys.argv:
        subprocess.run(["git", "-C", REPO, "add", "gc_data.json", "gc_detail_data.json"], check=True)
        r = subprocess.run(["git", "-C", REPO, "commit", "-m", "Refresh GC view data"], capture_output=True, text=True)
        print(r.stdout.strip() or r.stderr.strip())
        if r.returncode == 0:
            subprocess.run(["git", "-C", REPO, "push", "origin", "main"], check=True)
            print("[push] deployed")


def people_per(sid, gc, roles_of):
    rr = roles_of.get(sid, {})
    return {"GC": gc, "GM": rr.get("GM", ""), "GL": rr.get("GL", ""), "KAM": rr.get("KAM", ""),
            "KAE": rr.get("KAE", ""), "AM": rr.get("AM", ""), "Golive POC": rr.get("Golive POC", ""),
            "Onboarding POC": rr.get("Onboarding POC", ""), "Profitability AM": rr.get("Profitability AM", "")}


if __name__ == "__main__":
    main()
