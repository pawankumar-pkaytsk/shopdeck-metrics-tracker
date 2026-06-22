#!/usr/bin/env python3
"""Build hypercare_sellers.json — sellers belonging to the Hypercare team.

Rule: seller's growth_consultant_name (card 7753) is one of the Hypercare GCs
{Nikita S, Sadiya, Nishan, Aaruni, Dev Vashisth} AND the seller's last spend date
(card 10065) is more than 45 days ago (or never spent).

Run: cd ~/shopdeck-metrics-site && python3 ~/metabase-arr-refresh/hypercare_refresh.py --push
"""
import json, os, sys, subprocess, urllib.request, datetime

MAP_CARD = 7753
SPEND_CARD = 10065
REPO = os.path.expanduser(os.environ.get("REPO_DIR", "~/shopdeck-metrics-site"))
OUT = os.path.join(REPO, "hypercare_sellers.json")
CRED_CACHE = os.path.expanduser("~/metabase-arr-refresh/.mbcreds")
DESKTOP_CFG = os.path.expanduser("~/Library/Application Support/Claude/claude_desktop_config.json")
import re
HYPERCARE_GCS = ['nikita s', 'sadiya', 'nishan', 'aaruni', 'dev vashisth']
norm = lambda s: re.sub(r'\s+', ' ', str(s or '')).strip().lower()


def creds():
    if os.environ.get('METABASE_URL'):  # CI / env-driven
        return os.environ['METABASE_URL'].rstrip('/'), os.environ['METABASE_USER_EMAIL'], os.environ['METABASE_PASSWORD']
    e = json.load(open(CRED_CACHE)) if os.path.exists(CRED_CACHE) else json.load(open(DESKTOP_CFG))['mcpServers']['metabase']['env']
    return e['METABASE_URL'].rstrip('/'), e['METABASE_USER_EMAIL'], e['METABASE_PASSWORD']


def req(url, method='GET', body=None, H=None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, method=method, headers=H or {})
    with urllib.request.urlopen(r, timeout=300) as resp:
        return json.loads(resp.read().decode())


def main():
    url, email, pw = creds()
    tok = req(url + "/api/session", 'POST', {"username": email, "password": pw}, {'Content-Type': 'application/json'})['id']
    H = {'Content-Type': 'application/json', 'X-Metabase-Session': tok}

    mapping = req(f"{url}/api/card/{MAP_CARD}/query/json", 'POST', {}, H)
    spend = req(f"{url}/api/card/{SPEND_CARD}/query/json", 'POST', {}, H)
    print(f"[hypercare] mapping(7753)={len(mapping)} · spend(10065)={len(spend)} rows")

    last_spend = {}
    for r in spend:
        sid = str(r.get('seller id') or r.get('seller_id') or '').strip()
        ls = str(r.get('last spend date') or '')[:10]
        if sid:
            last_spend[sid] = ls
    today = datetime.date.today()

    def stale(sid):
        ls = last_spend.get(sid, '')
        if not ls:
            return True  # never spent -> >45d
        try:
            return (today - datetime.date.fromisoformat(ls)).days > 45
        except ValueError:
            return True

    hyper = []
    matched_gc = set()
    for r in mapping:
        sid = str(r.get('seller_id') or '').strip()
        gc = norm(r.get('growth_consultant_name'))
        if not sid or not gc:
            continue
        if any(gc == tok or gc.startswith(tok + ' ') for tok in HYPERCARE_GCS) and stale(sid):
            hyper.append(sid)
            matched_gc.add(r.get('growth_consultant_name'))
    hyper = sorted(set(hyper))
    out = {'generatedAt': datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ'), 'sellers': hyper}
    json.dump(out, open(OUT, 'w'), separators=(',', ':'))
    print(f"[out] {OUT} ({os.path.getsize(OUT)} bytes) · {len(hyper)} hypercare sellers · GCs matched: {sorted(matched_gc)}")

    if '--push' in sys.argv:
        subprocess.run(['git', '-C', REPO, 'add', 'hypercare_sellers.json'], check=True)
        r = subprocess.run(['git', '-C', REPO, 'commit', '-m', 'Refresh Hypercare seller mapping'], capture_output=True, text=True)
        print(r.stdout.strip() or r.stderr.strip())
        if r.returncode == 0:
            subprocess.run(['git', '-C', REPO, 'push', 'origin', 'main'], check=True); print("[push] deployed")


if __name__ == '__main__':
    main()
