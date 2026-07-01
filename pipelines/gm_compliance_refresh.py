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
    print(f"[gmc] GM universe (Validation col J, excl Floater/Good Seller): {len(gm_list)}")

    # seller -> GM (card 7753)
    gm_of = {}
    for r in req(f"{url}/api/card/7753/query/json", "POST", {}, H):
        sid = str(r.get("seller_id") or "").strip()
        if sid:
            gm_of[sid] = _key(r.get("growth_manager_name"))

    # seller -> auto/manual tag (card 9963)
    tag_of = {}
    for r in req(f"{url}/api/card/9963/query/json", "POST", {}, H):
        sid = str(r.get("seller_id") or "").strip()
        if sid:
            tag_of[sid] = "auto" if str(r.get("tag") or "").strip().lower() == "auto" else "manual"

    # by GM buckets (only for listed GMs)
    by_gm = {g: {"tsAuto": [], "tsManual": [], "golDates": []} for g in gm_list}

    # T/S events (card 2580) -> classify + map to GM
    ts_rows = req(f"{url}/api/card/2580/query/json", "POST", {}, H)
    ts_kept = 0
    for r in ts_rows:
        sid = str(r.get("seller_id") or "").strip()
        d = str(r.get("submitted_at") or "")[:10]
        if not sid or not d:
            continue
        gk = gm_of.get(sid)
        if gk not in gm_by_key:
            continue
        bucket = by_gm[gm_by_key[gk]]
        (bucket["tsAuto"] if tag_of.get(sid) == "auto" else bucket["tsManual"]).append(d)
        ts_kept += 1

    # Golives (card 7682) -> one per seller -> map to GM
    gol_seen = {}
    for r in req(f"{url}/api/card/7682/query/json", "POST", {}, H):
        sid = str(r.get("seller_id") or "").strip()
        g = str(r.get("go_live_date") or "")[:10]
        if sid and g and (sid not in gol_seen or not gol_seen[sid]):
            gol_seen[sid] = g
    gol_kept = 0
    for sid, g in gol_seen.items():
        gk = gm_of.get(sid)
        if gk in gm_by_key:
            by_gm[gm_by_key[gk]]["golDates"].append(g)
            gol_kept += 1

    out = {
        "generatedAt": datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "gms": gm_list,
        "byGM": by_gm,
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
