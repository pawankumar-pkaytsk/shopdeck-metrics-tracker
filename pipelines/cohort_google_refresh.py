#!/usr/bin/env python3
"""Build cohort_google_data.json — "Cohort Analysis — 1k-5k (Google)".

Exact replica of the 1k-5k cohort heatmap (card 11840 / cohort_1k5k_refresh.py) but
restricted to GOOGLE-LIVE sellers, so it is a strict subset of the 1k-5k cohort.

Google-live (same rule as the dashboard's Spend/Live + golive multiplier):
    has a google_ad_account_id  AND  lifetime google spend > 10
  * card 12264 applies this on db 6 (google_marketing_insights_master; that table demands a
    partition filter, so "lifetime" is the last 400 days — equivalent for a 12-month cohort).
  * card 7401 (db 23) is the authority used everywhere else in the dashboard, so the seller
    universe is intersected with it. The two agree to within one seller; the delta is reported.

Two spend bases, both emitted (the view toggles between them):
  * total  — weekly gc_view_3.marketing_spend (Meta + Google). Same metric as section 6, so
             this column is directly comparable with the all-1k-5k table.
  * google — weekly google-only spend. The honest basis for a Google read-out, since a
             Google-live seller's total spend is usually mostly Meta.

Cells per cohort x relative week: t = sellers present, s = spending (> 0), g = >= 3000.
3K Retention (computed in the view) = g at Wn / s at W0.

Run: cd ~/shopdeck-metrics-site && python3 pipelines/cohort_google_refresh.py
"""
import json, os, urllib.request, datetime

CARD = 12264        # 1k-5k cohort detail, google-live subset (total + google weekly spend)
GLIVE_CARD = 7401   # seller -> google_ad_account_id + lifetime google spend (dashboard authority)
GT_THRESHOLD = 10   # lifetime google spend > 10 => "live on google"
SPEND3K = 3000
REPO = os.path.expanduser(os.environ.get("REPO_DIR", "~/shopdeck-metrics-site"))
OUT = os.path.join(REPO, "cohort_google_data.json")
CRED_CACHE = os.path.expanduser("~/metabase-arr-refresh/.mbcreds")
DESKTOP_CFG = os.path.expanduser("~/Library/Application Support/Claude/claude_desktop_config.json")


def creds():
    if os.environ.get("METABASE_URL"):
        return (os.environ["METABASE_URL"].rstrip("/"), os.environ.get("METABASE_USER_EMAIL"),
                os.environ.get("METABASE_PASSWORD"))
    e = json.load(open(CRED_CACHE)) if os.path.exists(CRED_CACHE) else json.load(open(DESKTOP_CFG))["mcpServers"]["metabase"]["env"]
    return e["METABASE_URL"].rstrip("/"), e.get("METABASE_USER_EMAIL"), e.get("METABASE_PASSWORD")


def req(url, method="GET", body=None, H=None):
    import time as _t
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, method=method, headers=H or {})
    last = None
    for attempt in range(4):
        try:
            with urllib.request.urlopen(r, timeout=900) as resp:
                return json.loads(resp.read().decode())
        except Exception as e:
            last = e
            _t.sleep(3 * (attempt + 1))
    raise last


def fnum(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def main():
    url, email, pw = creds()
    key = os.environ.get("METABASE_API_KEY")
    if not key:
        try:
            key = json.load(open(CRED_CACHE)).get("METABASE_API_KEY")
        except Exception:
            key = None
    AUTH = {"x-api-key": key} if key else {"X-Metabase-Session": req(
        url + "/api/session", "POST", {"username": email, "password": pw},
        {"Content-Type": "application/json"})["id"]}
    H = {"Content-Type": "application/json", **AUTH}

    # authoritative Google-live set (same source as scaling_data / Spend/Live / golive multiplier)
    glive = set()
    try:
        for r in req(f"{url}/api/card/{GLIVE_CARD}/query/json", "POST", {}, H):
            sid = str(r.get("seller_id") or "").strip()
            acct = str(r.get("google_ad_account_id") or "").strip()
            if sid and acct and fnum(r.get("total_marketing_spend_with_tax")) > GT_THRESHOLD:
                glive.add(sid)
    except Exception as e:
        print(f"[cohort-google] card {GLIVE_CARD} unavailable ({e}) — falling back to card {CARD}'s own rule")

    rows = req(f"{url}/api/card/{CARD}/query/json", "POST", {}, H)
    card_sids = {str(r.get("seller_id") or "").strip() for r in rows}
    keep = (card_sids & glive) if glive else card_sids
    dropped = sorted(card_sids - keep)

    max_week = 0
    det = {}   # cohort -> week -> [ {s, n, sp, gsp} ]
    for r in rows:
        sid = str(r.get("seller_id") or "").strip()
        if sid not in keep:
            continue
        cm = str(r.get("cohort_month") or "")
        w = r.get("relative_week")
        if not cm or w is None:
            continue
        w = int(w)
        max_week = max(max_week, w)
        det.setdefault(cm, {}).setdefault(w, []).append({
            "s": sid, "n": str(r.get("company") or ""),
            "sp": round(fnum(r.get("sw_spend"))),
            "gsp": round(fnum(r.get("sw_google_spend"))),
        })

    cohorts = sorted(det.keys(), reverse=True)
    out_rows = []
    for cm in cohorts:
        cells, cellsG, dcell = {}, {}, {}
        for w in sorted(det[cm]):
            sellers = sorted(det[cm][w], key=lambda x: -x["sp"])
            cells[str(w)] = {
                "t": len(sellers),
                "s": sum(1 for x in sellers if x["sp"] > 0),
                "g": sum(1 for x in sellers if x["sp"] >= SPEND3K),
            }
            cellsG[str(w)] = {
                "t": len(sellers),
                "s": sum(1 for x in sellers if x["gsp"] > 0),
                "g": sum(1 for x in sellers if x["gsp"] >= SPEND3K),
            }
            dcell[str(w)] = sellers
        out_rows.append({"cohort": cm, "cells": cells, "cellsG": cellsG, "det": dcell})

    out = {
        "generatedAt": datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "card": CARD, "gliveCard": GLIVE_CARD, "gtThreshold": GT_THRESHOLD, "spend3k": SPEND3K,
        "maxWeek": max_week, "cohorts": cohorts, "rows": out_rows,
        "dq": {
            "sellersInCard": len(card_sids),
            "sellersGoogleLive": len(keep),
            "droppedNotInGliveCard": dropped,
            "gliveCardSellers": len(glive),
            "sellerWeeks": sum(len(v) for m in det.values() for v in m.values()),
        },
    }
    json.dump(out, open(OUT, "w"), separators=(",", ":"))
    print(f"[cohort-google] card {CARD}: {len(cohorts)} cohorts, W0..W{max_week}, "
          f"{len(keep)} google-live sellers (of {len(card_sids)} in card; dropped {len(dropped)}) -> {OUT}")


if __name__ == "__main__":
    main()
