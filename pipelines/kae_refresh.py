#!/usr/bin/env python3
"""Build kae_hits_data.json — KAE (Key Account Executive) task compliance for the HITS team.

Team mapping (card 11244): seller_id -> team_mapping ('HIT' = HITS team, 'REVENUE' = Revenue team).
Tasks (card 10959): KAE-bucket tasks (assignee_bucket='KAE') with created/completion dates, status,
SLA (sla_in_min) and turnaround (tat). We keep the HITS-team tasks (seller in a HIT-mapped seller)
as task-level rows so the UI can filter by any date range and aggregate per-KAE + overall:
  completed (Yesterday / Last 3d / Last 7d, by completion date), pending backlog, task compliance
  (completed / total) and task compliance within SLA (tat <= sla).

Revenue-team KAE data is provided later — this pipeline only emits the HITS side.

Run: cd ~/shopdeck-metrics-site && python3 pipelines/kae_refresh.py
"""
import json, os, datetime, urllib.request

TEAM_CARD = 11244   # seller_id -> team_mapping (HIT / REVENUE)
TASK_CARD = 10959   # All KAE tasks (assignee_bucket=KAE) with SLA/tat/status
REPO = os.path.expanduser(os.environ.get("REPO_DIR", "~/shopdeck-metrics-site"))
OUT = os.path.join(REPO, "kae_hits_data.json")
CRED_CACHE = os.path.expanduser("~/metabase-arr-refresh/.mbcreds")
DESKTOP_CFG = os.path.expanduser("~/Library/Application Support/Claude/claude_desktop_config.json")


def creds():
    if os.environ.get("METABASE_URL"):
        return os.environ["METABASE_URL"].rstrip("/"), os.environ.get("METABASE_USER_EMAIL"), os.environ.get("METABASE_PASSWORD")
    e = json.load(open(CRED_CACHE)) if os.path.exists(CRED_CACHE) else json.load(open(DESKTOP_CFG))["mcpServers"]["metabase"]["env"]
    return e["METABASE_URL"].rstrip("/"), e.get("METABASE_USER_EMAIL"), e.get("METABASE_PASSWORD")


def req(url, method="GET", body=None, H=None):
    import time as _t
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, method=method, headers=H or {})
    last = None
    for attempt in range(4):
        try:
            with urllib.request.urlopen(r, timeout=300) as resp:
                return json.loads(resp.read().decode())
        except Exception as e:
            last = e; _t.sleep(3 * (attempt + 1))
    raise last


def _num(v):
    try: return float(v)
    except (TypeError, ValueError): return None


def main():
    url, email, pw = creds()
    _mbkey = os.environ.get("METABASE_API_KEY")
    if not _mbkey:
        try: _mbkey = json.load(open(CRED_CACHE)).get("METABASE_API_KEY")
        except Exception: _mbkey = None
    AUTH = {"x-api-key": _mbkey} if _mbkey else {"X-Metabase-Session":
            req(url + "/api/session", "POST", {"username": email, "password": pw}, {"Content-Type": "application/json"})["id"]}
    H = {"Content-Type": "application/json", **AUTH}

    # seller -> team; HITS team = seller_ids mapped 'HIT'
    hit_sids = set()
    for r in req(f"{url}/api/card/{TEAM_CARD}/query/json", "POST", {}, H):
        if str(r.get("team_mapping") or "").strip().upper() == "HIT":
            hit_sids.add(str(r.get("seller_id") or "").strip())

    tasks = req(f"{url}/api/card/{TASK_CARD}/query/json", "POST", {}, H)
    _done = ("completed", "closed")
    rows = []
    for t in tasks:
        if str(t.get("assignee_bucket") or "").strip().upper() != "KAE":
            continue
        if str(t.get("seller_id") or "").strip() not in hit_sids:
            continue
        kae = str(t.get("assignee_name") or "").strip() or "Unassigned"
        cr = str(t.get("task_created_at") or "")[:10]
        cp = str(t.get("completion_date") or "")[:10]
        st = str(t.get("status") or "").strip().lower()
        done = st in _done
        tat, sla = _num(t.get("tat")), _num(t.get("sla_in_min"))
        within = 1 if (done and tat is not None and sla is not None and tat <= sla) else 0
        rows.append({"k": kae, "cr": cr, "cp": cp if done else "",
                     "d": 1 if done else 0, "w": within, "p": 1 if st == "pending" else 0})

    crs = [r["cr"] for r in rows if r["cr"]]
    out = {
        "generatedAt": datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "team": "HITS",
        "kaes": sorted({r["k"] for r in rows}),
        "dateRange": {"min": min(crs) if crs else None, "max": max(crs) if crs else None},
        "rows": rows,
    }
    json.dump(out, open(OUT, "w"), separators=(",", ":"))
    print(f"[kae] HITS: {len(rows)} KAE tasks · {len(out['kaes'])} KAEs · "
          f"{sum(r['d'] for r in rows)} done, {sum(r['p'] for r in rows)} pending "
          f"({out['dateRange']['min']}..{out['dateRange']['max']}) -> {OUT}")


if __name__ == "__main__":
    main()
