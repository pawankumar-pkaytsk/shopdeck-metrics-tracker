#!/usr/bin/env python3
"""Build creative_test_data.json — "Golive Creative Testing" project tracker.

Source: card 12207 (Clothing A2H cohort — Meta campaign/adset/ad performance day-on-day
post go-live). Grain: one row per level x entity x seller x day.

IMPORTANT — why we do not use the card's `campaign_type` column:
  card 12207 assigns campaign_type via MOD(ABS(FARM_FINGERPRINT(seller_id)),2), i.e. a
  deterministic COIN FLIP on the seller id. It is a placeholder, not the real arm
  (it agrees with the real setup only ~29% of the time). So the experiment arm is
  derived here from the ADSET NAME, which is how the arms are actually labelled:
     'catalog' / 'cat.' / 'all product'      -> Catalogue arm
     'banner' / 'video' / 'creative' / 'ugc' -> Creative arm
     both keywords                           -> Both (single mixed adset)
     neither                                 -> Unclassified (naming hygiene gap)
  A seller running >=1 Catalogue adset AND >=1 Creative adset is a
  "Creative + Catalogue" setup; a seller with only Creative adsets is "Creative only".

Ratio discipline: every published ratio is pooled (sum(numerator)/sum(denominator)),
never an average of per-row ratios. Only `final` days are used (the adset table lands a
day late, so 'partial-intraday' rows would understate spend).

Run: cd ~/shopdeck-metrics-site && python3 pipelines/creative_test_refresh.py
"""
import json, os, statistics, urllib.request, datetime
from collections import defaultdict

CARD = 12207
REPO = os.path.expanduser(os.environ.get("REPO_DIR", "~/shopdeck-metrics-site"))
OUT = os.path.join(REPO, "creative_test_data.json")
CRED_CACHE = os.path.expanduser("~/metabase-arr-refresh/.mbcreds")
DESKTOP_CFG = os.path.expanduser("~/Library/Application Support/Claude/claude_desktop_config.json")

SGMV_THRESHOLDS = [30, 50, 100]   # S/GMV % "success" bars (lower is better)


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


def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def arm_of(name):
    """Experiment arm from the adset/campaign name (see module docstring)."""
    n = (name or "").lower()
    cat = ("catalog" in n) or ("cat." in n) or ("all product" in n)
    cre = ("banner" in n) or ("video" in n) or ("creative" in n) or ("ugc" in n)
    if cat and cre:
        return "Both"
    if cat:
        return "Catalogue"
    if cre:
        return "Creative"
    return "Unclassified"


def _blank():
    return dict(sp=0.0, imp=0.0, clk=0.0, pur=0.0, mgmv=0.0, pgmv=0.0, sellers=set(), ents=set())


def _acc(d, r, ent=None):
    d["sp"] += num(r.get("spend")); d["imp"] += num(r.get("impressions"))
    d["clk"] += num(r.get("clicks")); d["pur"] += num(r.get("purchases"))
    d["mgmv"] += num(r.get("meta_gmv")); d["pgmv"] += num(r.get("platform_gmv"))
    d["sellers"].add(r.get("seller_id"))
    if ent:
        d["ents"].add(ent)


