#!/usr/bin/env python3
"""Build hit1_data.json for Output Metrics -> HIT 1.

Sources:
  - card 10453 (hits master): every HIT1 record — seller_id, seller_name, hit_month, hit_year
  - card 10454 (3-week golive): sellers that are a 3-week golive -> counted as 1.5 HITs
GC/GM/handover mapping is read live in the browser from the 'handover' sheet.

Run: cd ~/shopdeck-metrics-site && python3 ~/metabase-arr-refresh/hit1_refresh.py --push
"""
import json, os, sys, subprocess, urllib.request, datetime

HIT_CARD = 10453
GOLIVE_CARD = 10454
REPO = os.path.expanduser(os.environ.get("REPO_DIR", "~/shopdeck-metrics-site"))
OUT  = os.path.join(REPO, "hit1_data.json")
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


def main():
    url, email, pw = creds()
    tok = req(url + "/api/session", 'POST', {"username": email, "password": pw}, {'Content-Type': 'application/json'})['id']
    H = {'Content-Type': 'application/json', 'X-Metabase-Session': tok}

    hits = req(f"{url}/api/card/{HIT_CARD}/query/json", 'POST', {}, H)
    golive = req(f"{url}/api/card/{GOLIVE_CARD}/query/json", 'POST', {}, H)
    golive_ids = {str(r.get('seller_id') or '').strip() for r in golive}
    print(f"[hit1] hits(10453)={len(hits)} rows · golive(10454)={len(golive_ids)} sellers")

    rows = []
    for r in hits:
        sid = str(r.get('seller_id') or '').strip()
        hm, hy = r.get('hit_month'), r.get('hit_year')
        if not sid or hm is None or hy is None:
            continue
        rows.append({'id': sid, 'name': str(r.get('seller_name') or ''),
                     'hm': int(hm), 'hy': int(hy), 'g': sid in golive_ids})

    # GM month-wise HIT targets (card 11322, Role='GM') -> gmTargets[hy*100+hm][gm_lower] = {t, n}
    import re as _re
    gm_targets = {}
    try:
        for r in req(f"{url}/api/card/11322/query/json", 'POST', {}, H):
            if str(r.get('Role') or '').strip().upper() != 'GM':
                continue
            nm = _re.sub(r'\s+', ' ', str(r.get('Name') or '').strip())
            hm, hy, tgt = r.get('Target_Month'), r.get('Target_Year'), r.get('HITS_Target')
            if not nm or hm is None or hy is None or tgt is None:
                continue
            key = int(hy) * 100 + int(hm)
            gm_targets.setdefault(str(key), {})[nm.lower()] = {'t': float(tgt), 'n': nm}
        print(f"[hit1] GM targets (card 11322): {sum(len(v) for v in gm_targets.values())} entries across {len(gm_targets)} months")
    except Exception as _e:
        print('[hit1] GM target fetch failed:', _e)

    out = {'generatedAt': datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ'), 'rows': rows, 'gmTargets': gm_targets}
    json.dump(out, open(OUT, 'w'), separators=(',', ':'))
    print(f"[out] {OUT} ({os.path.getsize(OUT)} bytes) · {len(rows)} HIT1 records · {sum(1 for x in rows if x['g'])} are 3-week golive")

    if '--push' in sys.argv:
        subprocess.run(['git', '-C', REPO, 'add', 'hit1_data.json'], check=True)
        r = subprocess.run(['git', '-C', REPO, 'commit', '-m', 'Refresh HIT1 data'], capture_output=True, text=True)
        print(r.stdout.strip() or r.stderr.strip())
        if r.returncode == 0:
            subprocess.run(['git', '-C', REPO, 'push', 'origin', 'main'], check=True); print("[push] deployed")


if __name__ == '__main__':
    main()
