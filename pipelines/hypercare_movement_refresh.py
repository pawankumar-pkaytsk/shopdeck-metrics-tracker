#!/usr/bin/env python3
"""Build hypercare_movement_data.json from card 9353 ("seller revival flow") — day-on-day
and week-on-week movement of the three revival categories (Self-Serve / Churn / Hypercare).

Category is parsed from the `title` column ("Revive Seller [<Category> Case]", also the
"Postponed - ..." variants). Movement date = `created_at`; ISO year-week tag = `year_week`.
Rows are de-duplicated by `id` (the card can emit duplicate task rows). We also validate that
the ISO week of `created_at` equals the `year_week` tag and record the match rate.

Run: cd ~/shopdeck-metrics-site && python3 pipelines/hypercare_movement_refresh.py
"""
import json, os, re, datetime, urllib.request

CARD = 9353
CHANGELOG_CARD = 10992   # GC/GM/KAM assignment changelog (seller_id, assignee role, name, start/end)
REPO = os.path.expanduser(os.environ.get("REPO_DIR", "~/shopdeck-metrics-site"))
OUT = os.path.join(REPO, "hypercare_movement_data.json")
CRED_CACHE = os.path.expanduser("~/metabase-arr-refresh/.mbcreds")
DESKTOP_CFG = os.path.expanduser("~/Library/Application Support/Claude/claude_desktop_config.json")

CATS = ["Self-Serve", "Churn", "Hypercare"]
KEY = {"Self-Serve": "selfServe", "Churn": "churn", "Hypercare": "hypercare"}
_CAT_RE = re.compile(r"\[(Self-Serve|Churn|Hypercare) Case\]")


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


def _cat(title):
    m = _CAT_RE.search(title or "")
    return m.group(1) if m else None


