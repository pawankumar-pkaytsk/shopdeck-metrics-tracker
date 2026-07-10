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
    tok = req(url + "/api/session", "POST", {"username": email, "password": pw}, {"Content-Type": "application/json"})["id"]
    H = {"Content-Type": "application/json", "X-Metabase-Session": tok}
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


if __name__ == "__main__":
    main()
