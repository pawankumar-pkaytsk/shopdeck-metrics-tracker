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


def main():
    url, email, pw = creds()
    key = os.environ.get('METABASE_API_KEY')
    if not key:
        try:
            key = json.load(open(CRED_CACHE)).get('METABASE_API_KEY')
        except Exception:
            key = None

    _tok = [None]

    def fetch(cid):
        """api-key first; on failure (routinely a 400 from a spent BigQuery quota) fall back to
        a session token, which returns Metabase's cached result instead of forcing a fresh scan."""
        if key:
            try:
                r = urllib.request.Request(f"{url}/api/card/{cid}/query/json", data=b"{}",
                                           headers={"x-api-key": key, "Content-Type": "application/json"},
                                           method="POST")
                return json.loads(urllib.request.urlopen(r, timeout=180).read())
            except Exception as e:
                print(f"[callback-sla] card {cid} api-key path failed ({str(e)[:60]}) — trying session cache")
        if _tok[0] is None:
            r = urllib.request.Request(url + "/api/session",
                                       data=json.dumps({"username": email, "password": pw}).encode(),
                                       headers={"Content-Type": "application/json"}, method="POST")
            _tok[0] = json.loads(urllib.request.urlopen(r, timeout=180).read())["id"]
        r = urllib.request.Request(f"{url}/api/card/{cid}/query/json", data=b"{}",
                                   headers={"X-Metabase-Session": _tok[0], "Content-Type": "application/json"},
                                   method="POST")
        return json.loads(urllib.request.urlopen(r, timeout=180).read())

    rows = fetch(CARD)
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
    grows = fetch(GC_CARD)
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
