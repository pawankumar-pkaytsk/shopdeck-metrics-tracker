#!/usr/bin/env python3
"""Build bucket_data.json for Central Reports -> Bucket Summary.

Source: Metabase card 1880 (weekly PNL per seller, whole company). We keep the 3
most recent weeks (w20 = latest, w19, w18) and, per seller, store the PNL% and
marketing spend (w/ tax) for each. Bucket rules are evaluated client-side.

Fields: seller_id, year_week, marketing_spend_tax_ (spend gate >3540), profit/loss_%2 (PNL %).

Run: cd ~/shopdeck-metrics-site && python3 ~/metabase-arr-refresh/bucket_refresh.py --push
"""
import json, os, sys, subprocess, urllib.request, datetime

CARD = 1880
REPO = os.path.expanduser(os.environ.get("REPO_DIR", "~/shopdeck-metrics-site"))
OUT  = os.path.join(REPO, "bucket_data.json")
CRED_CACHE = os.path.expanduser("~/metabase-arr-refresh/.mbcreds")
DESKTOP_CFG = os.path.expanduser("~/Library/Application Support/Claude/claude_desktop_config.json")


def creds():
    if os.environ.get('METABASE_URL'):  # CI / env-driven
        return os.environ['METABASE_URL'].rstrip('/'), os.environ['METABASE_USER_EMAIL'], os.environ['METABASE_PASSWORD']
    e = json.load(open(CRED_CACHE)) if os.path.exists(CRED_CACHE) else json.load(open(DESKTOP_CFG))['mcpServers']['metabase']['env']
    return e['METABASE_URL'].rstrip('/'), e['METABASE_USER_EMAIL'], e['METABASE_PASSWORD']


def req(url, method='GET', body=None, H=None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, method=method, headers=H or {})
    with urllib.request.urlopen(r, timeout=600) as resp:
        return json.loads(resp.read().decode())


def fnum(v):
    try:
        return round(float(v), 2)
    except (TypeError, ValueError):
        return None


def main():
    url, email, pw = creds()
    tok = req(url + "/api/session", 'POST', {"username": email, "password": pw}, {'Content-Type': 'application/json'})['id']
    H = {'Content-Type': 'application/json', 'X-Metabase-Session': tok}
    rows = req(f"{url}/api/card/{CARD}/query/json", 'POST', {}, H)
    print(f"[bucket] card {CARD}: {len(rows)} weekly rows")

    weeks = sorted({str(r.get('year_week')) for r in rows if r.get('year_week') is not None}, reverse=True)[:3]
    print(f"[bucket] weeks (w20,w19,w18): {weeks}")
    widx = {w: i for i, w in enumerate(weeks)}  # 0=w20,1=w19,2=w18

    sellers = {}
    for r in rows:
        wk = str(r.get('year_week'))
        if wk not in widx:
            continue
        sid = str(r.get('seller_id') or '').strip()
        if not sid:
            continue
        rec = sellers.setdefault(sid, {'p': [None, None, None], 's': [None, None, None]})
        rec['p'][widx[wk]] = fnum(r.get('profit/loss_%2'))
        rec['s'][widx[wk]] = fnum(r.get('marketing_spend_tax_'))

    out = {
        'generatedAt': datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ'),
        'weeks': weeks,                      # [w20, w19, w18]
        'spendThreshold': 3540,
        'pnlHit': 5, 'pnlSubjective': 3, 'healthFloor': -20,
        'sellers': sellers,
    }
    json.dump(out, open(OUT, 'w'), separators=(',', ':'))
    print(f"[out] {OUT} ({os.path.getsize(OUT)} bytes) · {len(sellers)} sellers across {len(weeks)} weeks")

    if '--push' in sys.argv:
        subprocess.run(['git', '-C', REPO, 'add', 'bucket_data.json'], check=True)
        r = subprocess.run(['git', '-C', REPO, 'commit', '-m', 'Refresh Bucket (PNL) data'], capture_output=True, text=True)
        print(r.stdout.strip() or r.stderr.strip())
        if r.returncode == 0:
            subprocess.run(['git', '-C', REPO, 'push', 'origin', 'main'], check=True); print("[push] deployed")


if __name__ == '__main__':
    main()
