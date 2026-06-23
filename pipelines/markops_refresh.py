#!/usr/bin/env python3
"""Build markops_data.json for Input Metrics -> Task Compliance -> Marketing Ops.

Source: card 11036 (all MarkOps tasks). Keeps the last 45 days. Categorisation by
sub_type is done client-side. status completed/closed = done, pending = pending;
within-SLA = tat <= sla_in_min (client-side).

Run: cd ~/shopdeck-metrics-site && python3 pipelines/markops_refresh.py --push
"""
import json, os, sys, subprocess, urllib.request, datetime

CARD = 11036
REPO = os.path.expanduser(os.environ.get("REPO_DIR", "~/shopdeck-metrics-site"))
OUT = os.path.join(REPO, "markops_data.json")
CRED_CACHE = os.path.expanduser("~/metabase-arr-refresh/.mbcreds")
DESKTOP_CFG = os.path.expanduser("~/Library/Application Support/Claude/claude_desktop_config.json")
WINDOW_DAYS = 45


def creds():
    if os.environ.get('METABASE_URL'):
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


def main():
    url, email, pw = creds()
    tok = req(url + "/api/session", 'POST', {"username": email, "password": pw}, {'Content-Type': 'application/json'})['id']
    H = {'Content-Type': 'application/json', 'X-Metabase-Session': tok}
    rows = req(f"{url}/api/card/{CARD}/query/json", 'POST', {}, H)
    print(f"[markops] card {CARD}: {len(rows)} rows")
    cutoff = (datetime.date.today() - datetime.timedelta(days=WINDOW_DAYS)).isoformat()

    out = []
    for r in rows:
        cr = d10(r.get('task_created_at'))
        if cr and cr < cutoff:
            continue
        out.append({
            'id': str(r.get('id') or ''), 's': str(r.get('seller_id') or ''),
            'ty': str(r.get('type') or ''), 'st': str(r.get('sub_type') or ''), 'src': str(r.get('source') or ''),
            'who': str(r.get('assignee_name') or '').strip() or 'Unassigned',
            'status': str(r.get('status') or '').strip().lower(),
            'cr': cr, 'du': d10(r.get('task_due_date')),
            'sla': r.get('sla_in_min'), 'tat': r.get('tat'),
        })
    data = {'generatedAt': datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ'), 'windowDays': WINDOW_DAYS, 'cutoff': cutoff, 'tasks': out}
    json.dump(data, open(OUT, 'w'), separators=(',', ':'))
    done = sum(1 for t in out if t['status'] in ('completed', 'closed'))
    print(f"[out] {OUT} ({os.path.getsize(OUT)} bytes) · {len(out)} markops tasks · {done} done")

    if '--push' in sys.argv:
        subprocess.run(['git', '-C', REPO, 'add', 'markops_data.json'], check=True)
        r = subprocess.run(['git', '-C', REPO, 'commit', '-m', 'Refresh Marketing Ops data'], capture_output=True, text=True)
        print(r.stdout.strip() or r.stderr.strip())
        if r.returncode == 0:
            subprocess.run(['git', '-C', REPO, 'push', 'origin', 'main'], check=True); print("[push] deployed")


if __name__ == '__main__':
    main()
