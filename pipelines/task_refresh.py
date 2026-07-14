#!/usr/bin/env python3
"""Build task_data.json for Input Metrics -> Task Compliance (Growth Consultants).

Source: card 10181 (all tasks). Keep GC-bucket tasks. status completed/closed = done,
pending = pending. 'within SLA' computed client-side (completion_date <= task_due_date).

Run: cd ~/shopdeck-metrics-site && python3 ~/metabase-arr-refresh/task_refresh.py --push
"""
import json, os, sys, subprocess, urllib.request, datetime

CARD = 10181
REPO = os.path.expanduser(os.environ.get("REPO_DIR", "~/shopdeck-metrics-site"))
OUT  = os.path.join(REPO, "task_data.json")
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


def d10(v):
    return str(v)[:10] if v else ''


def yw_of(cr):
    if not cr:
        return None
    try:
        iy, iw, _ = datetime.date.fromisoformat(cr).isocalendar()
        return iy * 100 + iw
    except ValueError:
        return None


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
    WINDOW_DAYS = 45
    cutoff = (datetime.date.today() - datetime.timedelta(days=WINDOW_DAYS)).isoformat()
    import re as _re
    norm = lambda s: _re.sub(r'\s+', ' ', str(s or '')).strip().lower()
    EXCLUDE_GMS = {norm(x) for x in ['Anubhav Kumar', 'Biswas Churiwal', 'Dummy MGSV GM', 'Manorama Yadav', 'Simran S', 'Suraj Jogani', 'Unassigned', 'Vikas Tiwari']}
    EXCLUDE_CLS = {norm(x) for x in ['Gayathri Ramesh', 'Lokesh Kesiraju', 'Shreyansh Singhvi']}

    out = []
    for card_id, bucket in [(CARD, 'GC'), (10951, 'KAM'), (10959, 'KAE')]:
        rows = req(f"{url}/api/card/{card_id}/query/json", 'POST', {}, H)
        print(f"[task] card {card_id} ({bucket}): {len(rows)} rows")
        for r in rows:
            if str(r.get('assignee_bucket') or '').strip().upper() != bucket:
                continue
            if norm(r.get('cl_name')) in EXCLUDE_CLS:  # CL exclusion (all buckets)
                continue
            gmn = norm(r.get('gm_name'))
            if bucket == 'GC' and (gmn == '' or gmn in EXCLUDE_GMS):  # GM exclusion only for GC
                continue
            cr = d10(r.get('task_created_at'))
            if cr and cr < cutoff:
                continue
            out.append({
                'id': str(r.get('id') or ''), 's': str(r.get('seller_id') or ''), 'b': bucket,
                'ty': str(r.get('type') or ''), 'st': str(r.get('sub_type') or ''),
                'gc': str(r.get('assignee_name') or '').strip() or 'Unassigned',
                'gm': str(r.get('gm_name') or '').strip() or 'Unassigned',
                'cl': str(r.get('cl_name') or '').strip() or 'Unassigned',
                'status': str(r.get('status') or '').strip().lower(),
                'cr': cr, 'du': d10(r.get('task_due_date')), 'cp': d10(r.get('completion_date')),
                'sla': r.get('sla_in_min'), 'tat': r.get('tat'), 'yw': yw_of(cr),
            })

    data = {'generatedAt': datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ'), 'windowDays': WINDOW_DAYS, 'cutoff': cutoff, 'tasks': out}
    json.dump(data, open(OUT, 'w'), separators=(',', ':'))
    done = sum(1 for t in out if t['status'] in ('completed', 'closed'))
    pend = sum(1 for t in out if t['status'] == 'pending')
    print(f"[out] {OUT} ({os.path.getsize(OUT)} bytes) · {len(out)} GC tasks · {done} done · {pend} pending")

    if '--push' in sys.argv:
        subprocess.run(['git', '-C', REPO, 'add', 'task_data.json'], check=True)
        r = subprocess.run(['git', '-C', REPO, 'commit', '-m', 'Refresh Task Compliance data'], capture_output=True, text=True)
        print(r.stdout.strip() or r.stderr.strip())
        if r.returncode == 0:
            subprocess.run(['git', '-C', REPO, 'push', 'origin', 'main'], check=True); print("[push] deployed")


if __name__ == '__main__':
    main()
