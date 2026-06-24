#!/usr/bin/env python3
"""Build golive_data.json for Central Reports -> Golive Overview.

Source: Metabase card 7682 (ob-cohort-spend-0). Per seller we keep A2H date and
go-live date. Cohort (M0..M3+) and 'not live yet' (A2H present, golive blank) and
MTD-live (golive in current month) are computed client-side against today.

Run: cd ~/shopdeck-metrics-site && python3 ~/metabase-arr-refresh/golive_refresh.py --push
"""
import json, os, sys, subprocess, urllib.request, datetime

CARD = 7682
REPO = os.path.expanduser(os.environ.get("REPO_DIR", "~/shopdeck-metrics-site"))
OUT  = os.path.join(REPO, "golive_data.json")
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
    rows = req(f"{url}/api/card/{CARD}/query/json", 'POST', {}, H)
    print(f"[golive] card {CARD}: {len(rows)} rows")

    sellers = {}
    for r in rows:
        sid = str(r.get('seller_id') or '').strip()
        if not sid:
            continue
        a2h = str(r.get('a2h_date') or '')[:10]
        gol = str(r.get('go_live_date') or '')[:10]
        # keep the row with an A2H date; if duplicates, prefer one with golive
        prev = sellers.get(sid)
        if prev is None or (not prev.get('g') and gol):
            sellers[sid] = {'a': a2h, 'g': gol}

    # --- Ground-truth summary straight from card 7682 (the full universe) ---
    # go_live_date in 7682 = first day marketing_spend>0, so "live" == actual spend ever.
    # This is owner-independent (does NOT depend on the core assignment sheet), so it
    # reconciles exactly with the Metabase question and is the single source of truth.
    today = datetime.date.today()
    cur_idx = today.year * 12 + (today.month - 1)
    cur_month = today.strftime('%Y-%m')

    def cohort_of(a):
        try:
            d = datetime.date.fromisoformat(a[:10])
        except (ValueError, TypeError):
            return None
        di = cur_idx - (d.year * 12 + (d.month - 1))
        return 'M0' if di <= 0 else 'M1' if di == 1 else 'M2' if di == 2 else 'M3' if di == 3 else 'M3+'

    a2h_done = [s for s in sellers.values() if s.get('a')]
    live = [s for s in a2h_done if s.get('g')]
    pending = [s for s in a2h_done if not s.get('g')]
    coh = {k: 0 for k in ('M0', 'M1', 'M2', 'M3', 'M3+')}
    for s in pending:
        c = cohort_of(s['a'])
        if c:
            coh[c] += 1
    # M3+ (old never-live backlog) is excluded from the "yet to golive" list per request.
    kept_coh = {k: coh[k] for k in ('M0', 'M1', 'M2', 'M3')}
    mtd_live = sum(1 for s in live if (s.get('g') or '')[:7] == cur_month)
    summary = {
        'asOf': cur_month,
        'totalA2H': len(a2h_done),
        'live': len(live),                       # A2H done + any marketing spend (has go_live)
        'yetToGolive': sum(kept_coh.values()),   # A2H done, never spent (M0-M3 only)
        'mtdLive': mtd_live,                     # first spend landed in current month
        'cohort': kept_coh,                      # yet-to-golive bucketed by A2H age (M3+ excluded)
        'm3plusExcluded': coh['M3+'],            # count removed from the list (reference)
    }
    print(f"[golive] summary: A2H={summary['totalA2H']} live={summary['live']} "
          f"yetToGolive={summary['yetToGolive']} mtdLive={summary['mtdLive']} cohort={coh}")

    out = {
        'generatedAt': datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ'),
        'summary': summary,
        'sellers': sellers,
    }
    json.dump(out, open(OUT, 'w'), separators=(',', ':'))
    print(f"[out] {OUT} ({os.path.getsize(OUT)} bytes) · {len(sellers)} sellers")

    if '--push' in sys.argv:
        subprocess.run(['git', '-C', REPO, 'add', 'golive_data.json'], check=True)
        r = subprocess.run(['git', '-C', REPO, 'commit', '-m', 'Refresh Golive (cohort) data'], capture_output=True, text=True)
        print(r.stdout.strip() or r.stderr.strip())
        if r.returncode == 0:
            subprocess.run(['git', '-C', REPO, 'push', 'origin', 'main'], check=True); print("[push] deployed")


if __name__ == '__main__':
    main()
