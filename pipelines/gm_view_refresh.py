#!/usr/bin/env python3
"""Build gm_view_data.json for the 'View as Growth Manager' role.

Per GM (from card 7753 roles surfaced in gc_detail_data people-map):
  overall  : assigned, live, yet-to-golive, spend/live, live/assigned, task compliance
             (+ within SLA), callback compliance within SLA, T/S eligible (today), HITS
             target & achieved (current month).
  gcs      : the GM's Growth Consultants with their gc_data metrics (GC Management section).
  gls      : GL Management — placeholder for now (needs growth_lead wiring), UI shows awaiting.

Local-only (reads gc_data.json, gc_detail_data.json, gm_compliance_data.json, golive_data.json,
ts_data.json). No BigQuery, so it runs regardless of daily quota.

Run: cd ~/shopdeck-metrics-site && python3 pipelines/gm_view_refresh.py
"""
import json, os, datetime, re
from collections import defaultdict, Counter

REPO = os.path.expanduser(os.environ.get("REPO_DIR", "~/shopdeck-metrics-site"))
OUT = os.path.join(REPO, "gm_view_data.json")
_norm = lambda v: re.sub(r"\s+", " ", str(v or "").strip())
_key = lambda v: _norm(v).lower()


def load(name, dflt):
    p = os.path.join(REPO, name)
    return json.load(open(p)) if os.path.exists(p) else dflt


def num(v):
    try: return float(v)
    except (TypeError, ValueError): return 0.0


def main():
    gc_data = load("gc_data.json", {}) or {}
    by_gc = gc_data.get("byGC", {})
    detail = (load("gc_detail_data.json", {}) or {}).get("detail", {})
    gmc = load("gm_compliance_data.json", {}) or {}
    ts_sellers = (load("ts_data.json", {}) or {}).get("sellers", {})
    SPEND_TS = 3540

    # GC (canonical, from gc_data) -> GM, via the majority people.GM of that GC's sellers
    gc_gm = {}
    for gc, v in by_gc.items():
        gms = Counter()
        for s in v.get("sellers", []):
            gm = _norm(((detail.get(s["id"]) or {}).get("people") or {}).get("GM"))
            if gm and gm.lower() not in ("", "unassigned", "-"):
                gms[gm] += 1
        if gms:
            gc_gm[gc] = gms.most_common(1)[0][0]

    # gm_compliance: T/S done + eligible (rolling) per GM (match by normalized name)
    gmc_by = {}
    for g, v in (gmc.get("byGM") or {}).items():
        elig = len((gmc.get("tsEligRolling") or {}).get(g, []) or [])
        ts_events = v.get("ts", []) if isinstance(v, dict) else []
        gmc_by[_key(g)] = {"tsEligible": elig, "tsDone7": len(ts_events)}

    gm_gcs = defaultdict(list)
    for gc, gm in gc_gm.items():
        gm_gcs[gm].append(gc)

    by_gm = {}
    for gm, gcs in gm_gcs.items():
        tot = {"assigned": 0, "live": 0, "spending": 0, "spend3k": 0, "hitsTarget": 0, "hitsAchieved": 0}
        gc_rows = []
        elig_today = 0
        for gc in gcs:
            met = (by_gc.get(gc) or {}).get("metrics", {})
            for k in tot:
                tot[k] += num(met.get(k))
            sids = [s["id"] for s in by_gc.get(gc, {}).get("sellers", [])]
            e = sum(1 for sid in sids if num((ts_sellers.get(sid) or {}).get("s7")) > SPEND_TS)
            elig_today += e
            gc_rows.append({
                "gc": gc, "assigned": met.get("assigned"), "live": met.get("live"),
                "spending": met.get("spending"), "spendLivePct": met.get("spendLivePct"),
                "liveAssignedPct": met.get("liveAssignedPct"), "spend3kLivePct": met.get("spend3kLivePct"),
                "hitsTarget": met.get("hitsTarget"), "hitsAchieved": met.get("hitsAchieved"),
                "tsEligible": e,
            })
        gc_rows.sort(key=lambda x: -(x.get("assigned") or 0))
        assigned, live, spending = tot["assigned"], tot["live"], tot["spending"]
        comp = gmc_by.get(_key(gm), {})
        by_gm[gm] = {
            "overall": {
                "assigned": assigned, "live": live, "yetToGolive": max(0, assigned - live),
                "spending": spending, "spend3k": tot["spend3k"],
                "spendLivePct": round(spending / live * 100, 1) if live else None,
                "liveAssignedPct": round(live / assigned * 100, 1) if assigned else None,
                "hitsTarget": round(tot["hitsTarget"]), "hitsAchieved": round(tot["hitsAchieved"]),
                "tsEligibleToday": elig_today,
                "tsDone7": comp.get("tsDone7"),
            },
            "gcs": gc_rows,
            "gcCount": len(gcs),
        }

    out = {
        "generatedAt": datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "gms": sorted(by_gm.keys()),
        "byGM": by_gm,
    }
    json.dump(out, open(OUT, "w"), separators=(",", ":"))
    print(f"[gm-view] {len(by_gm)} GMs, {sum(v['gcCount'] for v in by_gm.values())} GCs mapped -> {OUT}")


if __name__ == "__main__":
    main()
