#!/usr/bin/env python3
"""Build gm_compliance_data.json for Central Reports -> GM Compliance.

Per GM, collect dates so the dashboard can count for any window (Today / Yesterday /
Last 3 / Last 7 / custom):
  - T/S done : troubleshoot EVENTS from card 2580 (submitted_at), split Auto vs Manual
               by the seller's auto-TS tag (card 9963: tag = auto|manual; default manual)
  - Golives  : card 7682 go_live_date (one per seller)
Both are mapped to a GM via card 7753 (growth_manager_name).

GM universe: the 'Validation' tab (col J) of sheet 1T-HXqHxDV2ZCWURxvjpyiYRIvaBDKYw9AolRNiJNsfs,
excluding the labels GC / GM / Floater / Good Seller / Grand Total (and pivot junk).
Every listed GM is emitted even with zero activity (so the report shows 0 / 0).

Run: cd ~/shopdeck-metrics-site && python3 pipelines/gm_compliance_refresh.py --push
"""
import json, os, sys, subprocess, urllib.request, urllib.parse, datetime, re
from collections import defaultdict

REPO = os.path.expanduser(os.environ.get("REPO_DIR", "~/shopdeck-metrics-site"))
OUT  = os.path.join(REPO, "gm_compliance_data.json")
CRED_CACHE = os.path.expanduser("~/metabase-arr-refresh/.mbcreds")
DESKTOP_CFG = os.path.expanduser("~/Library/Application Support/Claude/claude_desktop_config.json")
LOCAL_SA_KEY = os.path.expanduser("~/Downloads/metrics-tracker-automation-53ad2cdd4b65.json")

VALIDATION_SHEET = "1T-HXqHxDV2ZCWURxvjpyiYRIvaBDKYw9AolRNiJNsfs"
VALIDATION_RANGE = "'Validation'!J1:J200"
GM_EXCLUDE = {"gc", "gm", "floater", "good seller", "grand total", "role",
              "sum of total assigned", "counta of gc"}
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


def sa_info():
    if os.environ.get("GOOGLE_SA_KEY"):
        return json.loads(os.environ["GOOGLE_SA_KEY"])
    if os.path.exists(LOCAL_SA_KEY):
        return json.load(open(LOCAL_SA_KEY))
    raise RuntimeError("no GOOGLE_SA_KEY / local SA key for the Validation sheet")