def _pub(d):
    """Pooled metrics from an accumulator — ratios are sum/sum, never mean-of-ratios."""
    sp, imp, clk, pur, mgmv, pgmv = d["sp"], d["imp"], d["clk"], d["pur"], d["mgmv"], d["pgmv"]
    rd = lambda a, b, m=1: (round(m * a / b, 2) if b else None)
    return {
        "spend": round(sp), "impressions": int(imp), "clicks": int(clk),
        "purchases": round(pur, 1), "metaGmv": round(mgmv), "platformGmv": round(pgmv),
        "sellers": len(d["sellers"]), "entities": len(d["ents"]),
        "sgmv": rd(sp, mgmv, 100), "roas": rd(mgmv, sp),
        "sgmvPlatform": rd(sp, pgmv, 100), "roasPlatform": rd(pgmv, sp),
        "cpm": rd(sp, imp, 1000), "ctr": rd(clk, imp, 100), "cpc": rd(sp, clk),
        "c2pr": rd(pur, clk, 100), "aov": rd(mgmv, pur),
    }


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

    rows = req(f"{url}/api/card/{CARD}/query/json", "POST", {}, H)
    final = [r for r in rows if r.get("day_status") == "final"]

    # ---- derive per-seller setup from the arms their adsets actually run ----
    seller_arms = defaultdict(set)
    for r in final:
        if r.get("level") != "adset":
            continue
        a = arm_of(r.get("adset_name"))
        if a != "Unclassified":
            seller_arms[r["seller_id"]].add(a)
    setup = {}
    for sid, s in seller_arms.items():
        cat = ("Catalogue" in s) or ("Both" in s)
        cre = ("Creative" in s) or ("Both" in s)
        setup[sid] = "Creative + Catalogue" if (cat and cre) else ("Catalogue only" if cat else "Creative only")
    cc_sellers = {sid for sid, st in setup.items() if st == "Creative + Catalogue"}

    name_of = {}
    for r in final:
        if r.get("level") == "seller":
            name_of.setdefault(r["seller_id"], r["seller_id"][:8])

    # ---- (1) OVERALL: setup x campaign level, plus seller level for true platform GMV ----
    overall = defaultdict(_blank)
    for r in final:
        st = setup.get(r["seller_id"])
        if not st or r.get("level") != "campaign":
            continue
        _acc(overall[st], r, ent=r.get("campaign_id"))
    overall_seller = defaultdict(_blank)
    for r in final:
        st = setup.get(r["seller_id"])
        if not st or r.get("level") != "seller":
            continue
        _acc(overall_seller[st], r)
    overall_pub = {}
    for st in set(list(overall) + list(overall_seller)):
        o = _pub(overall[st]) if st in overall else _pub(_blank())
        s = _pub(overall_seller[st]) if st in overall_seller else {}
        o["platformGmv"] = s.get("platformGmv")
        o["sgmvPlatform"] = s.get("sgmvPlatform")
        o["roasPlatform"] = s.get("roasPlatform")
        overall_pub[st] = o

    # ---- (2) ARM performance inside Creative+Catalogue sellers (adset grain) ----
    arms = defaultdict(_blank)
    for r in final:
        if r.get("level") != "adset" or r["seller_id"] not in cc_sellers:
            continue
        a = arm_of(r.get("adset_name"))
        if a == "Unclassified":
            continue
        _acc(arms[a], r, ent=r.get("adset_id"))
    arm_total_spend = sum(d["sp"] for d in arms.values()) or 1.0
    arms_pub = {}
    for a, d in arms.items():
        p = _pub(d)
        p["spendShare"] = round(100 * d["sp"] / arm_total_spend, 1)
        arms_pub[a] = p

    # ---- (5) per-adset lifetime rows -> success rates + mean/median S/GMV ----
    per_adset = {}
    for r in final:
        if r.get("level") != "adset":
            continue
        a = arm_of(r.get("adset_name"))
        if a == "Unclassified":
            continue
        k = (r["seller_id"], r.get("adset_id"))
        p = per_adset.setdefault(k, dict(sid=r["seller_id"], adsetId=r.get("adset_id"),
                                         name=r.get("adset_name") or "", arm=a,
                                         setup=setup.get(r["seller_id"], ""),
                                         gc=r.get("growth_consultant_name") or "",
                                         gm=r.get("growth_manager_name") or "",
                                         sp=0.0, imp=0.0, clk=0.0, pur=0.0, mgmv=0.0, days=set()))
        p["sp"] += num(r.get("spend")); p["imp"] += num(r.get("impressions"))
        p["clk"] += num(r.get("clicks")); p["pur"] += num(r.get("purchases"))
        p["mgmv"] += num(r.get("meta_gmv"))
        p["days"].add(r.get("day_since_go_live"))
    adset_rows = []
    for p in per_adset.values():
        if p["sp"] <= 0:
            continue
        adset_rows.append({
            "sid": p["sid"], "name": p["name"], "arm": p["arm"], "setup": p["setup"],
            "gc": p["gc"], "gm": p["gm"], "days": len(p["days"]),
            "spend": round(p["sp"]), "impressions": int(p["imp"]), "clicks": int(p["clk"]),
            "purchases": round(p["pur"], 1), "metaGmv": round(p["mgmv"]),
            "sgmv": (round(100 * p["sp"] / p["mgmv"], 1) if p["mgmv"] else None),
            "cpm": (round(1000 * p["sp"] / p["imp"], 2) if p["imp"] else None),
            "ctr": (round(100 * p["clk"] / p["imp"], 3) if p["imp"] else None),
            "c2pr": (round(100 * p["pur"] / p["clk"], 3) if p["clk"] else None),
        })
    adset_rows.sort(key=lambda x: -x["spend"])

    success = {}
    for a in ("Creative", "Catalogue", "Both"):
        L = [x for x in adset_rows if x["arm"] == a]
        if not L:
            continue
        conv = [x for x in L if (x["purchases"] or 0) > 0]
        sgs = [x["sgmv"] for x in L if x["sgmv"] is not None]
        s = {"n": len(L), "converting": len(conv),
             "convRate": round(100 * len(conv) / len(L), 1),
             "meanSgmv": (round(statistics.mean(sgs), 1) if sgs else None),
             "medianSgmv": (round(statistics.median(sgs), 1) if sgs else None),
             "pooledSgmv": (round(100 * sum(x["spend"] for x in L) / sum(x["metaGmv"] for x in L), 1)
                            if sum(x["metaGmv"] for x in L) else None),
             "thresholds": {}}
        for th in SGMV_THRESHOLDS:
            ok = [x for x in L if x["sgmv"] is not None and x["sgmv"] <= th]
            s["thresholds"][str(th)] = {"n": len(ok), "rate": round(100 * len(ok) / len(L), 1)}
        success[a] = s

    # ---- (6) AGING: day-since-go-live x arm (C+C sellers) ----
    aging = defaultdict(lambda: defaultdict(_blank))
    for r in final:
        if r.get("level") != "adset" or r["seller_id"] not in cc_sellers:
            continue
        a = arm_of(r.get("adset_name"))
        if a == "Unclassified":
            continue
        _acc(aging[a][r.get("day_since_go_live")], r, ent=r.get("adset_id"))
    max_day = max([d for m in aging.values() for d in m] or [0])
    aging_pub = {"days": list(range(0, max_day + 1)), "byArm": {}, "spendShare": []}
    for a, m in aging.items():
        aging_pub["byArm"][a] = {str(d): _pub(m[d]) for d in m}
    for d in aging_pub["days"]:
        cre = aging["Creative"][d]["sp"] if d in aging["Creative"] else 0
        cat = aging["Catalogue"][d]["sp"] if d in aging["Catalogue"] else 0
        aging_pub["spendShare"].append({"d": d, "creative": round(cre), "catalogue": round(cat),
                                        "catPct": (round(100 * cat / (cre + cat), 1) if (cre + cat) else None)})
    # campaign-level S/GMV per day (for "does an adset overtake the campaign?" question)
    camp_day = defaultdict(_blank)
    for r in final:
        if r.get("level") != "campaign" or r["seller_id"] not in cc_sellers:
            continue
        _acc(camp_day[r.get("day_since_go_live")], r, ent=r.get("campaign_id"))
    aging_pub["campaign"] = {str(d): _pub(camp_day[d]) for d in camp_day}

    # ---- per-seller table (drilldown) ----
    seller_rows = []
    per_seller = defaultdict(_blank)
    for r in final:
        if r.get("level") != "seller":
            continue
        _acc(per_seller[r["seller_id"]], r)
    for sid, d in per_seller.items():
        p = _pub(d)
        arms_here = sorted({arm_of(x["name"]) for x in adset_rows if x["sid"] == sid})
        seller_rows.append({
            "sid": sid, "setup": setup.get(sid, "no adset data"),
            "arms": ", ".join(arms_here),
            "spend": p["spend"], "metaGmv": p["metaGmv"], "platformGmv": p["platformGmv"],
            "sgmv": p["sgmv"], "sgmvPlatform": p["sgmvPlatform"],
            "cpm": p["cpm"], "ctr": p["ctr"], "c2pr": p["c2pr"],
            "gc": next((x["gc"] for x in adset_rows if x["sid"] == sid), ""),
            "gm": next((x["gm"] for x in adset_rows if x["sid"] == sid), ""),
        })
    seller_rows.sort(key=lambda x: -(x["spend"] or 0))

    # ---- data quality / caveats ----
    all_sellers = {r["seller_id"] for r in final}
    uncl = [r for r in final if r.get("level") == "adset" and arm_of(r.get("adset_name")) == "Unclassified"]
    nonai_spend = sum(num(r.get("spend")) for r in final if r.get("level") == "campaign" and r.get("ai_flag") == 0)
    ai_spend = sum(num(r.get("spend")) for r in final if r.get("level") == "campaign" and r.get("ai_flag") == 1)
    ct_agree = sum(1 for sid in setup if {r["seller_id"]: r.get("campaign_type") for r in final}.get(sid) == setup[sid])
    dq = {
        "rows": len(rows), "finalRows": len(final),
        "partialRows": len(rows) - len(final),
        "sellersTotal": len(all_sellers),
        "sellersClassified": len(setup),
        "setupCounts": {k: sum(1 for v in setup.values() if v == k) for k in set(setup.values())},
        "unclassifiedAdsetRows": len(uncl),
        "unclassifiedAdsetSpend": round(sum(num(r.get("spend")) for r in uncl)),
        "unclassifiedNames": sorted({(r.get("adset_name") or "(blank)") for r in uncl})[:12],
        "aiSpend": round(ai_spend), "nonAiSpend": round(nonai_spend),
        "cardCampaignTypeAgreement": (round(100 * ct_agree / len(setup)) if setup else None),
        "dateMin": min((r["date"] for r in final), default=None),
        "dateMax": max((r["date"] for r in final), default=None),
        "maxDaySinceGoLive": max((r["day_since_go_live"] for r in final), default=None),
    }

    out = {
        "generatedAt": datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "card": CARD,
        "thresholds": SGMV_THRESHOLDS,
        "overall": overall_pub,
        "arms": arms_pub,
        "success": success,
        "aging": aging_pub,
        "adsets": adset_rows,
        "sellers": seller_rows,
        "dq": dq,
    }
    json.dump(out, open(OUT, "w"), separators=(",", ":"))
    print(f"[creative-test] {len(final)} final rows · {len(setup)} classified sellers "
          f"{dq['setupCounts']} · {len(adset_rows)} adsets · arms={ {a: arms_pub[a]['spendShare'] for a in arms_pub} } -> {OUT}")


if __name__ == "__main__":
    main()
