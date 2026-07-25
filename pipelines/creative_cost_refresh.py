#!/usr/bin/env python3
"""Build creative_cost_data.json from card 12131 (user-level AI creative generation cost, daywise + team).
Powers 'Creative Generation - Cost Tracking' under Leadership -> Input Metrics.

Teams come from the card itself (card 12100 1k_5k_gl / card 12101 core_gc mappings + named overrides),
so a refresh of those mapping tables flows through here with no code change.

Run: cd ~/shopdeck-metrics-site && python3 pipelines/creative_cost_refresh.py
"""
import json, os, sys, urllib.request, datetime

CARD = 12131
REPO = os.path.expanduser(os.environ.get("REPO_DIR", "~/shopdeck-metrics-site"))
OUT = os.path.join(REPO, "creative_cost_data.json")
CRED_CACHE = os.path.expanduser("~/metabase-arr-refresh/.mbcreds")
DESKTOP_CFG = os.path.expanduser("~/Library/Application Support/Claude/claude_desktop_config.json")

# team label from the card -> compact code used by the frontend toggle
TEAM_CODE = {
    "Core GC": "coregc",
    "1K-5K": "1k5k",
    "Campaign Creation Team": "campaign",
    "Video Creation Team": "video",
}


def creds():
    if os.environ.get("METABASE_URL"):
        return os.environ["METABASE_URL"].rstrip("/"), os.environ.get("METABASE_USER_EMAIL"), os.environ.get("METABASE_PASSWORD")
    e = json.load(open(CRED_CACHE)) if os.path.exists(CRED_CACHE) else json.load(open(DESKTOP_CFG))["mcpServers"]["metabase"]["env"]
    return e["METABASE_URL"].rstrip("/"), e["METABASE_USER_EMAIL"], e["METABASE_PASSWORD"]


def req(url, method="GET", body=None, H=None):
    import time as _t
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, method=method, headers=H or {})
    last = None
    for attempt in range(4):
        try:
            with urllib.request.urlopen(r, timeout=600) as resp:
                return json.loads(resp.read().decode())
        except Exception as e:
            last = e
            _t.sleep(3 * (attempt + 1))
    raise last


def num(v):
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def main():
    url, email, pw = creds()
    mbkey = os.environ.get("METABASE_API_KEY")
    if not mbkey:
        try:
            mbkey = json.load(open(CRED_CACHE)).get("METABASE_API_KEY")
        except Exception:
            mbkey = None
    if mbkey:
        AUTH = {"x-api-key": mbkey}
    else:
        tok = req(url + "/api/session", "POST", {"username": email, "password": pw}, {"Content-Type": "application/json"})["id"]
        AUTH = {"X-Metabase-Session": tok}
    H = {"Content-Type": "application/json", **AUTH}

    rows = req(f"{url}/api/card/{CARD}/query/json", "POST", {}, H)
    print(f"[creative-cost] card {CARD}: {len(rows)} raw rows")

    out_rows, skipped = [], 0
    for r in rows:
        team = (r.get("team") or "").strip()
        code = TEAM_CODE.get(team)
        if not code:
            skipped += 1
            continue  # only the four tracked teams feed this view
        d = str(r.get("creative_date") or "")[:10]
        if not d:
            skipped += 1
            continue
        vc = num(r.get("ad_video_generation_cost_usd")) + num(r.get("ad_video_generation_v2_cost_usd"))
        vn = int(num(r.get("ad_video_generation_count"))) + int(num(r.get("ad_video_generation_v2_count")))
        out_rows.append({
            "u": r.get("user_name") or "(unknown)",
            "t": code,
            "d": d,
            "c": round(num(r.get("total_cost_usd")), 6),   # creative cost = all creative types
            "v": round(vc, 6),                             # video cost  = ad_video_generation v1 + v2
            "n": int(num(r.get("total_creative_count"))),
            "vn": vn,
        })

    out_rows.sort(key=lambda x: (x["d"], x["t"], x["u"]))
    dates = [r["d"] for r in out_rows]
    out = {
        "generatedAt": datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "card": CARD,
        "teams": [
            {"id": "coregc", "label": "Core GC"},
            {"id": "1k5k", "label": "1K-5K"},
            {"id": "campaign", "label": "Campaign Creation Team"},
            {"id": "video", "label": "Video Creation Team"},
        ],
        "dateRange": {"min": min(dates) if dates else "", "max": max(dates) if dates else ""},
        "rows": out_rows,
    }
    # serialize fully in memory first so a failure can't truncate the file
    blob = json.dumps(out, separators=(",", ":"))
    with open(OUT, "w") as f:
        f.write(blob)
    tot = sum(r["c"] for r in out_rows)
    vtot = sum(r["v"] for r in out_rows)
    print(f"[creative-cost] {len(out_rows)} team rows ({skipped} untagged skipped) · "
          f"${tot:,.2f} creative / ${vtot:,.2f} video · {out['dateRange']['min']}..{out['dateRange']['max']} -> {OUT}")


if __name__ == "__main__":
    main()
