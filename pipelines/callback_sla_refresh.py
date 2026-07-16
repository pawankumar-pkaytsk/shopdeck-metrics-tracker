#!/usr/bin/env python3
"""Build callback_sla_data.json from card 11646 (day-wise slot-wise callback SLA adherence).
Powers the 'Scheduled callback adherence' tile under Leadership -> Input Metrics.

Run: cd ~/shopdeck-metrics-site && python3 pipelines/callback_sla_refresh.py
"""
import json, os, sys, urllib.request, datetime

CARD = 11646
REPO = os.path.expanduser(os.environ.get("REPO_DIR", "~/shopdeck-metrics-site"))
OUT = os.path.join(REPO, "callback_sla_data.json")
CRED_CACHE = os.path.expanduser("~/metabase-arr-refresh/.mbcreds")
DESKTOP_CFG = os.path.expanduser("~/Library/Application Support/Claude/claude_desktop_config.json")


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
            with urllib.request.urlopen(r, timeout=180) as resp:
                return json.loads(resp.read().decode())
        except Exception as e:
            last = e
            _t.sleep(3 * (attempt + 1))
    raise last


def main():
    url, email, pw = creds()
    _mbkey = os.environ.get('METABASE_API_KEY')
    if not _mbkey:
        try:
            _mbkey = json.load(open(os.path.expanduser('~/metabase-arr-refresh/.mbcreds'))).get('METABASE_API_KEY')
        except Exception:
            _mbkey = None
    if _mbkey:
        AUTH = {'x-api-key': _mbkey}
    else:
        tok = req(url + "/api/session", 'POST', {"username": email, "password": pw}, {'Content-Type': 'application/json'})['id']
        AUTH = {'X-Metabase-Session': tok}
    H = {'Content-Type': 'application/json', **AUTH}
    rows = req(f"{url}/api/card/{CARD}/query/json", "POST", {}, H)
    out_rows = []
    for r in rows:
        out_rows.append({
            "date": str(r.get("Date") or ""),
            "slot1": r.get("Slot 1 SLA adherence"),
            "slot2": r.get("Slot 2 SLA adherence"),
            "overall": r.get("Overall SLA adherence"),
        })
    out = {"generatedAt": datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"), "rows": out_rows}
    json.dump(out, open(OUT, "w"), separators=(",", ":"))
    print(f"[callback-sla] card {CARD}: {len(out_rows)} days -> {OUT}")

    # GC-wise callback SLA adherence (card 11230): D0 (same day) / D1 (yesterday) / L30 (last 30d),
    # with D1 slot-wise breakdown. Powers Leadership -> All Reports -> 11. Callback Adherence.
    GC_CARD = 11230
    GC_OUT = os.path.join(REPO, "callback_gc_data.json")
    def _n(v):
        try: return round(float(v), 2)
        except (TypeError, ValueError): return None
    grows = req(f"{url}/api/card/{GC_CARD}/query/json", "POST", {}, H)
    gc_rows = []
    for r in grows:
        gc_rows.append({
            "gc": r.get("gc_name"),
            "d0": {"tasks": r.get("d0_calling_tasks"), "within": r.get("d0_calls_within_sla"), "pct": _n(r.get("d0_sla_pct"))},
            "d1": {"tasks": r.get("d1_calling_tasks"), "within": r.get("d1_calls_within_sla"), "pct": _n(r.get("d1_sla_pct")),
                   "slot1": {"tasks": r.get("d1_slot1_tasks"), "pct": _n(r.get("d1_slot1_sla_pct"))},
                   "slot2": {"tasks": r.get("d1_slot2_tasks"), "pct": _n(r.get("d1_slot2_sla_pct"))}},
            "l30": {"tasks": r.get("l30_calling_tasks"), "within": r.get("l30_calls_within_sla"), "pct": _n(r.get("l30_sla_pct"))},
        })
    gc_rows.sort(key=lambda x: (x["gc"] or ""))
    json.dump({"generatedAt": datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"), "rows": gc_rows},
              open(GC_OUT, "w"), separators=(",", ":"))
    print(f"[callback-gc] card {GC_CARD}: {len(gc_rows)} GCs -> {GC_OUT}")


if __name__ == "__main__":
    main()
