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
    with urllib.request.urlopen(r, timeout=300) as resp:
        return json.loads(resp.read().decode())


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

    out = {
        'generatedAt': datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ'),
        'spendThreshold': 3540,
        'daysThreshold': 7,
        'goodSellerGLs': ['Aitesam Khan', 'Davidson Udayakumar'],
        'sellers': sellers,
        'hitsMap': hits_map,
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
