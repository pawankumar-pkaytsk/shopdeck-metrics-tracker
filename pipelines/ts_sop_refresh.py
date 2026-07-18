#!/usr/bin/env python3
"""Build ts_sop_data.json — Troubleshoot SOP Call Compliance (card 10181, sub_type=troubleshoot_sop,
Core team GC). Task-level rows (GC, created date, within-SLA flag) so the UI can filter by custom
date range and aggregate week-wise per GC.

within SLA = task completed within its SLA (tat minutes <= sla_in_min).
Run: cd ~/shopdeck-metrics-site && python3 pipelines/ts_sop_refresh.py
"""
import json, os, urllib.request, urllib.parse, datetime

CARD = 10181
REPO = os.path.expanduser(os.environ.get("REPO_DIR", "~/shopdeck-metrics-site"))
OUT = os.path.join(REPO, "ts_sop_data.json")
CRED_CACHE = os.path.expanduser("~/metabase-arr-refresh/.mbcreds")
DESKTOP_CFG = os.path.expanduser("~/Library/Application Support/Claude/claude_desktop_config.json")


def creds():
    if os.environ.get("METABASE_URL"):
        return os.environ["METABASE_URL"].rstrip("/"), os.environ.get("METABASE_USER_EMAIL"), os.environ.get("METABASE_PASSWORD")
    e = json.load(open(CRED_CACHE)) if os.path.exists(CRED_CACHE) else json.load(open(DESKTOP_CFG))["mcpServers"]["metabase"]["env"]
    return e["METABASE_URL"].rstrip("/"), e.get("METABASE_USER_EMAIL"), e.get("METABASE_PASSWORD")


def _open(req):
    import time as _t
    last = None
    for a in range(4):
        try:
            with urllib.request.urlopen(req, timeout=180) as r:
                return json.loads(r.read().decode())
        except Exception as e:
            last = e; _t.sleep(3 * (a + 1))
    raise last


def main():
    url, email, pw = creds()
    _mbkey = os.environ.get("METABASE_API_KEY")
    if not _mbkey:
        try: _mbkey = json.load(open(CRED_CACHE)).get("METABASE_API_KEY")
        except Exception: _mbkey = None
    if _mbkey:
        AUTH = {"x-api-key": _mbkey}
    else:
        tok = _open(urllib.request.Request(url + "/api/session", data=json.dumps({"username": email, "password": pw}).encode(),
                                           method="POST", headers={"Content-Type": "application/json"}))["id"]
        AUTH = {"X-Metabase-Session": tok}

    tt = {"#%d" % CARD: {"card-id": CARD, "type": "card", "name": "#%d" % CARD,
                         "id": "a1b2c3d4-0000-0000-0000-000000010181", "display-name": "#%d" % CARD}}
    sql = ("SELECT assignee_name, task_created_at, task_due_date, completion_date, status, sla_in_min, tat "
           "FROM {{#%d}} WHERE sub_type='troubleshoot_sop' AND assignee_bucket='GC'" % CARD)
    q = {"database": 6, "type": "native", "native": {"query": sql, "template-tags": tt}}
    body = urllib.parse.urlencode({"query": json.dumps(q)}).encode()
    req = urllib.request.Request(url + "/api/dataset/json", data=body, method="POST",
                                 headers={**AUTH, "Content-Type": "application/x-www-form-urlencoded"})
    rows_in = _open(req)

    def _num(v):
        try: return float(v)
        except (TypeError, ValueError): return None

    rows = []
    for r in rows_in:
        gc = str(r.get("assignee_name") or "").strip() or "Unassigned"
        d = str(r.get("task_created_at") or "")[:10]
        if not d:
            continue
        tat = _num(r.get("tat")); sla = _num(r.get("sla_in_min"))
        within = 1 if (tat is not None and sla is not None and tat <= sla) else 0
        rows.append({"gc": gc, "d": d, "w": within})
    rows.sort(key=lambda x: x["d"])
    out = {"generatedAt": datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
           "rows": rows,
           "dateRange": {"min": rows[0]["d"] if rows else None, "max": rows[-1]["d"] if rows else None}}
    json.dump(out, open(OUT, "w"), separators=(",", ":"))
    print(f"[ts-sop] card {CARD} troubleshoot_sop GC: {len(rows)} tasks "
          f"({out['dateRange']['min']}..{out['dateRange']['max']}) -> {OUT}")


if __name__ == "__main__":
    main()