def fetch_gm_list():
    from google.oauth2 import service_account
    import google.auth.transport.requests as gtr
    c = service_account.Credentials.from_service_account_info(
        sa_info(), scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"])
    c.refresh(gtr.Request())
    u = "https://sheets.googleapis.com/v4/spreadsheets/%s/values/%s" % (VALIDATION_SHEET, urllib.parse.quote(VALIDATION_RANGE))
    vals = json.loads(urllib.request.urlopen(
        urllib.request.Request(u, headers={"Authorization": "Bearer " + c.token}), timeout=120).read()).get("values", [])
    gms = []
    for row in vals:
        if not row:
            continue
        name = _norm(row[0])
        if not name or _key(name) in GM_EXCLUDE:
            continue
        if re.fullmatch(r"[\d.,]+", name):        # pivot count cells
            continue
        if name.lower().startswith(("sum ", "counta", "*note", "note")):
            continue
        gms.append(name)
    # de-dupe preserving order
    seen, out = set(), []
    for g in gms:
        if _key(g) not in seen:
            seen.add(_key(g)); out.append(g)
    return out


def main():
    url, email, pw = creds()
    tok = req(url + "/api/session", "POST", {"username": email, "password": pw}, {"Content-Type": "application/json"})["id"]
    H = {"Content-Type": "application/json", "X-Metabase-Session": tok}

    gm_list = fetch_gm_list()
    gm_by_key = {_key(g): g for g in gm_list}          # display-name lookup
    val_tokens = [(g, _key(g).split()) for g in gm_list]
    print(f"[gmc] GM universe (Validation col J, excl Floater/Good Seller): {len(gm_list)}")

    # Resolve a raw card-7753 GM name to a Validation display name.
    # Exact normalized match, else token-wise prefix match (handles "Aakash A" -> "Aakash Aakash").
    # Ambiguous or no match -> None (seller counts as Unmapped).
    _rcache = {}
    def resolve_gm(raw):
        k = _key(raw)
        if not k or k == "-":
            return None
        if k in _rcache:
            return _rcache[k]
        res = gm_by_key.get(k)
        if not res:
            nt = k.split()
            cands = [g for g, vt in val_tokens
                     if len(vt) == len(nt) and all(vt[i].startswith(nt[i]) or nt[i].startswith(vt[i]) for i in range(len(nt)))]
            res = cands[0] if len(cands) == 1 else None
        _rcache[k] = res
        return res

    # seller -> canonical GM display name (or None)
    gm_of = {}
    for r in req(f"{url}/api/card/7753/query/json", "POST", {}, H):
        sid = str(r.get("seller_id") or "").strip()
        if sid:
            gm_of[sid] = resolve_gm(r.get("growth_manager_name"))

    # seller -> auto/manual tag (card 9963)
    tag_of = {}
    for r in req(f"{url}/api/card/9963/query/json", "POST", {}, H):
        sid = str(r.get("seller_id") or "").strip()
        if sid:
            tag_of[sid] = "auto" if str(r.get("tag") or "").strip().lower() == "auto" else "manual"

    # by GM buckets (only for listed GMs) + an 'unmapped' bucket for Floater/Good Seller/others
    # each bucket stores event objects so the dashboard can drill down: ts={s,n,d,t}, gol={s,n,d}
    by_gm = {g: {"ts": [], "gol": []} for g in gm_list}
    unmapped = {"ts": [], "gol": []}
    name_of = {}

    # T/S events (card 2580) -> classify + map to GM
    ts_rows = req(f"{url}/api/card/2580/query/json", "POST", {}, H)
    ts_kept = 0
    for r in ts_rows:
        sid = str(r.get("seller_id") or "").strip()
        d = str(r.get("submitted_at") or "")[:10]
        if not sid or not d:
            continue
        nm = _norm(r.get("company"))
        if nm:
            name_of[sid] = nm
        canon = gm_of.get(sid)
        bucket = by_gm[canon] if canon in by_gm else unmapped
        bucket["ts"].append({"s": sid, "n": nm, "d": d, "t": "A" if tag_of.get(sid) == "auto" else "M"})
        if canon in by_gm:
            ts_kept += 1

    # Golives -> one date per seller. Prefer local golive_data.json (already built from card
    # 7682 by golive_refresh.py, which runs earlier in the workflow) to avoid re-scanning
    # BigQuery; fall back to card 7682 directly if the file is absent.
    gol_seen = {}
    gol_path = os.path.join(REPO, "golive_data.json")
    if os.path.exists(gol_path):
        for sid, s in (json.load(open(gol_path)).get("sellers", {})).items():
            g = str((s or {}).get("g") or "")[:10]
            if g:
                gol_seen[str(sid).strip()] = g
        print(f"[gmc] golives from local golive_data.json: {len(gol_seen)} sellers")
    else:
        for r in req(f"{url}/api/card/7682/query/json", "POST", {}, H):
            sid = str(r.get("seller_id") or "").strip()
            g = str(r.get("go_live_date") or "")[:10]
            if sid and g and (sid not in gol_seen or not gol_seen[sid]):
                gol_seen[sid] = g
    gol_kept = 0
    for sid, g in gol_seen.items():
        canon = gm_of.get(sid)
        ev = {"s": sid, "n": name_of.get(sid, ""), "d": g}
        if canon in by_gm:
            by_gm[canon]["gol"].append(ev)
            gol_kept += 1
        else:
            unmapped["gol"].append(ev)

    # ---- Troubleshoot eligibility per GM per ISO week (card 10773: weekly seller spend > 3540) ----
    # Denominator for TS Compliance % = sellers under the GM whose summed spend in that ISO week > 3540.
    elig = {g: {} for g in gm_list}
    elig_weeks = set()
    try:
        wkspend = {}
        for r in req(f"{url}/api/card/10773/query/json", "POST", {}, H):
            sid = str(r.get("seller_id") or "").strip()
            d = str(r.get("date") or "")[:10]
            if not sid or not d:
                continue
            iso = datetime.date.fromisoformat(d).isocalendar()
            yw = iso[0] * 100 + iso[1]
            wkspend[(yw, sid)] = wkspend.get((yw, sid), 0.0) + float(r.get("spend") or 0)
        for (yw, sid), v in wkspend.items():
            if v > 3540:
                g = gm_of.get(sid)
                if g in elig:
                    elig[g][str(yw)] = elig[g].get(str(yw), 0) + 1
                    elig_weeks.add(yw)
        print(f"[gmc] 10773 eligibility: weeks={sorted(elig_weeks)} · mapped eligible seller-weeks={sum(sum(w.values()) for w in elig.values())}")
    except Exception as _e:
        try:
            prev_g = json.load(open(OUT))
        except Exception:
            prev_g = {}
        elig = prev_g.get("tsElig", elig)
        elig_weeks = {int(w) for v in elig.values() for w in v}
        print(f"[gmc] 10773 failed -> reused previous tsElig: {_e}")

    # ---- Troubleshoot Task Compliance (task_data.json: ty=troubleshoot_action per GM per created-week) ----
    # total tasks created, done (completed/closed), pending, done-within-SLA (tat <= sla).
    task_comp = {g: {} for g in gm_list}
    tk_path = os.path.join(REPO, "task_data.json")
    if os.path.exists(tk_path):
        n_mapped = 0
        for t in (json.load(open(tk_path)).get("tasks", [])):
            if str(t.get("ty") or "") != "troubleshoot_action":
                continue
            sid = str(t.get("s") or "").strip()
            g = gm_of.get(sid)
            yw = str(t.get("yw") or "")
            if g not in task_comp or not yw:
                continue
            rec = task_comp[g].setdefault(yw, {"t": 0, "d": 0, "p": 0, "s": 0})
            rec["t"] += 1
            st = str(t.get("status") or "").lower()
            if st in ("completed", "closed"):
                rec["d"] += 1
                if t.get("tat") is not None and t.get("sla") is not None and t["tat"] <= t["sla"]:
                    rec["s"] += 1
            elif st == "pending":
                rec["p"] += 1
            n_mapped += 1
        print(f"[gmc] task compliance: {n_mapped} troubleshoot tasks mapped to Validation GMs")
    else:
        print("[gmc] task_data.json missing -> taskComp empty")

    out = {
        "generatedAt": datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "gms": gm_list,
        "byGM": by_gm,
        "unmapped": unmapped,
        "tsElig": elig,
        "eligWeeks": sorted((str(w) for w in elig_weeks), reverse=True),
        "taskComp": task_comp,
    }
    json.dump(out, open(OUT, "w"), separators=(",", ":"))
    print(f"[out] {OUT} ({os.path.getsize(OUT)} bytes) · {len(gm_list)} GMs · "
          f"{ts_kept} T/S events mapped · {gol_kept} golives mapped")

    if "--push" in sys.argv:
        subprocess.run(["git", "-C", REPO, "add", "gm_compliance_data.json"], check=True)
        r = subprocess.run(["git", "-C", REPO, "commit", "-m", "Refresh GM Compliance data"], capture_output=True, text=True)
        print(r.stdout.strip() or r.stderr.strip())
        if r.returncode == 0:
            subprocess.run(["git", "-C", REPO, "push", "origin", "main"], check=True)
            print("[push] deployed")


if __name__ == "__main__":
    main()
