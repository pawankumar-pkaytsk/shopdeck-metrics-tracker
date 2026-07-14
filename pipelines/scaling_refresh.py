#!/usr/bin/env python3
"""Build scaling_data.json for Central Reports -> 1k-5k Team allocation (Meta + Google).

Sources:
  - card 2787 (sellerwise yesterday/today/lifetime spend): meta yesterday_spend
  - card 7401 (seller google last1/3/7 day spend): google_ad_account_id + google yesterday_spend
Keyed by seller_id. Meta spending = meta yesterday > 1; Google live = has google_ad_account_id,
Google spending = google yesterday > 50.

Run: cd ~/shopdeck-metrics-site && python3 ~/metabase-arr-refresh/scaling_refresh.py --push
"""
import json, os, re, sys, subprocess, urllib.request, datetime

META_CARD = 2787
GOOGLE_CARD = 7401
REPO = os.path.expanduser(os.environ.get("REPO_DIR", "~/shopdeck-metrics-site"))
OUT  = os.path.join(REPO, "scaling_data.json")
CRED_CACHE = os.path.expanduser("~/metabase-arr-refresh/.mbcreds")
DESKTOP_CFG = os.path.expanduser("~/Library/Application Support/Claude/claude_desktop_config.json")
HEX24 = re.compile(r'^[0-9a-f]{24}$')


def creds():
    if os.environ.get('METABASE_URL'):  # CI / env-driven
        return os.environ['METABASE_URL'].rstrip('/'), os.environ['METABASE_USER_EMAIL'], os.environ['METABASE_PASSWORD']
    e = json.load(open(CRED_CACHE)) if os.path.exists(CRED_CACHE) else json.load(open(DESKTOP_CFG))['mcpServers']['metabase']['env']
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


def fnum(v):
    try:
        return round(float(v), 2)
    except (TypeError, ValueError):
        return 0.0


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

    meta = req(f"{url}/api/card/{META_CARD}/query/json", 'POST', {}, H)
    google = req(f"{url}/api/card/{GOOGLE_CARD}/query/json", 'POST', {}, H)
    gts = req(f"{url}/api/card/10976/query/json", 'POST', {}, H)  # seller-wise google T/S details
    print(f"[scaling] meta(2787)={len(meta)} · google(7401)={len(google)} · google_ts(10976)={len(gts)} rows")

    g7 = {}  # seller -> google last-7-day spend
    sellers = {}
    for r in meta:
        sid = str(r.get('seller_id') or '').strip()
        if not HEX24.match(sid):
            continue
        sellers.setdefault(sid, {})['my'] = fnum(r.get('yesterday_spend'))  # meta yesterday spend
    for r in google:
        sid = str(r.get('seller_id') or '').strip()
        if not HEX24.match(sid):
            continue
        rec = sellers.setdefault(sid, {})
        acct = r.get('google_ad_account_id')
        rec['ga'] = str(acct).strip() if acct not in (None, '') else ''
        rec['gy'] = fnum(r.get('yesterday_spend'))  # google yesterday spend
        rec['gt'] = fnum(r.get('total_marketing_spend_with_tax'))  # google total marketing spend (w/tax)

    # Google last-7-day spend (for Google T/S eligibility) from card 7669 col 'google_spend_last7day'
    for r in req(f"{url}/api/card/7669/query/json", 'POST', {}, H):
        sid = str(r.get('seller_id') or '').strip()
        if HEX24.match(sid):
            g7[sid] = fnum(r.get('google_spend_last7day'))

    out = {
        'generatedAt': datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ'),
        'metaSpendGate': 1, 'googleSpendGate': 50,
        'sellers': sellers,
    }
    json.dump(out, open(OUT, 'w'), separators=(',', ':'))
    print(f"[out] {OUT} ({os.path.getsize(OUT)} bytes) · {len(sellers)} sellers")

    # google T/S compliance source: per seller google last-7d spend (s7) + last google T/S facts
    gsell = {}
    for sid, s7 in g7.items():
        gsell.setdefault(sid, {})['s7'] = s7
    for r in gts:
        sid = str(r.get('seller_id') or '').strip()
        if not HEX24.match(sid):
            continue
        rec = gsell.setdefault(sid, {})
        rec['t'] = r.get('total_ts_done')
        rec['d'] = str(r.get('last_ts_done') or '')[:10]
        rec['ty'] = str(r.get('ts_type') or '')
        rec['a'] = str(r.get('last_ts_actions') or '')
    gout = {'generatedAt': out['generatedAt'], 'spendThreshold': 3540, 'daysThreshold': 7, 'sellers': gsell}
    GOUT = os.path.join(REPO, 'google_ts_data.json')
    json.dump(gout, open(GOUT, 'w'), separators=(',', ':'))
    print(f"[out] {GOUT} ({os.path.getsize(GOUT)} bytes) · {len(gsell)} sellers · {len(gts)} with google T/S")

    if '--push' in sys.argv:
        subprocess.run(['git', '-C', REPO, 'add', 'scaling_data.json'], check=True)
        r = subprocess.run(['git', '-C', REPO, 'commit', '-m', 'Refresh 1k-5k (meta/google spend) data'], capture_output=True, text=True)
        print(r.stdout.strip() or r.stderr.strip())
        if r.returncode == 0:
            subprocess.run(['git', '-C', REPO, 'push', 'origin', 'main'], check=True); print("[push] deployed")


if __name__ == '__main__':
    main()
