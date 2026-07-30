#!/usr/bin/env python3
"""Build cohort_google_data.json — "Cohort Analysis — 1k-5k (Google)".

Same grid as the 1k-5k cohort heatmap (section 6) but for GOOGLE-LIVE sellers, RE-ANCHORED so
that W0 is the ISO week of the seller's FIRST GOOGLE SPEND (W1.. follow), with the cohort row
being the month of that week. Cohorts start Mar-26.

Four bucket variants, toggled in the view. The predicates are copied verbatim from the sibling
"Weekly Metrics (1k-5k) · Google" table (the card-11815 variants in bev_refresh), so the two
tables slice the population identically:
    hit1    : team = 'HITS' AND good_seller IS NULL
    hit2    : hit2 = 1
    both    : either of the above                (HIT1 and HIT2 overlap by design, as there)
    revenue : good_seller IS NULL AND team is not 'HITS' AND hit2 is not 1

Google-live: has a google_ad_account_id AND lifetime google spend > 10 (card 7401 — the same
source as Spend/Live and the go-live multiplier, so these sellers tie to the rest of the app).

Sources (all existing cards — no new BigQuery scan):
    10453  hit_master_data       -> bucket flags
    7401   google spend/account  -> google-live
    10469  day-wise seller spend -> first-google-spend week (W0 anchor) + weekly spend
    11850  google golive month   -> cross-check of the W0 anchor

Spend metric: weekly TOTAL spend (spend_overall from card 10469 = meta + google). Section 6
uses gc_view_3.marketing_spend; the two agree to ~1% (median ratio 1.006), so the >= 3K
threshold is equivalent. Each seller-week's google-only spend is carried for the drilldown.

Run: cd ~/shopdeck-metrics-site && python3 pipelines/cohort_google_refresh.py
"""
import json, os, urllib.request, datetime
from collections import defaultdict

HIT_CARD = 10453
GLIVE_CARD = 7401
SPEND_CARD = 10469
GOLIVE_MONTH_CARD = 11850
GT_THRESHOLD = 10          # lifetime google spend > 10 => live on google
SPEND3K = 3000
MAX_WEEK = 16
COHORT_FLOOR = "2026-03"   # cohorts (month of first google spend) start here
REPO = os.path.expanduser(os.environ.get("REPO_DIR", "~/shopdeck-metrics-site"))
OUT = os.path.join(REPO, "cohort_google_data.json")
CRED_CACHE = os.path.expanduser("~/metabase-arr-refresh/.mbcreds")
DESKTOP_CFG = os.path.expanduser("~/Library/Application Support/Claude/claude_desktop_config.json")

BUCKETS = ["hit1", "hit2", "both", "revenue"]


def creds():
    if os.environ.get("METABASE_URL"):
        return (os.environ["METABASE_URL"].rstrip("/"), os.environ.get("METABASE_USER_EMAIL"),
                os.environ.get("METABASE_PASSWORD"))
    e = json.load(open(CRED_CACHE)) if os.path.exists(CRED_CACHE) else json.load(open(DESKTOP_CFG))["mcpServers"]["metabase"]["env"]
    return e["METABASE_URL"].rstrip("/"), e.get("METABASE_USER_EMAIL"), e.get("METABASE_PASSWORD")


