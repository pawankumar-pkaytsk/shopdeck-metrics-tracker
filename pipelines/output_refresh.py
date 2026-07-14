#!/usr/bin/env python3
"""Build arr_data.json for the Output Metrics -> ARR dashboard and (optionally) push
it to the shopdeck-metrics-tracker repo (Netlify auto-deploys).

Source: Metabase card 10469 (day-wise, seller-wise spend + ARR, last 6 months, all
sellers) with columns: date, seller_id, spend_meta, spend_google, spend_overall,
arr_meta, arr_google, arr_overall. Card 10469 has NO name/GM/GC columns, so we:
  - filter to the HITS 1k-5k population (ts_data.json hitsMap),
  - map each seller -> GM/GC via card 7753 (growth_manager_name / growth_consultant_name),
  - take the seller name from hitsMap.
We aggregate per day for: overall (HITS total), per GM, per GC. The dashboard
filters by date range client-side. (Card 10892 was retired — it returned HTTP 400.)

Creds: ~/metabase-arr-refresh/.mbcreds (JSON) if present, else Claude desktop config
mcpServers.metabase.env (cached to .mbcreds on first use).
"""
import json, os, sys, subprocess, urllib.request, datetime

DATA_CARD = 10469   # day-wise seller-wise spend + ARR (Meta/Google/overall), last 6 months
MAP_CARD = 7753     # seller -> GC (growth_consultant_name) / GM (growth_manager_name)
REPO = os.path.expanduser(os.environ.get("REPO_DIR", "~/shopdeck-metrics-site"))
OUT  = os.path.join(REPO, "arr_data.json")
CRED_CACHE = os.path.expanduser("~/metabase-arr-refresh/.mbcreds")
DESKTOP_CFG = os.path.expanduser("~/Library/Application Support/Claude/claude_desktop_config.json")


def creds():
    if os.environ.get('METABASE_URL'):  # CI / env-driven
        return os.environ['METABASE_URL'].rstrip('/'), os.environ['METABASE_USER_EMAIL'], os.environ['METABASE_PASSWORD']
    if os.path.exists(CRED_CACHE):
        e = json.load(open(CRED_CACHE))
    else:
        e = json.load(open(DESKTOP_CFG))['mcpServers']['metabase']['env']
        try:
            json.dump({k: e[k] for k in ('METABASE_URL', 'METABASE_USER_EMAIL', 'METABASE_PASSWORD')},
                      open(CRED_CACHE, 'w'))
            os.chmod(CRED_CACHE, 0o600)
        except Exception:
            pass
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

    # HITS 1k-5k population + seller names (card 10469 has no name column)
    ts_path = os.path.join(REPO, 'ts_data.json')
    hits = (json.load(open(ts_path)).get('hitsMap', {}) if os.path.exists(ts_path) else {})
    sids = set(hits)

    # seller -> GM / GC via card 7753 (authoritative role mapping)
    import re as _re
    clean = lambda v: (_re.sub(r'\s+', ' ', str(v or '').strip())
                       if str(v or '').strip() not in ('', '-') else 'Unassigned')
    smap = {}
    for r in req(f"{url}/api/card/{MAP_CARD}/query/json", 'POST', {}, H):
        sid = str(r.get('seller_id') or '').strip()
        if sid:
            smap[sid] = {'gm': clean(r.get('growth_manager_name')), 'gc': clean(r.get('growth_consultant_name'))}
    print(f"[map] card {MAP_CARD}: {len(smap)} sellers · HITS population {len(sids)}")

    rows = req(f"{url}/api/card/{DATA_CARD}/query/json", 'POST', {}, H)  # export = all rows
    print(f"[data] card {DATA_CARD}: {len(rows)} day-wise rows")

    overall, by_gm, by_gc = {}, {}, {}
    sellers = set()
    seller_rows = []   # raw seller-level rows for drilldowns (short keys to keep file small)
    for r in rows:
        date = str(r.get('date') or '')[:10]
        if not date:
            continue
        sid = str(r.get('seller_id') or '').strip()
        if sid not in sids:   # restrict to the HITS 1k-5k population
            continue
        sellers.add(sid)
        sp = num(r.get('spend_overall'))
        ar = num(r.get('arr_overall'))
        mp = smap.get(sid, {})
        gm = mp.get('gm', 'Unassigned')
        gc = mp.get('gc', 'Unassigned')

        seller_rows.append({'d': date, 'i': sid,
                            'n': str(hits.get(sid, {}).get('n') or ''), 'gm': gm, 'gc': gc,
                            's': round(sp, 2), 'a': round(ar, 2)})

        def add(bucket, key):
            c = bucket.setdefault(key, {}).setdefault(date, {'spend': 0.0, 'arr': 0.0})
            c['spend'] += sp; c['arr'] += ar
        c = overall.setdefault(date, {'spend': 0.0, 'arr': 0.0})
        c['spend'] += sp; c['arr'] += ar
        add(by_gm, gm); add(by_gc, gc)

    def to_series(d):
        return [{'date': dt, 'spend': round(v['spend'], 2), 'arr': round(v['arr'], 2)}
                for dt, v in sorted(d.items())]
    def to_groups(b):
        return {k: to_series(v) for k, v in b.items()}

    dates = sorted(overall.keys())
    out = {
        'generatedAt': datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ'),
        'dateRange': {'min': dates[0] if dates else None, 'max': dates[-1] if dates else None},
        'sellers': len(sellers),
        'overall': to_series(overall),
        'byGM': to_groups(by_gm),
        'byGC': to_groups(by_gc),
        'gms': sorted(by_gm.keys()),
        'gcs': sorted(by_gc.keys()),
        'rows': seller_rows,
    }
    json.dump(out, open(OUT, 'w'), separators=(',', ':'))
    print(f"[out] {OUT} ({os.path.getsize(OUT)} bytes) · {len(dates)} days "
          f"· {len(out['gms'])} GMs · {len(out['gcs'])} GCs · {len(sellers)} sellers")

    if '--push' in sys.argv:
        subprocess.run(['git', '-C', REPO, 'add', 'arr_data.json'], check=True)
        r = subprocess.run(['git', '-C', REPO, 'commit', '-m', 'Refresh ARR/Spend data'],
                           capture_output=True, text=True)
        print(r.stdout.strip() or r.stderr.strip())
        if r.returncode == 0:
            subprocess.run(['git', '-C', REPO, 'push', 'origin', 'main'], check=True)
            print("[push] deployed")
        else:
            print("[push] nothing to commit (data unchanged)")


if __name__ == '__main__':
    main()
