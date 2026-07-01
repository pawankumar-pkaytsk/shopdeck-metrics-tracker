#!/usr/bin/env python3
"""Build gm_compliance_data.json for Central Reports -> GM Compliance.

Per GM (HITS team only), collect the dates that let the dashboard count, for any
client-selected date window (Today / Yesterday / Last 3 / Last 7 / custom range):
  - T/S done   : HIT sellers whose LAST troubleshoot date falls in the window (card 10189)
  - Golives    : HIT sellers whose go_live_date falls in the window (card 7682)

HITS team membership: card 11244 team_mapping == 'HIT'.
GM per seller:        card 7753 growth_manager_name (fallback 'Unassigned').

NOTE: card 10189 exposes only each seller's LATEST T/S date, not per-event history,
so the T/S count is "sellers whose most-recent T/S is in the window", not a raw
count of every T/S event. (Event-level counting would need a different source.)

Run: cd ~/shopdeck-metrics-site && python3 pipelines/gm_compliance_refresh.py --push
"""
import json, os, sys, subprocess, urllib.request, datetime, re
from collections import defaultdict

REPO = os.path.expanduser(os.environ.get("REPO_DIR", "~/shopdeck-metrics-site"))
OUT  = os.path.join(REPO, "gm_compliance_data.json")
CRED_CACHE = os.path.expanduser("~/metabase-arr-refresh/.mbcreds")
DESKTOP_CFG = os.path.expanduser("~/Library/Application Support/Claude/claude_desktop_config.json")
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


def main():
    url, email, pw = creds()
    tok = req(url + "/api/session", "POST", {"username": email, "password": pw}, {"Content-Type": "application/json"})["id"]
    H = {"Content-Type": "application/json", "X-Metabase-Session": tok}

    # HIT sellers (card 11244)
    hit = set()
    for r in req(f"{url}/api/card/11244/query/json", "POST", {}, H):
        if str(r.get("team_mapping") or "").strip().upper() == "HIT":
            sid = str(r.get("seller_id") or "").strip()
            if sid:
                hit.add(sid)
    print(f"[gmc] HIT sellers (card 11244): {len(hit)}")

    # seller -> GM (card 7753)
    gm_of = {}
    for r in req(f"{url}/api/card/7753/query/json", "POST", {}, H):
        sid = str(r.get("seller_id") or "").strip()
        if not sid:
            continue
        gm = _norm(r.get("growth_manager_name"))
        gm_of[sid] = gm if gm not in ("", "-") else "Unassigned"

    # seller -> last T/S date (card 10189)
    ts_of = {}
    for r in req(f"{url}/api/card/10189/query/json", "POST", {}, H):
        sid = str(r.get("seller_id") or "").strip()
        d = str(r.get("last_ts_date") or "")[:10]
        if sid and d:
            ts_of[sid] = d

    # seller -> go_live_date (card 7682, dedup preferring a golive)
    gol_of = {}
    for r in req(f"{url}/api/card/7682/query/json", "POST", {}, H):
        sid = str(r.get("seller_id") or "").strip()
        if not sid:
            continue
        g = str(r.get("go_live_date") or "")[:10]
        if g and (sid not in gol_of or not gol_of[sid]):
            gol_of[sid] = g

    # aggregate per GM over HIT sellers
    by_gm = defaultdict(lambda: {"tsDates": [], "golDates": []})
    for sid in hit:
        gm = gm_of.get(sid, "Unassigned")
        rec = by_gm[gm]
        t = ts_of.get(sid)
        if t:
            rec["tsDates"].append(t)
        g = gol_of.get(sid)
        if g:
            rec["golDates"].append(g)

    out = {
        "generatedAt": datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "gms": sorted(by_gm.keys()),
        "byGM": {k: v for k, v in by_gm.items()},
    }
    json.dump(out, open(OUT, "w"), separators=(",", ":"))
    tot_ts = sum(len(v["tsDates"]) for v in by_gm.values())
    tot_gol = sum(len(v["golDates"]) for v in by_gm.values())
    print(f"[out] {OUT} ({os.path.getsize(OUT)} bytes) · {len(by_gm)} GMs · "
          f"{tot_ts} sellers-with-T/S · {tot_gol} sellers-with-golive")

    if "--push" in sys.argv:
        subprocess.run(["git", "-C", REPO, "add", "gm_compliance_data.json"], check=True)
        r = subprocess.run(["git", "-C", REPO, "commit", "-m", "Refresh GM Compliance data"], capture_output=True, text=True)
        print(r.stdout.strip() or r.stderr.strip())
        if r.returncode == 0:
            subprocess.run(["git", "-C", REPO, "push", "origin", "main"], check=True)
            print("[push] deployed")


if __name__ == "__main__":
    main()
