#!/usr/bin/env python3
"""Build hit2_data.json for Output Metrics -> HIT 2.

Source: card 10453 (hits master incentive automation) — rows where column hit2 == 1.
Each such row is a HIT2 achievement, dated by hit2_month / hit2_year.
GC/GM mapping is read live in the browser from the 'handover' sheet (same as HIT 1).

Run: cd ~/shopdeck-metrics-site && python3 ~/metabase-arr-refresh/hit2_refresh.py --push
"""
import json, os, sys, subprocess, urllib.request, datetime

HIT_CARD = 10453
REPO = os.path.expanduser(os.environ.get("REPO_DIR", "~/shopdeck-metrics-site"))
OUT = os.path.join(REPO, "hit2_data.json")
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


def is_one(v):
    return str(v).strip() in ('1', '1.0', 'True', 'true')


def main():
    url, email, pw = creds()
    tok = req(url + "/api/session", 'POST', {"username": email, "password": pw}, {'Content-Type': 'application/json'})['id']
    H = {'Content-Type': 'application/json', 'X-Metabase-Session': tok}

    hits = req(f"{url}/api/card/{HIT_CARD}/query/json", 'POST', {}, H)
    print(f"[hit2] hits(10453)={len(hits)} rows")

    rows = []
    for r in hits:
        if not is_one(r.get('hit2')):
            continue
        sid = str(r.get('seller_id') or '').strip()
        hm, hy = r.get('hit2_month'), r.get('hit2_year')
        if not sid or hm is None or hy is None:
            continue
        rows.append({'id': sid, 'name': str(r.get('seller_name') or ''),
                     'hm': int(hm), 'hy': int(hy), 'team': str(r.get('team') or '')})

    out = {'generatedAt': datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ'), 'rows': rows}
    json.dump(out, open(OUT, 'w'), separators=(',', ':'))
    from collections import Counter
    print(f"[out] {OUT} ({os.path.getsize(OUT)} bytes) · {len(rows)} HIT2 records")
    print("  by month:", dict(Counter((x['hy'], x['hm']) for x in rows)))

    if '--push' in sys.argv:
        subprocess.run(['git', '-C', REPO, 'add', 'hit2_data.json'], check=True)
        r = subprocess.run(['git', '-C', REPO, 'commit', '-m', 'Refresh HIT2 data'], capture_output=True, text=True)
        print(r.stdout.strip() or r.stderr.strip())
        if r.returncode == 0:
            subprocess.run(['git', '-C', REPO, 'push', 'origin', 'main'], check=True); print("[push] deployed")


if __name__ == '__main__':
    main()