def fnum(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def iso_monday(d):
    return (d - datetime.timedelta(days=d.weekday())).isoformat()


def main():
    url, email, pw = creds()
    key = os.environ.get("METABASE_API_KEY")
    if not key:
        try:
            key = json.load(open(CRED_CACHE)).get("METABASE_API_KEY")
        except Exception:
            key = None

    _tok = [None]

    def fetch(cid):
        """api-key first; on failure fall back to a session token, which returns Metabase's
        cached result instead of forcing a fresh (quota-consuming) BigQuery scan."""
        if key:
            try:
                r = urllib.request.Request(f"{url}/api/card/{cid}/query/json", data=b"{}",
                                           headers={"x-api-key": key, "Content-Type": "application/json"},
                                           method="POST")
                return json.loads(urllib.request.urlopen(r, timeout=900).read())
            except Exception as e:
                print(f"[cohort-google] card {cid} api-key path failed ({str(e)[:60]}) — trying session cache")
        if _tok[0] is None:
            r = urllib.request.Request(url + "/api/session",
                                       data=json.dumps({"username": email, "password": pw}).encode(),
                                       headers={"Content-Type": "application/json"}, method="POST")
            _tok[0] = json.loads(urllib.request.urlopen(r, timeout=180).read())["id"]
        r = urllib.request.Request(f"{url}/api/card/{cid}/query/json", data=b"{}",
                                   headers={"X-Metabase-Session": _tok[0], "Content-Type": "application/json"},
                                   method="POST")
        return json.loads(urllib.request.urlopen(r, timeout=900).read())

    # ---- bucket flags -------------------------------------------------------------------
    flags, names = {}, {}
    for r in fetch(HIT_CARD):
        sid = str(r.get("seller_id") or "").strip()
        if not sid:
            continue
        team = str(r.get("team") or "").strip().upper()
        good = str(r.get("good_seller") or "").strip() not in ("", "None", "none")
        hit2 = str(r.get("hit2") or "").strip() in ("1", "1.0", "True", "true")
        f = flags.setdefault(sid, {"hit1": False, "hit2": False, "revenue": False})
        if team == "HITS" and not good:
            f["hit1"] = True
        if hit2:
            f["hit2"] = True
        if (not good) and team != "HITS" and not hit2:
            f["revenue"] = True
        if r.get("seller_name"):
            names.setdefault(sid, str(r.get("seller_name")))
    for sid, f in flags.items():
        f["both"] = f["hit1"] or f["hit2"]

    # ---- google-live --------------------------------------------------------------------
    glive = set()
    for r in fetch(GLIVE_CARD):
        sid = str(r.get("seller_id") or "").strip()
        acct = str(r.get("google_ad_account_id") or "").strip()
        if sid and acct and fnum(r.get("total_marketing_spend_with_tax")) > GT_THRESHOLD:
            glive.add(sid)

    # ---- daily spend -> weekly total/google + first-google-spend week -------------------
    wk_tot, wk_goog = defaultdict(float), defaultdict(float)
    g_first = {}        # sid -> Monday of the ISO week of first google spend (the W0 anchor)
    g_first_d = {}      # sid -> the first google spend DATE (drives the cohort-month label)
    for r in fetch(SPEND_CARD):
        sid = str(r.get("seller_id") or "").strip()
        if not sid or sid not in glive:
            continue
        try:
            d = datetime.date.fromisoformat(str(r.get("date") or "")[:10])
        except ValueError:
            continue
        mon = iso_monday(d)
        tot, goog = fnum(r.get("spend_overall")), fnum(r.get("spend_google"))
        if tot:
            wk_tot[(sid, mon)] += tot
        if goog > 0:
            wk_goog[(sid, mon)] += goog
            ds = d.isoformat()
            if sid not in g_first_d or ds < g_first_d[sid]:
                g_first_d[sid] = ds
                g_first[sid] = mon
        if r.get("seller_name"):
            names.setdefault(sid, str(r.get("seller_name")))

    # cross-check the W0 anchor against card 11850 (google golive month)
    golive_month = {}
    try:
        for r in fetch(GOLIVE_MONTH_CARD):
            sid = str(r.get("seller_id") or "").strip()
            gm = str(r.get("google_golive_month") or "")[:7]
            if sid and gm:
                golive_month[sid] = gm
    except Exception as e:
        print(f"[cohort-google] card {GOLIVE_MONTH_CARD} unavailable ({str(e)[:60]}) — anchor cross-check skipped")
    anchor_mismatch = sorted(sid for sid, ds in g_first_d.items()
                             if sid in golive_month and golive_month[sid] != ds[:7])
    # guard: card 11850 is the authority on google golive. If it says the seller went live on
    # google BEFORE the cohort floor, card 10469's window (starts 2025-01) clipped the true
    # first spend — exclude them rather than mis-anchor them into a recent cohort.
    pre_floor = {sid for sid, gm in golive_month.items() if gm < COHORT_FLOOR}

    # ---- per-bucket cohort grids --------------------------------------------------------
    today = datetime.date.today()
    variants = {}
    for bucket in BUCKETS:
        det = defaultdict(lambda: defaultdict(list))
        for sid, g0 in g_first.items():
            if not flags.get(sid, {}).get(bucket):
                continue
            if sid in pre_floor:
                continue                          # went live on google before the floor (per card 11850)
            cm = g_first_d.get(sid, g0)[:7]       # cohort = month of the first-spend DATE
            if cm < COHORT_FLOOR:
                continue
            g0d = datetime.date.fromisoformat(g0)
            for w in range(0, MAX_WEEK + 1):
                mon = g0d + datetime.timedelta(weeks=w)
                if mon > today:
                    break
                k = (sid, mon.isoformat())
                if k not in wk_tot and k not in wk_goog:
                    continue                      # seller not present that week at all
                det[cm][w].append({
                    "s": sid, "n": names.get(sid, ""),
                    "sp": round(wk_tot.get(k, 0.0)),
                    "gsp": round(wk_goog.get(k, 0.0)),
                    "g0": g_first_d.get(sid, g0),
                })
        cohorts = sorted(det.keys(), reverse=True)
        rows = []
        for cm in cohorts:
            cells, dcell = {}, {}
            for w in sorted(det[cm]):
                sellers = sorted(det[cm][w], key=lambda x: -x["sp"])
                cells[str(w)] = {
                    "t": len(sellers),
                    "s": sum(1 for x in sellers if x["sp"] > 0),
                    "g": sum(1 for x in sellers if x["sp"] >= SPEND3K),
                }
                dcell[str(w)] = sellers
            rows.append({"cohort": cm, "cells": cells, "det": dcell})
        variants[bucket] = {
            "cohorts": cohorts, "rows": rows,
            "sellers": len({x["s"] for cm in det for w in det[cm] for x in det[cm][w]}),
        }

    out = {
        "generatedAt": datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "cards": {"hit": HIT_CARD, "glive": GLIVE_CARD, "spend": SPEND_CARD,
                  "goliveMonth": GOLIVE_MONTH_CARD},
        "gtThreshold": GT_THRESHOLD, "spend3k": SPEND3K, "maxWeek": MAX_WEEK,
        "cohortFloor": COHORT_FLOOR, "buckets": BUCKETS, "variants": variants,
        "dq": {
            "googleLiveSellers": len(glive),
            "googleLiveWithFirstSpend": len(g_first),
            "sellersByBucket": {b: variants[b]["sellers"] for b in BUCKETS},
            "anchorCrossCheckedAgainst": GOLIVE_MONTH_CARD,
            "anchorMismatchVs11850": len(anchor_mismatch),
        },
    }
    json.dump(out, open(OUT, "w"), separators=(",", ":"))
    print(f"[cohort-google] cohorts from {COHORT_FLOOR}, W0..W{MAX_WEEK} · sellers by bucket "
          f"{out['dq']['sellersByBucket']} · anchor mismatch vs card {GOLIVE_MONTH_CARD}: "
          f"{len(anchor_mismatch)} -> {OUT}")


if __name__ == "__main__":
    main()