def _iso_yw(ds):
    d = datetime.datetime.fromisoformat(str(ds)[:19])
    y, w, _ = d.isocalendar()
    return y * 100 + w


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

    rows_in = req(f"{url}/api/card/{CARD}/query/json", "POST", {}, H)

    # de-dup by task id
    uniq = {}
    for r in rows_in:
        uniq[r.get("id")] = r
    rows = list(uniq.values())

    # ISO-week vs year_week validation
    iso_total = iso_match = 0
    day_acc = {}   # 'YYYY-MM-DD' -> {cat: n}
    week_acc = {}  # year_week int -> {cat: n}
    for r in rows:
        cat = _cat(r.get("title"))
        ca = r.get("created_at")
        yw = r.get("year_week")
        if not cat or not ca:
            continue
        d = str(ca)[:10]
        day_acc.setdefault(d, {}).setdefault(cat, 0)
        day_acc[d][cat] += 1
        if yw is not None:
            iso_total += 1
            try:
                if _iso_yw(ca) == int(yw):
                    iso_match += 1
            except Exception:
                pass
            wk = int(yw)
            week_acc.setdefault(wk, {}).setdefault(cat, 0)
            week_acc[wk][cat] += 1

    def _emit(acc, kfield, kval):
        out = []
        for k in sorted(acc):
            c = acc[k]
            row = {kfield: kval(k)}
            tot = 0
            for cat in CATS:
                n = c.get(cat, 0)
                row[KEY[cat]] = n
                tot += n
            row["total"] = tot
            out.append(row)
        return out

    day_rows = _emit(day_acc, "d", lambda k: k)
    week_rows = _emit(week_acc, "yw", lambda k: str(k))

    # --- Yesterday movement with PREVIOUS GC/GM mapping (card 10992 changelog) ---
    # "yesterday" = the calendar day before the refresh runs (created_at is naive IST).
    yday = (datetime.datetime.utcnow() - datetime.timedelta(days=1)).strftime("%Y-%m-%d")
    y_rows = [r for r in rows if str(r.get("created_at"))[:10] == yday and _cat(r.get("title"))]

    # Mapping = the GC/GM the seller had BEFORE they were moved into the revival/hypercare team.
    # Card 7753 (current mapping) collapses everyone onto the hypercare GM post-move, so instead we
    # read the assignment changelog (card 10992): for each revival task, find the GM interval active
    # at created_at (the revival GM), take its start_date S as the move boundary, and report the GC
    # and GM whose intervals ended at/just before S — i.e. the assignment prior to the unassignment.
    def _dt(d):
        try: return datetime.datetime.fromisoformat(str(d)[:19])
        except Exception: return None

    chlog = {}   # seller_id -> list of records
    curmap = {}  # seller_id -> {gc, gm} from current mapping (card 7753), used as fallback
    if y_rows:
        for cr in req(f"{url}/api/card/{CHANGELOG_CARD}/query/json", "POST", {}, H):
            chlog.setdefault(str(cr.get("seller_id") or ""), []).append(cr)
        _clean = lambda v: (str(v).strip() if v not in (None, "", "-") else None)
        for mr in req(f"{url}/api/card/7753/query/json", "POST", {}, H):
            sid = str(mr.get("seller_id") or "")
            if sid:
                curmap[sid] = {"gc": _clean(mr.get("growth_consultant_name")),
                               "gm": _clean(mr.get("growth_manager_name"))}

    def _prev_gc_gm(sid, ca):
        recs = chlog.get(sid, [])
        gms = [x for x in recs if x.get("assignee") == "GM" and _dt(x.get("start_date"))]
        act = [x for x in gms if _dt(x["start_date"]) <= ca and (not x.get("end_date") or _dt(x["end_date"]) >= ca)]
        if not act:
            return None, None
        S = _dt(act[-1]["start_date"])   # boundary: when the seller entered the current (revival) GM

        def prev(role):
            cand = [x for x in recs if x.get("assignee") == role and _dt(x.get("end_date")) and _dt(x["end_date"]) <= S]
            cand.sort(key=lambda x: _dt(x["end_date"]))
            nm = str(cand[-1].get("name")).strip() if cand else ""
            return nm or None
        return prev("GC"), prev("GM")

    UNK = "Unknown"
    gm_acc, gcgm_acc = {}, {}
    y_unmapped = 0
    for r in y_rows:
        cat = _cat(r.get("title"))
        sid = str(r.get("seller_id") or "")
        gc, gm = _prev_gc_gm(sid, _dt(r.get("created_at")))
        # Fallback for sellers absent from the changelog (e.g. assigned after the 1-Jan dump):
        # use their current mapping (card 7753) so a real GM/GC still shows instead of "Unknown".
        cur = curmap.get(sid) or {}
        gm = gm or cur.get("gm") or UNK
        gc = gc or cur.get("gc") or UNK
        if gm == UNK:
            y_unmapped += 1
        gm_acc.setdefault(gm, {}).setdefault(cat, 0)
        gm_acc[gm][cat] += 1
        gcgm_acc.setdefault((gm, gc), {}).setdefault(cat, 0)
        gcgm_acc[(gm, gc)][cat] += 1

    def _row(counts):
        row, tot = {}, 0
        for cat in CATS:
            n = counts.get(cat, 0); row[KEY[cat]] = n; tot += n
        row["total"] = tot
        return row

    y_gm = []
    for gm in sorted(gm_acc, key=lambda g: (g == UNK, -sum(gm_acc[g].values()))):
        y_gm.append({"gm": gm, **_row(gm_acc[gm])})
    y_gcgm = []
    for (gm, gc) in sorted(gcgm_acc, key=lambda t: (t[0] == UNK, t[0], -sum(gcgm_acc[t].values()))):
        y_gcgm.append({"gm": gm, "gc": gc, **_row(gcgm_acc[(gm, gc)])})

    yesterday = {
        "date": yday,
        "gm": y_gm,
        "gcgm": y_gcgm,
        "totals": _row({cat: sum(v.get(cat, 0) for v in gm_acc.values()) for cat in CATS}),
        "unmapped": y_unmapped,
        "tasks": len(y_rows),
    }
    print(f"[hypercare-mvmt] yesterday {yday}: {len(y_rows)} tasks · {len(y_gm)} GMs · {y_unmapped} unmapped")

    out = {
        "generatedAt": datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "card": CARD,
        "cats": CATS,
        "isoCheck": {"matched": iso_match, "total": iso_total,
                     "pct": round(iso_match / iso_total * 100, 2) if iso_total else None},
        "day": day_rows,
        "week": week_rows,
        "yesterday": yesterday,
        "dayRange": {"min": day_rows[0]["d"] if day_rows else None, "max": day_rows[-1]["d"] if day_rows else None},
    }
    json.dump(out, open(OUT, "w"), separators=(",", ":"))
    print(f"[hypercare-mvmt] card {CARD}: {len(rows)} uniq tasks (of {len(rows_in)}), "
          f"{len(day_rows)} days, {len(week_rows)} weeks · ISO match {iso_match}/{iso_total} -> {OUT}")


if __name__ == "__main__":
    main()
