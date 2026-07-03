#!/usr/bin/env python3
"""Build bev_data.json for "Pratiksha's Bird Eye View" (1k-5k team lead overview).

Channel-split ARR/spend come from card 10892 (spend_meta/spend_google/arr_meta/arr_google
per seller per day). Everything else is read from the SAME local JSON snapshots the other
tabs render, so the numbers reconcile exactly:
  - scaling_data.json   -> Spend/Live (meta/google/blended)        (== Central Reports 1k-5k)
  - ts_data.json        -> Troubleshoot Compliance (meta)          (current week)
  - google_ts_data.json -> Troubleshoot Compliance (google)        (current week)
  - task_data.json      -> Task Compliance (last 7d, split by seller channel)

1k-5k membership = ts_data.hitsMap sellers whose GL is NOT a Good-Seller GL (== other tabs).
Channel of a seller: meta = meta yest spend > ₹1; google = has a Google ad account.

Run: cd ~/shopdeck-metrics-site && python3 ~/metabase-arr-refresh/bev_refresh.py --push
"""
import json, os, sys, subprocess, urllib.request, urllib.parse, datetime, glob, re


def read_sheet_sa(sid, rng):
    """Read a Google Sheet range via the service account (GOOGLE_SA_KEY env or local key file)."""
    from google.oauth2 import service_account
    import google.auth.transport.requests as gtr
    if os.environ.get('GOOGLE_SA_KEY'):
        cred = service_account.Credentials.from_service_account_info(
            json.loads(os.environ['GOOGLE_SA_KEY']), scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"])
    else:
        path = glob.glob(os.path.expanduser('~/Downloads/metrics-tracker-automation-*.json'))
        cred = service_account.Credentials.from_service_account_file(
            path[0], scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"])
    cred.refresh(gtr.Request())
    u = f"https://sheets.googleapis.com/v4/spreadsheets/{sid}/values/{urllib.parse.quote(rng)}"
    return json.loads(urllib.request.urlopen(urllib.request.Request(u, headers={'Authorization': 'Bearer ' + cred.token}), timeout=120).read()).get('values', [])

ARR_CARD = 10892
HIT_CARD = 10453
COHORT_CARD = 11020  # hit1 seller-monthwise ARR cohort (hit_year_month x M0..M6, incl TARGET row)
COHORT_ARR_CARD = 7336  # sellerwise-monthwise ARR — seller-level, for per-cell drilldown + GM/GL split
COHORT_MAP_CARD = 7753  # seller -> GC (growth_consultant_name) / GM (growth_manager_name)
# sellers excluded from the cohort (mirrors card 10881 SQL)
COHORT_EXCLUDE = {
    '6842d90c72a04e21d2b1a568', '68a6d75f199d3a1a4dc5999f', '6899cdfb9276fa61e591e495', '685e8c7272a04e21d2c4d06f',
    '68c825e3a062d6f5887bb7f5', '68a053264dd9dd3b3b7d24ac', '6894b31a43af5a95bf8fd866', '66c46e484730a17bf759b5e1',
    '6853fc5a72a04e21d2eee5f2', '691adb803cfef8159331df1e', '680b44ce32cecfb226779ad1', '68e766ca463c10f96030c2dd',
    '690080da83b6cf2e520802f9', '68bc07af13b3cba175cc8f3d', '692156c7317e68f10e677f24', '68f3481f317e68f10ef0c11c',
    '69396694f4a741fdd42784d1', '67b708bf64df57567422652b', '68b14a95edc7d1a2fb1d8e39', '694524eaef9f28d42121f563',
    '694f85d22ecd986d636d2997', '6996da8529b2762903563934', '6953a473927331f73defe234', '69c21e729a47d682f2552e7e',
    '69ce43737d14343a581b3778', '69de29971a323f7cb108aeb2',
}
REPO = os.path.expanduser(os.environ.get("REPO_DIR", "~/shopdeck-metrics-site"))
OUT = os.path.join(REPO, "bev_data.json")
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


def load(name):
    return json.load(open(os.path.join(REPO, name)))


def main():
    url, email, pw = creds()
    tok = req(url + "/api/session", 'POST', {"username": email, "password": pw}, {'Content-Type': 'application/json'})['id']
    H = {'Content-Type': 'application/json', 'X-Metabase-Session': tok}

    ts = load('ts_data.json')
    scaling = load('scaling_data.json')
    gts = load('google_ts_data.json')
    task = load('task_data.json')

    hits = ts.get('hitsMap', {})
    # 1k-5k team = hitsMap sellers with good=0 (as set by hit_master_data good_seller flag)
    team = {sid: m for sid, m in hits.items() if not m.get('good')}
    sids = set(team.keys())
    sc = scaling.get('sellers', {})

    def rec(sid):
        return sc.get(sid, {})

    # ---- channel-split ARR + spend (card 10892), yesterday = latest date present ----
    arr = req(f"{url}/api/card/{ARR_CARD}/query/json", 'POST', {}, H)
    by_date = {}
    for r in arr:
        d = str(r.get('date') or '')[:10]
        by_date.setdefault(d, []).append(r)
    def fnum(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return 0.0

    # Exclude TODAY (and any future-dated rows): today's row is partial (ARR lags spend, and the
    # day isn't over). "Yesterday" must be the calendar day before today.
    todayS = datetime.date.today().isoformat()
    all_dates = sorted(by_date)
    good_dates = [d for d in all_dates if d < todayS]
    if not good_dates:
        good_dates = all_dates
    as_of = good_dates[-1] if good_dates else ''
    yrows = [r for r in by_date.get(as_of, []) if str(r.get('seller_id') or '').strip() in sids]

    # daily series over booked dates -> lets the UI pick yesterday / day-before / last-7 / week / custom
    seller_meta = {}
    perf_by_date = {}
    for d in good_dates:
        am = sm = ag = sg = 0.0
        drows = []
        for r in by_date[d]:
            sid = str(r.get('seller_id') or '').strip()
            if sid not in sids:
                continue
            a_m, s_m = fnum(r.get('arr_meta')), fnum(r.get('spend_meta'))
            a_g, s_g = fnum(r.get('arr_google')), fnum(r.get('spend_google'))
            am += a_m; sm += s_m; ag += a_g; sg += s_g
            if a_m or s_m or a_g or s_g:
                drows.append([sid, round(a_m), round(s_m), round(a_g), round(s_g),
                              round(a_m + a_g), round(s_m + s_g)])
                if sid not in seller_meta:
                    seller_meta[sid] = {'n': r.get('seller_name') or team.get(sid, {}).get('n') or '',
                                        'gl': r.get('gc') or team.get(sid, {}).get('gc') or ''}
        perf_by_date[d] = {'am': round(am), 'sm': round(sm), 'ag': round(ag), 'sg': round(sg),
                           'ao': round(am + ag), 'so': round(sm + sg), 'rows': drows}

    arr_meta_detail, arr_google_detail = [], []
    arr_meta = spend_meta = arr_google = spend_google = 0.0
    for r in yrows:
        sid = str(r.get('seller_id') or '').strip()
        nm = r.get('seller_name') or team.get(sid, {}).get('n') or ''
        gl = r.get('gc') or team.get(sid, {}).get('gc') or ''
        am, sm = fnum(r.get('arr_meta')), fnum(r.get('spend_meta'))
        ag, sg = fnum(r.get('arr_google')), fnum(r.get('spend_google'))
        arr_meta += am; spend_meta += sm; arr_google += ag; spend_google += sg
        if am or sm:
            arr_meta_detail.append({'s': sid, 'n': nm, 'gl': gl, 'arr': round(am), 'spend': round(sm)})
        if ag or sg:
            arr_google_detail.append({'s': sid, 'n': nm, 'gl': gl, 'arr': round(ag), 'spend': round(sg)})
    arr_meta_detail.sort(key=lambda x: -x['arr'])
    arr_google_detail.sort(key=lambda x: -x['arr'])

    # ---- Spend/Live (meta/google/blended) over 1k-5k, matches Central Reports allocation ----
    assigned = len(sids)
    sl_detail = []
    meta_sp = goog_live = goog_sp = blend_sp = 0
    for sid in sids:
        t = rec(sid)
        my = fnum(t.get('my')); gy = fnum(t.get('gy')); gt = fnum(t.get('gt')); ga = str(t.get('ga') or '')
        m_spend = my > 1
        g_live = bool(ga) and gt > 1
        g_spend = gy > 10
        b_spend = m_spend or g_spend
        meta_sp += m_spend; goog_live += g_live; goog_sp += g_spend; blend_sp += b_spend
        sl_detail.append({'s': sid, 'n': team[sid].get('n', ''), 'gl': team[sid].get('gc', ''),
                          'my': round(my), 'gy': round(gy), 'ga': ga, 'gt': round(gt),
                          'mSpend': m_spend, 'gLive': g_live, 'gSpend': g_spend, 'blend': b_spend})
    sl_meta_pct = (meta_sp / assigned * 100) if assigned else None
    sl_google_pct = (goog_sp / goog_live * 100) if goog_live else None
    sl_blended_pct = (blend_sp / assigned * 100) if assigned else None

    # ---- Troubleshoot Compliance (current week): done this week / sellers with 7d spend > 3540 ----
    today = datetime.date.today()
    mon = today - datetime.timedelta(days=today.weekday())
    sun = mon + datetime.timedelta(days=6)
    monS, sunS = mon.isoformat(), sun.isoformat()
    SPEND_TS = 3540

    def ts_compliance(src_sellers, threshold):
        done = denom = 0
        det = []
        for sid in sids:
            t = src_sellers.get(sid, {})
            s7 = fnum(t.get('s7'))
            d = str(t.get('d') or '')[:10]
            if s7 > threshold:
                denom += 1
                dn = bool(d and monS <= d <= sunS)
                done += dn
                det.append({'s': sid, 'n': team[sid].get('n', ''), 'gl': team[sid].get('gc', ''),
                            's7': round(s7), 'lastTS': d or '—', 'doneThisWeek': dn})
        det.sort(key=lambda x: (x['doneThisWeek'], -x['s7']))
        pct = (done / denom * 100) if denom else None
        return done, denom, pct, det

    ts_done, ts_denom, ts_pct, ts_det = ts_compliance(ts.get('sellers', {}), ts.get('spendThreshold', SPEND_TS))
    g_done, g_denom, g_pct, g_det = ts_compliance(gts.get('sellers', {}), gts.get('spendThreshold', SPEND_TS))

    # ---- Task Compliance (last 7 days), split by seller channel (done / total) ----
    cut7 = (today - datetime.timedelta(days=7)).isoformat()
    todayS = today.isoformat()

    def is_meta(sid):
        return fnum(rec(sid).get('my')) > 1

    def is_google(sid):
        return bool(str(rec(sid).get('ga') or ''))

    CALL_TYPES = ('callback', 'seller_callback_management')

    def compliance(channel_pred, call_mode):
        done = total = 0
        det = []
        for t in task.get('tasks', []):
            sid = str(t.get('s') or '')
            if sid not in sids:
                continue
            cr = t.get('cr') or ''
            if not cr or cr < cut7:
                continue
            is_call = (t.get('ty') or '').lower().strip() in CALL_TYPES
            if call_mode != is_call:  # Task = non-call; Call = call types only
                continue
            if not channel_pred(sid):
                continue
            dn = (t.get('status') or '') in ('completed', 'closed')
            # exclude pending tasks whose SLA isn't breached yet (due today or later) — still have time
            if not dn:
                du = t.get('du') or ''
                if du and du >= todayS:
                    continue
            total += 1
            done += dn
            det.append({'s': sid, 'n': team.get(sid, {}).get('n', ''), 'gl': team.get(sid, {}).get('gc', ''),
                        'b': t.get('b', ''), 'ty': t.get('ty', ''), 'status': t.get('status', ''), 'cr': cr, 'done': dn})
        det.sort(key=lambda x: (x['done'], x['cr']))
        pct = (done / total * 100) if total else None
        return done, total, pct, det

    tm_done, tm_total, tm_pct, tm_det = compliance(is_meta, False)
    tg_done, tg_total, tg_pct, tg_det = compliance(is_google, False)
    cm_done, cm_total, cm_pct, cm_det = compliance(is_meta, True)
    cg_done, cg_total, cg_pct, cg_det = compliance(is_google, True)

    accounts_detail = sorted(({'s': sid, 'n': m.get('n', ''), 'gl': m.get('gc', ''), 'gm': m.get('gm', '')}
                              for sid, m in team.items()), key=lambda x: (x['gl'], x['n']))

    # ---- HIT2 (card 10453, hit2 == 1) — total count across all teams ----
    MON3 = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
    hitrows = req(f"{url}/api/card/{HIT_CARD}/query/json", 'POST', {}, H)
    hit2_detail = []
    for r in hitrows:
        if str(r.get('hit2')).strip() not in ('1', '1.0', 'True', 'true'):
            continue
        hm, hy = r.get('hit2_month'), r.get('hit2_year')
        if hm is None or hy is None:
            continue
        hit2_detail.append({'s': str(r.get('seller_id') or ''), 'n': str(r.get('seller_name') or ''),
                            'mon': MON3[int(hm) - 1] + ' ' + str(int(hy)), 'team': str(r.get('team') or '')})
    hit2_detail.sort(key=lambda x: x['mon'])

    # ---- ARR cohort (card 11020): hit_year_month x M0..M6, with TARGET row ----
    cohort_raw = req(f"{url}/api/card/{COHORT_CARD}/query/json", 'POST', {}, H)
    mcols = ['M0', 'M1', 'M2', 'M3', 'M4', 'M5', 'M6']
    target = {}
    cohort_rows = []
    for r in cohort_raw:
        ym = str(r.get('hit_year_month') or '')
        vals = {c: (round(fnum(r.get(c.lower()))) if r.get(c.lower()) is not None else None) for c in mcols}
        if ym.upper() == 'TARGET':
            target = vals
        else:
            cohort_rows.append({'ym': ym, 'n': r.get('seller_count'), 'v': vals})
    cohort_rows.sort(key=lambda x: x['ym'])

    # cohort membership (seller -> earliest hit_year_month, matching 11020's MIN) from hit_master_data
    cohort_sellers = {}
    for r in hitrows:
        sid = str(r.get('seller_id') or '').strip()
        if not sid or sid in COHORT_EXCLUDE:
            continue
        is_hits = str(r.get('team') or '').strip().upper() == 'HITS' or str(r.get('hit2')).strip() in ('1', '1.0', 'True', 'true')
        hm, hy = r.get('hit_month'), r.get('hit_year')
        if not is_hits or hm is None or hy is None:
            continue
        ym = '%d%02d' % (int(hy), int(hm))
        if ym >= '202510' and (sid not in cohort_sellers or ym < cohort_sellers[sid]):
            cohort_sellers[sid] = ym

    # seller -> GC/GM (card 7753)
    clean = lambda v: (re.sub(r'\s+', ' ', str(v or '').strip()) if str(v or '').strip() not in ('', '-') else 'Unassigned')
    smap = {}
    for r in req(f"{url}/api/card/{COHORT_MAP_CARD}/query/json", 'POST', {}, H):
        sid = str(r.get('seller_id') or '').strip()
        if sid:
            smap[sid] = {'gc': clean(r.get('growth_consultant_name')), 'gm': clean(r.get('growth_manager_name'))}

    # seller-level ARR (card 7336): cohort age-matrix detail (top table) + per-(seller,month) ARR
    arr_rows = req(f"{url}/api/card/{COHORT_ARR_CARD}/query/json", 'POST', {}, H)
    seen_arr = set()
    cohort_detail = {}
    arr_by_sm, name_by_s, months_seen = {}, {}, set()
    for r in arr_rows:
        sid = str(r.get('seller_id') or '').strip()
        try:
            aym = str(int(r.get('year_month')))
        except (TypeError, ValueError):
            continue
        if len(aym) != 6 or (sid, aym) in seen_arr:
            continue
        seen_arr.add((sid, aym))
        arr_v = r.get('arr')
        if arr_v is None:
            continue
        name_by_s[sid] = str(r.get('company_name') or '')
        arr_by_sm[(sid, aym)] = round(fnum(arr_v))
        cym = cohort_sellers.get(sid)
        if cym:
            months_seen.add(aym)
            age = (int(aym[:4]) - int(cym[:4])) * 12 + (int(aym[4:]) - int(cym[4:]))
            if 0 <= age <= 6:
                cohort_detail.setdefault(cym, {}).setdefault('M%d' % age, []).append(
                    {'s': sid, 'n': name_by_s[sid], 'arr': arr_by_sm[(sid, aym)]})
    for k in cohort_detail:
        for mk in cohort_detail[k]:
            cohort_detail[k][mk].sort(key=lambda x: -x['arr'])

    # ---- GL/GM Target vs Achievement (1k-5k), month-wise ----
    def target_for(age):
        return target.get('M%d' % min(max(age, 0), 5)) or 0

    # HIT2 target per (year_month -> GC) from the 'Collated' sheet
    hit2_tgt_m = {}
    try:
        for row in read_sheet_sa('1cV0DptEcl-HfamWP_6k6oAmLqYliUvo21hDKwWkqq2s', "'Collated'!A2:D"):
            try:
                ym = '%d%02d' % (int(row[3]), int(row[2]))
                hit2_tgt_m.setdefault(ym, {})[clean(row[0])] = int(float(row[1]))
            except (ValueError, TypeError, IndexError):
                continue
    except Exception as _e:
        print('[cohort] Collated sheet read failed:', _e)
    # HIT2 owner (seller -> GL/GM) from the 'HITS 2 Handover' sheet (C=seller, E=GL, F=GM)
    hit2_owner = {}
    try:
        for row in read_sheet_sa('198xsGns4LC-80BqAoOdv_Aup29udacaam8WB7jOZalA', "'HITS 2 Handover'!A2:F"):
            if len(row) >= 5 and str(row[2]).strip():
                hit2_owner[str(row[2]).strip()] = {'gl': clean(row[4]), 'gm': clean(row[5]) if len(row) >= 6 else 'Unassigned'}
    except Exception as _e:
        print('[cohort] HITS 2 Handover sheet read failed:', _e)

    # canonical 1k-5k GLs = union of GLs across all Collated months
    gls = sorted({g for mm in hit2_tgt_m.values() for g in mm if g != 'Unassigned'})
    gc2gm_all = {}
    for _sid, _mm in smap.items():
        _g = _mm.get('gc')
        if _g and _g != 'Unassigned' and _g not in gc2gm_all:
            gc2gm_all[_g] = _mm.get('gm') or 'Unassigned'

    # months to expose: Collated months + recent ARR months (cap 6)
    months = sorted(set(hit2_tgt_m) | set(months_seen))[-6:]
    report_ym = (max(hit2_tgt_m) if hit2_tgt_m else (max(months) if months else ''))

    def compute_tva(ym):
        ry, rm = int(ym[:4]), int(ym[4:])
        arrT, arrA, det = {}, {}, {}
        for sid, cym in cohort_sellers.items():
            arr = arr_by_sm.get((sid, ym))
            if arr is None:
                continue
            age = (ry - int(cym[:4])) * 12 + (rm - int(cym[4:]))
            if age < 0:
                continue
            gc = smap.get(sid, {}).get('gc', 'Unassigned')
            if gc not in gls:
                continue  # only canonical 1k-5k GLs
            t = target_for(age)
            arrT[gc] = arrT.get(gc, 0) + t
            arrA[gc] = arrA.get(gc, 0) + arr
            det.setdefault(gc, []).append({'s': sid, 'n': name_by_s.get(sid, ''), 'age': 'M%d' % age, 'tgt': t, 'arr': arr})
        h2A, h2det = {}, {}
        for r in hitrows:
            if str(r.get('hit2')).strip() not in ('1', '1.0', 'True', 'true'):
                continue
            try:
                if int(r.get('hit2_year')) != ry or int(r.get('hit2_month')) != rm:
                    continue
            except (ValueError, TypeError):
                continue
            sid_h = str(r.get('seller_id') or '').strip()
            own = hit2_owner.get(sid_h)
            gl = own['gl'] if own else 'Unassigned'   # HIT2 credited per the Handover sheet
            if gl not in gls:
                continue
            h2A[gl] = h2A.get(gl, 0) + 1
            h2det.setdefault(gl, []).append({'s': sid_h, 'n': str(r.get('seller_name') or '') or name_by_s.get(sid_h, '')})
        h2t = hit2_tgt_m.get(ym, {})

        def mkrow(name, aT, aA, h2t_v, h2a_v, nrec):
            aT, aA = round(aT), round(aA)
            return {'name': name, 'hit2T': h2t_v, 'hit2A': h2a_v, 'arrT': aT, 'arrA': aA,
                    'delta': max(0, round(0.85 * aT - aA)), 'n': nrec}
        gl_rows = [mkrow(g, arrT.get(g, 0), arrA.get(g, 0), h2t.get(g), h2A.get(g, 0), len(det.get(g, []))) for g in gls]
        gl_detail = {g: sorted(det.get(g, []), key=lambda x: -x['arr']) for g in gls}
        gl_hit2 = {g: h2det.get(g, []) for g in gls}
        # GM rollup
        gT, gA, gh2T, gh2A, gdet, ghit2 = {}, {}, {}, {}, {}, {}
        for g in gls:
            gm = gc2gm_all.get(g) or 'Unassigned'
            if gm == 'Unassigned':
                continue
            gT[gm] = gT.get(gm, 0) + arrT.get(g, 0)
            gA[gm] = gA.get(gm, 0) + arrA.get(g, 0)
            gh2A[gm] = gh2A.get(gm, 0) + h2A.get(g, 0)
            gh2T[gm] = gh2T.get(gm, 0) + (h2t.get(g) or 0)
            gdet.setdefault(gm, []).extend(det.get(g, []))
            ghit2.setdefault(gm, []).extend(h2det.get(g, []))
        gms = sorted(gT)
        gm_rows = [mkrow(gm, gT.get(gm, 0), gA.get(gm, 0), gh2T.get(gm) or None, gh2A.get(gm, 0), len(gdet.get(gm, []))) for gm in gms]
        gm_detail = {gm: sorted(gdet.get(gm, []), key=lambda x: -x['arr']) for gm in gms}
        gm_hit2 = {gm: ghit2.get(gm, []) for gm in gms}
        return {'byGL': {'rows': gl_rows, 'detail': gl_detail, 'hit2': gl_hit2},
                'byGM': {'rows': gm_rows, 'detail': gm_detail, 'hit2': gm_hit2}}

    by_month = {ym: compute_tva(ym) for ym in months}
    print(f'[cohort] TVA months {months} · report {report_ym} · {len(gls)} GLs')

    cohort = {'mcols': mcols, 'target': target, 'rows': cohort_rows, 'detail': cohort_detail,
              'reportMonth': report_ym,
              'tva': {'months': months, 'reportMonth': report_ym, 'byMonth': by_month}}

    # ---- CHURN: sellers who moved HIT -> REVENUE (card 1880), no spend for > 21 days ----
    # BEV card: only days gate (rev_spend gate removed per request).
    # TvA column: both gates (rev_spend >= 11800 AND days > 21) — old logic for GL/GM breakdown.
    CHURN_REV_SPEND = 11800
    CHURN_DAYS = 21
    last_spend = {}
    for r in req(f"{url}/api/card/10065/query/json", 'POST', {}, H):
        sid = str(r.get('seller id') or r.get('seller_id') or '').strip()
        if sid:
            last_spend[sid] = {'d': str(r.get('last spend date') or '')[:10], 'c': str(r.get('company') or '')}
    bk = req(f"{url}/api/card/1880/query/json", 'POST', {}, H)  # team_mapping + weekly marketing spend
    wk = {}
    for r in bk:
        sid = str(r.get('seller_id') or '').strip()
        if not sid:
            continue
        wk.setdefault(sid, []).append({'st': str(r.get('start_date') or '')[:10],
                                       'tm': str(r.get('team_mapping') or '').strip().upper(),
                                       'sp': fnum(r.get('marketing_spend_tax_'))})
    churned = []          # BEV card: days > 21 only (no rev_spend gate)
    churned_tva = []      # TvA column: old logic — rev_spend >= 11800 AND days > 21
    for sid, weeks in wk.items():
        if sid not in sids:
            continue
        _hits = [w for w in weeks if w['tm'] == 'HIT' and w['st']]
        revs = [w for w in weeks if w['tm'] == 'REVENUE' and w['st']]
        if not _hits or not revs:
            continue
        first_hit = min(w['st'] for w in _hits)
        rev_after = [w for w in revs if w['st'] >= first_hit]
        if not rev_after:
            continue
        rev_spend = sum(w['sp'] for w in rev_after)
        ls = last_spend.get(sid)
        if not ls or not ls['d']:
            continue
        try:
            lsd = datetime.date.fromisoformat(ls['d'])
        except ValueError:
            continue
        days = (today - lsd).days
        if days <= CHURN_DAYS:
            continue
        churn_date = lsd + datetime.timedelta(days=CHURN_DAYS)
        entry = {'s': sid, 'n': ls['c'] or team.get(sid, {}).get('n', ''),
                 'revSpend': round(rev_spend), 'lastSpend': ls['d'], 'days': days,
                 'churnDate': churn_date.isoformat(), 'churnMonth': churn_date.isoformat()[:7]}
        churned.append(entry)
        if rev_spend >= CHURN_REV_SPEND:
            churned_tva.append(entry)
    churned.sort(key=lambda x: x['churnDate'], reverse=True)
    churned_tva.sort(key=lambda x: x['churnDate'], reverse=True)
    from collections import Counter as _C
    churn = {'value': len(churned), 'churned': churned, 'daysGate': CHURN_DAYS,
             'byMonth': dict(_C(x['churnMonth'] for x in churned))}

    # Churn per GL/GM for TvA column (old logic: rev_spend >= 11800 AND days > 21)
    churned_tva_by_gl = {}
    for c in churned_tva:
        gc = smap.get(c['s'], {}).get('gc', 'Unassigned')
        churned_tva_by_gl.setdefault(gc, []).append(c['s'])
    for ym, tva_data in by_month.items():
        for row in tva_data['byGL']['rows']:
            row['churn'] = len(churned_tva_by_gl.get(row['name'], []))
        for row in tva_data['byGM']['rows']:
            cnt = sum(len(churned_tva_by_gl.get(g, [])) for g in gls if gc2gm_all.get(g) == row['name'])
            row['churn'] = cnt

    # Weekly 1k-5k metrics (card 11115) for the table under ARR Cohort (1k-5k)
    weekly_1k5k = []
    try:
        _w = req(f"{url}/api/card/11115/query/json", 'POST', {}, H)
        def _wnum(v):
            try: return round(float(v), 2)
            except (TypeError, ValueError): return None
        for _r in sorted(_w, key=lambda x: -(int(x.get('year_week') or 0))):
            weekly_1k5k.append({
                'yw': str(_r.get('year_week') or ''),
                'total': _r.get('total'),
                'running': _r.get('running'),
                'notRunning': _r.get('not_running'),
                'profitGt5': _r.get('profit_gt_5'),
                'breakeven': _r.get('breakeven'),
                'loss': _r.get('loss'),
                'hitPct': _wnum(_r.get('hit_pct')),
                'bh': _wnum(_r.get('bh')),
                'beBh': _wnum(_r.get('be_bh')),
                'arrBhPct': _wnum(_r.get('arr_bh_pct')),
                'arrBeBhPct': _wnum(_r.get('arr_be_bh_pct')),
            })
        print(f"[bev] weekly 1k-5k (card 11115): {len(weekly_1k5k)} weeks")
    except Exception as _e:
        print(f"[bev] card 11115 failed: {_e}")

    out = {
        'generatedAt': datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ'),
        'asOfDate': as_of, 'weekMon': monS, 'weekSun': sunS, 'last7Cutoff': cut7, 'last7End': today.isoformat(),
        'dates': good_dates, 'sellerMeta': seller_meta, 'perfByDate': perf_by_date,
        'cards': {
            'accounts':    {'value': len(sids), 'detail': accounts_detail},
            'hit2':        {'value': len(hit2_detail), 'detail': hit2_detail},
            'cohort':      cohort,
            'weekly1k5k':  weekly_1k5k,
            'churn':       churn,
            'arr_meta':    {'value': round(arr_meta), 'detail': arr_meta_detail},
            'spend_meta':  {'value': round(spend_meta), 'detail': arr_meta_detail},
            'arr_google':  {'value': round(arr_google), 'detail': arr_google_detail},
            'spend_google':{'value': round(spend_google), 'detail': arr_google_detail},
            'arr_overall': {'value': round(arr_meta + arr_google), 'detail': sorted(arr_meta_detail + arr_google_detail, key=lambda x: -x['arr'])},
            'spend_overall':{'value': round(spend_meta + spend_google), 'detail': sorted(arr_meta_detail + arr_google_detail, key=lambda x: -x['arr'])},
            'sl_meta':     {'pct': sl_meta_pct, 'num': meta_sp, 'den': assigned, 'detail': sl_detail},
            'sl_google':   {'pct': sl_google_pct, 'num': goog_sp, 'den': goog_live, 'detail': sl_detail},
            'sl_blended':  {'pct': sl_blended_pct, 'num': blend_sp, 'den': assigned, 'detail': sl_detail},
            'ts_meta':     {'pct': ts_pct, 'num': ts_done, 'den': ts_denom, 'detail': ts_det},
            'ts_google':   {'pct': g_pct, 'num': g_done, 'den': g_denom, 'detail': g_det},
            'task_meta':   {'pct': tm_pct, 'num': tm_done, 'den': tm_total, 'detail': tm_det},
            'task_google': {'pct': tg_pct, 'num': tg_done, 'den': tg_total, 'detail': tg_det},
            'call_meta':   {'pct': cm_pct, 'num': cm_done, 'den': cm_total, 'detail': cm_det},
            'call_google': {'pct': cg_pct, 'num': cg_done, 'den': cg_total, 'detail': cg_det},
        },
    }
    json.dump(out, open(OUT, 'w'), separators=(',', ':'))
    print(f"[bev] as-of {as_of} · week {monS}..{sunS} · 1k-5k accounts={len(sids)} · HIT2 total={len(hit2_detail)}")
    print(f"  ARR meta={round(arr_meta):,} spend meta={round(spend_meta):,} · ARR google={round(arr_google):,} spend google={round(spend_google):,}")
    print(f"  Spend/Live meta={meta_sp}/{assigned}={fmtpct(sl_meta_pct)} · google={goog_sp}/{goog_live}={fmtpct(sl_google_pct)} · blended={blend_sp}/{assigned}={fmtpct(sl_blended_pct)}")
    print(f"  TS meta={ts_done}/{ts_denom}={fmtpct(ts_pct)} · TS google={g_done}/{g_denom}={fmtpct(g_pct)}")
    print(f"  Task meta={tm_done}/{tm_total}={fmtpct(tm_pct)} · Task google={tg_done}/{tg_total}={fmtpct(tg_pct)}")
    print(f"  Call meta={cm_done}/{cm_total}={fmtpct(cm_pct)} · Call google={cg_done}/{cg_total}={fmtpct(cg_pct)}")
    print(f"[out] {OUT} ({os.path.getsize(OUT)} bytes)")

    if '--push' in sys.argv:
        subprocess.run(['git', '-C', REPO, 'add', 'bev_data.json'], check=True)
        r = subprocess.run(['git', '-C', REPO, 'commit', '-m', 'Refresh Bird Eye View data'], capture_output=True, text=True)
        print(r.stdout.strip() or r.stderr.strip())
        if r.returncode == 0:
            subprocess.run(['git', '-C', REPO, 'push', 'origin', 'main'], check=True); print("[push] deployed")


def fmtpct(p):
    return '—' if p is None else f"{p:.1f}%"


if __name__ == '__main__':
    main()
