#!/usr/bin/env python3
"""Build sharded per-seller call records (calls/<4-char-prefix>.json) from card 9688
(HITS call dump). Powers the 'Seller past call records' section in Show-any-seller-details.

Sharded by the first 4 chars of seller_id so each on-demand fetch stays small (<1 MB).
Pure-noise calls (0 duration, no summary, no recording) are dropped; summaries trimmed;
capped to 40 most-recent calls per seller.

Run: cd ~/shopdeck-metrics-site && python3 pipelines/calls_refresh.py
"""
import json, os, sys, urllib.request, glob, datetime
from collections import defaultdict

CALL_CARD = 9688
PREFIX = 4
CAP = 40
SUMMARY_MAX = 500
REPO = os.path.expanduser(os.environ.get("REPO_DIR", "~/shopdeck-metrics-site"))
OUT_DIR = os.path.join(REPO, "calls")
CRED_CACHE = os.path.expanduser("~/metabase-arr-refresh/.mbcreds")
DESKTOP_CFG = os.path.expanduser("~/Library/Application Support/Claude/claude_desktop_config.json")


def creds():
    if os.environ.get("METABASE_URL"):
        return os.environ["METABASE_URL"].rstrip("/"), os.environ["METABASE_USER_EMAIL"], os.environ["METABASE_PASSWORD"]
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


def main():
    url, email, pw = creds()
    _mbkey = os.environ.get('METABASE_API_KEY')
    if not _mbkey:
        try:
            _mbkey = json.load(open(os.path.expanduser('~/metabase-arr-refresh/.mbcreds'))).get('METABASE_API_KEY')
        except Exception:
            _mbkey = None
    if _mbkey:
        AUTH = {'x-api-key': _mbkey}
    else:
        tok = req(url + "/api/session", 'POST', {"username": email, "password": pw}, {'Content-Type': 'application/json'})['id']
        AUTH = {'X-Metabase-Session': tok}
    H = {'Content-Type': 'application/json', **AUTH}

    rows = req(f"{url}/api/card/{CALL_CARD}/query/json", "POST", {}, H)
    by = defaultdict(list)
    for x in rows:
        sid = str(x.get("seller_id") or "").strip()
        if not sid:
            continue
        dur = int(x.get("duration") or 0)
        summ = (x.get("summary") or "").strip()
        rec = (x.get("recording_url") or "").strip()
        if dur <= 0 and not summ and not rec:
            continue  # drop pure-noise hangups
        by[sid].append([str(x.get("created_at") or "")[:19], x.get("caller_name") or x.get("call_from") or "", dur, summ[:SUMMARY_MAX], rec])

    os.makedirs(OUT_DIR, exist_ok=True)
    for f in glob.glob(os.path.join(OUT_DIR, "*.json")):
        os.remove(f)
    gen = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    shard = defaultdict(dict)
    for sid, lst in by.items():
        lst.sort(key=lambda c: c[0], reverse=True)
        shard[sid[:PREFIX]][sid] = lst[:CAP]
    total = 0
    for pre, mp in shard.items():
        p = os.path.join(OUT_DIR, pre + ".json")
        json.dump({"generatedAt": gen, "calls": mp}, open(p, "w"), separators=(",", ":"))
        total += os.path.getsize(p)
    print(f"[calls] card {CALL_CARD}: {len(rows)} rows -> {len(by)} sellers, {len(shard)} shards, {round(total/1e6,1)} MB")


if __name__ == "__main__":
    main()
