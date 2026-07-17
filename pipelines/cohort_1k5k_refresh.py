#!/usr/bin/env python3
"""Build cohort_1k5k_data.json from card 11838 — 1k-5k cohort analysis
(go-live month x relative week: total / spending / >=3K sellers). Powers the
Bird Eye View cohort heatmap (Spending Sellers, >=3K Sellers, 3K Retention).

Run: cd ~/shopdeck-metrics-site && python3 pipelines/cohort_1k5k_refresh.py
"""
import json, os, urllib.request, datetime

CARD = 11840   # seller-level cohort detail (cohort_month x relative_week x seller_id x company x sw_spend)
REPO = os.path.expanduser(os.environ.get("REPO_DIR", "~/shopdeck-metrics-site"))
OUT = os.path.join(REPO, "cohort_1k5k_data.json")
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
            with urllib.request.urlopen(r, timeout=180) as resp:
                return json.loads(resp.read().decode())
        except Exception as e:
            last = e; _t.sleep(3 * (attempt + 1))
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
        tok = req(url + "/api/session", "POST", {"username": email, "password": pw}, {"Content-Type": "application/json"})["id"]
        AUTH = {"X-Metabase-Session": tok}
    H = {"Content-Type": "application/json", **AUTH}

    rows = req(f"{url}/api/card/{CARD}/query/json", "POST", {}, H)
    max_week = 0
    det = {}   # cohort -> week -> [ {s, n, sp} ]  (all sellers present that week)
    for r in rows:
        cm = str(r.get("cohort_month") or "")
        w = r.get("relative_week")
        if not cm or w is None:
            continue
        w = int(w); max_week = max(max_week, w)
        try: sp = round(float(r.get("sw_spend") or 0))
        except (TypeError, ValueError): sp = 0
        det.setdefault(cm, {}).setdefault(w, []).append(
            {"s": str(r.get("seller_id") or ""), "n": str(r.get("company") or ""), "sp": sp})
    cohorts = sorted(det.keys(), reverse=True)
    out_rows = []
    for cm in cohorts:
        cells, dcell = {}, {}
        for w in sorted(det[cm]):
            sellers = sorted(det[cm][w], key=lambda x: -x["sp"])
            cells[str(w)] = {
                "t": len(sellers),
                "s": sum(1 for x in sellers if x["sp"] > 0),
                "g": sum(1 for x in sellers if x["sp"] >= 3000),
            }
            dcell[str(w)] = sellers
        out_rows.append({"cohort": cm, "cells": cells, "det": dcell})
    out = {
        "generatedAt": datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "maxWeek": max_week, "cohorts": cohorts, "rows": out_rows,
    }
    json.dump(out, open(OUT, "w"), separators=(",", ":"))
    print(f"[cohort-1k5k] card {CARD}: {len(cohorts)} cohorts, W0..W{max_week}, {len(rows)} seller-weeks -> {OUT}")


if __name__ == "__main__":
    main()
