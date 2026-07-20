#!/usr/bin/env python3
"""Build revival_data.json from card 11911 ("Revived Seller Log / Hypercare-Kickstarter
Slack Responses"). Powers All Reports #7 — Yesterday Revival Numbers, current running
week, and ISO-week-wise (last 12 weeks) revival counts by Revival POC (= submitted_by),
plus an ISO-week-wise total for the graph.

Columns: seller_id, seller_name, funds_added_amount_in_rupees, submitted_by, timestamp.
Revival POC = submitted_by. Revival date = timestamp (text; parsed defensively).

Card 11911 lives on a separate BigQuery project (db 23, 1 TB/day quota). On quota/rate
failure the previous revival_data.json is preserved so the report isn't wiped.

Run: cd ~/shopdeck-metrics-site && python3 pipelines/revival_refresh.py
"""
import json, os, re, datetime, urllib.request

CARD = 11911
REPO = os.path.expanduser(os.environ.get("REPO_DIR", "~/shopdeck-metrics-site"))
OUT = os.path.join(REPO, "revival_data.json")
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


_ISO_FMTS = ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d",
             "%d/%m/%Y %H:%M:%S", "%d/%m/%Y", "%m/%d/%Y %H:%M:%S", "%m/%d/%Y",
             "%d-%m-%Y", "%b %d, %Y", "%d %b %Y", "%Y/%m/%d")


def parse_date(v):
    """Best-effort parse of the free-text timestamp column into a date."""
    s = str(v or "").strip()
    if not s:
        return None
    # epoch seconds / millis (Slack ts can be like 1710...)
    if re.fullmatch(r"\d{13}", s):
        try: return datetime.datetime.utcfromtimestamp(int(s) / 1000).date()
        except (ValueError, OSError): pass
    if re.fullmatch(r"\d{10}(\.\d+)?", s):
        try: return datetime.datetime.utcfromtimestamp(float(s)).date()
        except (ValueError, OSError): pass
    # ISO fast path
    try:
        return datetime.date.fromisoformat(s[:10])
    except ValueError:
        pass
    for fmt in _ISO_FMTS:
        try:
            return datetime.datetime.strptime(s[:len(datetime.datetime.now().strftime(fmt)) + 4], fmt).date()
        except (ValueError, TypeError):
            continue
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        try: return datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError: pass
    return None


def _num(v):
    try:
        return float(re.sub(r"[^\d.\-]", "", str(v))) if str(v).strip() else 0.0
    except (ValueError, TypeError):
        return 0.0


def load_prev():
    try:
        return json.load(open(OUT))
    except Exception:
        return None


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

    try:
        raw = req(f"{url}/api/card/{CARD}/query/json", "POST", {}, H)
    except Exception as e:
        prev = load_prev()
        if prev:
            print(f"[revival] card {CARD} fetch failed ({str(e)[:80]}); preserving previous revival_data.json")
            return
        # no prior data: write an empty-but-valid file so the UI shows a graceful message
        raw = []
        print(f"[revival] card {CARD} fetch failed ({str(e)[:80]}); writing empty revival_data.json")

    rows, unparsed = [], 0
    dbg = []
    for r in raw:
        ts = r.get("timestamp")
        d = parse_date(ts)
        if len(dbg) < 5:
            dbg.append((str(ts)[:30], d.isoformat() if d else None))
        if not d:
            unparsed += 1
            continue
        iso = d.isocalendar()
        rows.append({
            "s": str(r.get("seller_id") or ""), "n": str(r.get("seller_name") or ""),
            "poc": (str(r.get("submitted_by") or "").strip() or "Unknown"),
            "d": d.isoformat(), "yw": "%d%02d" % (iso[0], iso[1]),
            "funds": round(_num(r.get("funds_added_amount_in_rupees"))),
        })
    rows.sort(key=lambda x: x["d"], reverse=True)

    today = datetime.date.today()
    ci = today.isocalendar()
    out = {
        "generatedAt": datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "asOf": today.isoformat(),
        "yesterday": (today - datetime.timedelta(days=1)).isoformat(),
        "curYw": "%d%02d" % (ci[0], ci[1]),
        "rows": rows,
        "unparsed": unparsed,
        "tsSamples": dbg,
    }
    json.dump(out, open(OUT, "w"), separators=(",", ":"))
    print(f"[revival] card {CARD}: {len(rows)} revival rows ({unparsed} unparsed ts) · "
          f"{len(set(r['poc'] for r in rows))} POCs · ts samples {dbg} -> {OUT}")


if __name__ == "__main__":
    main()
