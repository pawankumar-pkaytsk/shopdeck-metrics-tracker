#!/usr/bin/env python3
"""Build nps_data.json for Output Metrics -> NPS.

Sources:
  - card 10990: NPS submissions (seller_id, status, submitted_date, answer_text=score)
  - card 10992: GC/GM/KAM changelog (seller_id, assignee=role, name, start_date, end_date)
For each submitted NPS, attribute the score to the GC/GM/KAM who was assigned to that
seller AT submit time (point-in-time via tenure windows). NPS metric = simple average
of scores (rounded 2 dp) per person.

Run: cd ~/shopdeck-metrics-site && python3 ~/metabase-arr-refresh/nps_refresh.py --push
"""
import json, os, sys, subprocess, urllib.request, datetime

NPS_CARD = 10990
LOG_CARD = 10992
TEAM_CARD = 1880  # col team_mapping = HIT | REVENUE, weekly [start_date,end_date] per seller
REPO = os.path.expanduser(os.environ.get("REPO_DIR", "~/shopdeck-metrics-site"))
OUT = os.path.join(REPO, "nps_data.json")
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
    nps = req(f"{url}/api/card/{NPS_CARD}/query/json", 'POST', {}, H)
    log = req(f"{url}/api/card/{LOG_CARD}/query/json", 'POST', {}, H)
    bk = req(f"{url}/api/card/{TEAM_CARD}/query/json", 'POST', {}, H)
    print(f"[nps] feedback(10990)={len(nps)} · changelog(10992)={len(log)} · team(1880)={len(bk)} rows")

    # team index: seller -> [ {start, end, team} ]  (weekly windows, date-only)
    tidx = {}
    for r in bk:
        sid = str(r.get('seller_id') or '').strip()
        t = str(r.get('team_mapping') or '').strip().upper()
        if not sid or t not in ('HIT', 'REVENUE'):
            continue
        tidx.setdefault(sid, []).append({
            'start': str(r.get('start_date') or '')[:10], 'end': str(r.get('end_date') or '')[:10], 't': t,
        })

    def team(sid, when):
        ents = tidx.get(sid)
        if not ents:
            return 'Unassigned'
        d = when[:10]  # team windows are dates; compare date-to-date
        active = [e for e in ents if e['start'] and e['start'] <= d and (not e['end'] or d <= e['end'])]
        if active:
            return max(active, key=lambda e: e['start'])['t']
        before = [e for e in ents if e['start'] and e['start'] <= d]
        if before:
            return max(before, key=lambda e: e['start'])['t']  # last-known team
        return 'Unassigned'

    # changelog index: seller -> role -> [ {start, end, name} ]
    idx = {}
    for r in log:
        sid = str(r.get('seller_id') or '').strip()
        role = str(r.get('assignee') or '').strip().upper()
        if not sid or role not in ('GC', 'GM', 'KAM'):
            continue
        idx.setdefault(sid, {}).setdefault(role, []).append({
            'start': str(r.get('start_date') or ''), 'end': str(r.get('end_date') or ''),
            'name': str(r.get('name') or '').strip() or 'Unassigned',
        })

    def who(sid, role, when):
        ents = idx.get(sid, {}).get(role)
        if not ents:
            return 'Unassigned'
        # active window: start <= when <= end (end blank = ongoing)
        active = [e for e in ents if e['start'] and e['start'] <= when and (not e['end'] or when <= e['end'])]
        if active:
            return max(active, key=lambda e: e['start'])['name']
        # last-known: a window started before submit but had ended -> attribute to that person
        before = [e for e in ents if e['start'] and e['start'] <= when]
        if before:
            return max(before, key=lambda e: e['start'])['name']
        # NPS predates all records -> do not guess
        return 'Unassigned'

    rows = []
    matched = 0
    for r in nps:
        if str(r.get('status') or '').strip().lower() != 'submitted':
            continue
        sid = str(r.get('seller_id') or '').strip()
        when = str(r.get('submitted_date') or '')
        if not sid or not when:
            continue
        try:
            score = float(r.get('answer_text'))
        except (TypeError, ValueError):
            continue
        gc, gm, kam = who(sid, 'GC', when), who(sid, 'GM', when), who(sid, 'KAM', when)
        tm = team(sid, when)
        if gc != 'Unassigned' or gm != 'Unassigned' or kam != 'Unassigned':
            matched += 1
        rows.append({'s': sid, 'd': when[:10], 'sc': score, 'gc': gc, 'gm': gm, 'kam': kam, 'tm': tm})

    out = {'generatedAt': datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ'), 'rows': rows}
    json.dump(out, open(OUT, 'w'), separators=(',', ':'))
    from collections import Counter
    tc = Counter(r['tm'] for r in rows)
    print(f"[out] {OUT} ({os.path.getsize(OUT)} bytes) · {len(rows)} NPS responses · {matched} mapped to a person")
    print(f"[team] HIT={tc['HIT']} · REVENUE={tc['REVENUE']} · Unassigned={tc['Unassigned']}")

    if '--push' in sys.argv:
        subprocess.run(['git', '-C', REPO, 'add', 'nps_data.json'], check=True)
        r = subprocess.run(['git', '-C', REPO, 'commit', '-m', 'Refresh NPS data'], capture_output=True, text=True)
        print(r.stdout.strip() or r.stderr.strip())
        if r.returncode == 0:
            subprocess.run(['git', '-C', REPO, 'push', 'origin', 'main'], check=True); print("[push] deployed")


if __name__ == '__main__':
    main()
