#!/usr/bin/env python3
"""Build ts_data.json for Input Metrics -> Troubleshoot Compliance.

Source: Metabase card 10189 (seller-wise last T/S details), all sellers. The
seller->GC/GM/CL/Status mapping is read live in the browser from the Daily Plan
sheet, so the pipeline only needs the Metabase T/S facts keyed by seller_id.

Columns in 10189: seller_id, seller_name, total_ts_done, last_ts_date, ts_type,
last_ts_actions, last_7_days__meta_spend, last_7_days__meta_spend_w_tax.

Run:  cd ~/shopdeck-metrics-site && python3 ~/metabase-arr-refresh/ts_refresh.py --push
Creds: ~/metabase-arr-refresh/.mbcreds (JSON) or Claude desktop config.
"""
import json, os, sys, subprocess, urllib.request, datetime

CARD = 10189
REPO = os.path.expanduser(os.environ.get("REPO_DIR", "~/shopdeck-metrics-site"))
OUT  = os.path.join(REPO, "ts_data.json")
CRED_CACHE = os.path.expanduser("~/metabase-arr-refresh/.mbcreds")
DESKTOP_CFG = os.path.expanduser("~/Library/Application Support/Claude/claude_desktop_config.json")


def creds():
    if os.environ.get('METABASE_URL'):  # CI / env-driven
        return os.environ['METABASE_URL'].rstrip('/'), os.environ['METABASE_USER_EMAIL'], os.environ['METABASE_PASSWORD']
    if os.path.exists(CRED_CACHE):
        e = json.load(open(CRED_CACHE))
    else:
        e = json.load(open(DESKTOP_CFG))['mcpServers']['metabase']['env']
    return e['METABASE_URL'].rstrip('/'), e['METABASE_USER_EMAIL'], e['METABASE_PASSWORD']


def req(url, method='GET', body=None, H=None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, method=method, headers=H or {})
    import time as _t
    last = None
    for _attempt in range(4):
        try:
            with urllib.request.urlopen(r, timeout=600) as resp:
                return json.loads(resp.read().decode())
        except Exception as _e:
            last = _e
            _t.sleep(3 * (_attempt + 1))
    raise last


def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def main():
    url, email, pw = creds()
    tok = req(url + "/api/session", 'POST', {"username": email, "password": pw},
              {'Content-Type': 'application/json'})['id']
    H = {'Content-Type': 'application/json', 'X-Metabase-Session': tok}

    rows = req(f"{url}/api/card/{CARD}/query/json", 'POST', {}, H)
    print(f"[ts] card {CARD}: {len(rows)} rows")

    sellers = {}
    for r in rows:
        sid = str(r.get('seller_id') or '').strip()
        if not sid:
            continue
        sellers[sid] = {
            'n':  str(r.get('seller_name') or ''),
            't':  r.get('total_ts_done'),
            'd':  (str(r.get('last_ts_date') or '')[:10]),
            'ty': str(r.get('ts_type') or ''),
            'a':  str(r.get('last_ts_actions') or ''),
            's7': round(num(r.get('last_7_days__meta_spend_w_tax')), 2),
        }

    # --- Complete last-7d META spend (with tax) for ALL sellers ---
    # Card 10189's base is built only from sellers that already have a T/S action, so high-spend
    # sellers that were NEVER troubleshot are absent entirely → last7=0 → never "Eligible".
    # Merge the full spend (same definition as 10189) so every spending seller is covered.
    try:
        import urllib.parse
        spend_sql = ("SELECT seller_id, ROUND(SUM(spend)*1.18,2) AS s7 "
                     "FROM `blitzscale-prod-project.fb_marketings.fb_marketing_insights` "
                     "WHERE breakdown_key is NULL "
                     "AND TIMESTAMP_TRUNC(spend_date, DAY) >= TIMESTAMP(DATE_SUB(CURRENT_DATE('Asia/Kolkata'), INTERVAL 7 DAY)) "
                     "AND TIMESTAMP_TRUNC(spend_date, DAY) < TIMESTAMP(CURRENT_DATE('Asia/Kolkata')) "
                     "GROUP BY seller_id")
        body = urllib.parse.urlencode({"query": json.dumps({"database": 6, "type": "native", "native": {"query": spend_sql}})}).encode()
        spreq = urllib.request.Request(url + "/api/dataset/json", data=body, method='POST',
                                       headers={'X-Metabase-Session': tok, 'Content-Type': 'application/x-www-form-urlencoded'})
        spend_rows = json.loads(urllib.request.urlopen(spreq, timeout=300).read())
        added = 0
        for r in spend_rows:
            sid = str(r.get('seller_id') or '').strip()
            if not sid:
                continue
            s7 = round(num(r.get('s7')), 2)
            if sid in sellers:
                sellers[sid]['s7'] = s7  # same source/definition — keeps TS fields, refreshes spend
            else:
                sellers[sid] = {'n': '', 't': None, 'd': '', 'ty': '', 'a': '', 's7': s7}
                added += 1
        print(f"[spend] merged 7d meta spend for {len(spend_rows)} sellers · {added} added (never-troubleshot spenders)")
    except Exception as _e:
        print('[spend] full-spend merge failed (keeping 10189 only):', _e)

    # HITS seller -> GL(=GC)/GM/name mapping from card 10892 (same filtered universe as ARR).
    # Used by the 1K-5K and Good Seller teams in Troubleshoot Compliance.
    hits = req(f"{url}/api/card/10892/query/json", 'POST', {}, H)
    hits_map = {}
    for r in hits:
        sid = str(r.get('seller_id') or '').strip()
        if not sid:
            continue
        hits_map[sid] = {
            'n':  str(r.get('seller_name') or ''),
            'gc': str(r.get('gc') or '').strip(),   # GL name (col F of 5K sheet)
            'gm': str(r.get('gm') or '').strip(),   # GM name (col G)
        }
    print(f"[hits] card 10892: {len(hits_map)} unique sellers mapped (GL/GM)")

    # best PnL visibility (card 11011): seller -> best_source, best_w1_pnl_value, w1_spend
    pnl = {}
    for r in req(f"{url}/api/card/11011/query/json", 'POST', {}, H):
        sid = str(r.get('seller_id') or '').strip()
        if not sid:
            continue
        pnl[sid] = {
            'src': str(r.get('best_source') or ''),
            'pnl': r.get('best_w1_pnl_value'),
            'w1s': round(num(r.get('w1_spend')), 2) if r.get('w1_spend') is not None else None,
        }
    print(f"[pnl] card 11011: {len(pnl)} sellers")

    out = {
        'generatedAt': datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ'),
        'spendThreshold': 3540,
        'daysThreshold': 7,
        'goodSellerGLs': ['Aitesam Khan', 'Davidson Udayakumar'],
        'sellers': sellers,
        'hitsMap': hits_map,
        'pnl': pnl,
    }
    json.dump(out, open(OUT, 'w'), separators=(',', ':'))
    print(f"[out] {OUT} ({os.path.getsize(OUT)} bytes) · {len(sellers)} sellers")

    if '--push' in sys.argv:
        subprocess.run(['git', '-C', REPO, 'add', 'ts_data.json'], check=True)
        r = subprocess.run(['git', '-C', REPO, 'commit', '-m', 'Refresh Troubleshoot (T/S) data'],
                           capture_output=True, text=True)
        print(r.stdout.strip() or r.stderr.strip())
        if r.returncode == 0:
            subprocess.run(['git', '-C', REPO, 'push', 'origin', 'main'], check=True)
            print("[push] deployed")


if __name__ == '__main__':
    main()
