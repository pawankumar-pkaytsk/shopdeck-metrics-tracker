#!/usr/bin/env python3
"""Build bev_data.json for "Pratiksha's Bird Eye View" (1k-5k team lead overview).

Channel-split ARR/spend come from card 10469 (spend_meta/spend_google/arr_meta/arr_google/
arr_overall per seller per day, last 6 months). Everything else is read from the SAME local JSON snapshots the other
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
from collections import defaultdict, Counter


def read_sheet_sa(sid, rng):
    """Read a Google Sheet range via the service account (GOOGLE_SA_KEY env or local key file)."""
    from google.oauth2 import service_account
    import google.auth.transport.requests as gtr
    SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
    if os.environ.get('GOOGLE_SA_KEY'):
        cred = service_account.Credentials.from_service_account_info(
            json.loads(os.environ['GOOGLE_SA_KEY']), scopes=SCOPES)
    else:
        # exact known path first (glob of ~/Downloads is blocked in some sandboxes), then glob fallback
        exact = os.path.expanduser('~/Downloads/metrics-tracker-automation-53ad2cdd4b65.json')
        path = exact if os.path.exists(exact) else (glob.glob(os.path.expanduser('~/Downloads/metrics-tracker-automation-*.json')) or [None])[0]
        cred = service_account.Credentials.from_service_account_file(path, scopes=SCOPES)
    cred.refresh(gtr.Request())
    u = f"https://sheets.googleapis.com/v4/spreadsheets/{sid}/values/{urllib.parse.quote(rng)}"
    return json.loads(urllib.request.urlopen(urllib.request.Request(u, headers={'Authorization': 'Bearer ' + cred.token}), timeout=120).read()).get('values', [])

ARR_CARD = 10469  # day-wise seller-wise spend + ARR (Meta/Google/overall), last 6 months, all sellers
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

    # single cached fetch of the ARR card (10469) — reused by the channel-split block,
    # the frozen-ARR (TvA) block, and the ARR-cohort block below.
    _c10469 = {}
    def get10469():
        if 'r' not in _c10469:
            try:
                _c10469['r'] = req(f"{url}/api/card/{ARR_CARD}/query/json", 'POST', {}, H)
            except Exception as _e:
                print(f"[arr] card {ARR_CARD} fetch failed: {_e}"); _c10469['r'] = []
        return _c10469['r']

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

    # ---- channel-split ARR + spend (card 10469), yesterday = latest date present ----
    arr = get10469()
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
    # Only keep SETTLED days: the most recent days often have ARR trickling in while spend/Google
    # aren't booked yet (spend = 0), which shows empty cards. A settled day = spend actually booked
    # (meta+google spend over 1k-5k sellers > 0). This keeps "Yesterday", "Day before", "Last 7d"
    # etc. all consistent on real days. Fall back to raw pre-today dates if none look settled.
    def _day_spend(d):
        return sum(fnum(r.get('spend_meta')) + fnum(r.get('spend_google'))
                   for r in by_date.get(d, []) if str(r.get('seller_id') or '').strip() in sids)
    good_dates = [d for d in all_dates if d < todayS and _day_spend(d) > 0]
    if not good_dates:
        good_dates = [d for d in all_dates if d < todayS] or all_dates
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

    # ---- freeze ARR at HIT2-conversion Friday (uses the shared card-10469 fetch) ----
    # HIT2 conversion Friday per seller (ISO week of hit2 -> that week's Friday)
    conv_friday = {}
    for r in hitrows:
        if str(r.get('hit2')).strip() not in ('1', '1.0', 'True', 'true'):
            continue
        sid = str(r.get('seller_id') or '').strip()
        yw = str(r.get('hit2_year_week') or '').strip()
        if not sid or len(yw) < 6:
            continue
        try:
            fri = datetime.date.fromisocalendar(int(yw[:4]), int(yw[4:6]), 5)  # Friday of that ISO week
            conv_friday[sid] = fri.isoformat()
        except (ValueError, TypeError):
            continue
    # frozen ARR = latest daily ARR_All (card 10469) on/before that seller's conversion Friday
    frozen_arr = {}
    for r in get10469():
        sid = str(r.get('seller_id') or '').strip()
        fri = conv_friday.get(sid)
        if not fri:
            continue
        at = r.get('arr_overall')
        if at is None:
            continue
        ds = str(r.get('date') or '')[:10]
        if ds and ds <= fri and (sid not in frozen_arr or ds > frozen_arr[sid][0]):
            frozen_arr[sid] = (ds, fnum(at))
    frozen_arr = {sid: round(v[1]) for sid, v in frozen_arr.items()}
    print(f"[tva] frozen ARR at conversion Friday for {len(frozen_arr)} HIT2 sellers")

    # ---- per-GL Google qualifiers (1k-5k accounts / live-google / google-spending), from scaling ----
    _ceil = lambda x: int(x) + (1 if x > int(x) else 0)
    gl_goog = {}
    for sid in sids:
        gl = smap.get(sid, {}).get('gc', 'Unassigned')
        if gl not in gls:
            continue
        t = rec(sid); d = gl_goog.setdefault(gl, {'acc': 0, 'glive': 0, 'gspend': 0})
        d['acc'] += 1
        if str(t.get('ga') or '') and fnum(t.get('gt')) > 1:
            d['glive'] += 1
        if fnum(t.get('gy')) > 50:
            d['gspend'] += 1

    def goog_fields(acc, glive, gspend):
        golive_ok = acc > 0 and (glive / acc) >= 0.5
        sl_ok = glive > 0 and (gspend / glive) >= 0.6
        return {
            'gAcc': acc, 'gLive': glive, 'gSpend': gspend,
            'gGoliveQual': golive_ok, 'gGolivePct': round(glive / acc * 100, 1) if acc else None,
            'gGoliveDelta': max(0, _ceil(0.5 * acc) - glive),
            'gSLQual': sl_ok, 'gSLPct': round(gspend / glive * 100, 1) if glive else None,
            'gSLDelta': max(0, _ceil(0.6 * glive) - gspend),
        }

    def compute_tva(ym):
        ry, rm = int(ym[:4]), int(ym[4:])
        arrT, arrA, det = {}, {}, {}
        for sid, cym in cohort_sellers.items():
            arr = frozen_arr.get(sid)   # HIT2 sellers: ARR frozen at their conversion Friday
            if arr is None:
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
            # qualified = HIT2 achieved >= HIT2 target AND ARR achieved >= 85% of ARR target
            hit2_ok = (h2t_v is not None) and (h2a_v >= h2t_v)
            arr_ok = (aT > 0) and (aA >= 0.85 * aT)
            return {'name': name, 'hit2T': h2t_v, 'hit2A': h2a_v, 'arrT': aT, 'arrA': aA,
                    'delta': max(0, round(0.85 * aT - aA)), 'n': nrec,
                    'hit2Ok': hit2_ok, 'arrOk': arr_ok, 'qualified': hit2_ok and arr_ok}
        gl_rows = [mkrow(g, arrT.get(g, 0), arrA.get(g, 0), h2t.get(g), h2A.get(g, 0), len(det.get(g, []))) for g in gls]
        for row in gl_rows:
            gg = gl_goog.get(row['name'], {'acc': 0, 'glive': 0, 'gspend': 0})
            row.update(goog_fields(gg['acc'], gg['glive'], gg['gspend']))
        gl_detail = {g: sorted(det.get(g, []), key=lambda x: -x['arr']) for g in gls}
        gl_hit2 = {g: h2det.get(g, []) for g in gls}
        # GM rollup
        gT, gA, gh2T, gh2A, gdet, ghit2 = {}, {}, {}, {}, {}, {}
        gGoog = {}
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
            gg = gl_goog.get(g, {'acc': 0, 'glive': 0, 'gspend': 0})
            acc = gGoog.setdefault(gm, {'acc': 0, 'glive': 0, 'gspend': 0})
            acc['acc'] += gg['acc']; acc['glive'] += gg['glive']; acc['gspend'] += gg['gspend']
        gms = sorted(gT)
        gm_rows = [mkrow(gm, gT.get(gm, 0), gA.get(gm, 0), gh2T.get(gm) or None, gh2A.get(gm, 0), len(gdet.get(gm, []))) for gm in gms]
        for row in gm_rows:
            gg = gGoog.get(row['name'], {'acc': 0, 'glive': 0, 'gspend': 0})
            row.update(goog_fields(gg['acc'], gg['glive'], gg['gspend']))
        gm_detail = {gm: sorted(gdet.get(gm, []), key=lambda x: -x['arr']) for gm in gms}
        gm_hit2 = {gm: ghit2.get(gm, []) for gm in gms}
        return {'byGL': {'rows': gl_rows, 'detail': gl_detail, 'hit2': gl_hit2},
                'byGM': {'rows': gm_rows, 'detail': gm_detail, 'hit2': gm_hit2}}

    by_month = {ym: compute_tva(ym) for ym in months}
    print(f'[cohort] TVA months {months} · report {report_ym} · {len(gls)} GLs')

    cohort = {'mcols': mcols, 'target': target, 'rows': cohort_rows, 'detail': cohort_detail,
              'reportMonth': report_ym,
              'tva': {'months': months, 'reportMonth': report_ym, 'byMonth': by_month}}
    # Guard: if the GL universe is empty (Google Sheets unreachable, e.g. sandboxed local run),
    # do NOT clobber a good previously-generated cohort/TvA — reuse the prior bev_data.json cohort.
    if not gls:
        prev_cohort = (load('bev_data.json') if os.path.exists(os.path.join(REPO, 'bev_data.json')) else {}).get('cards', {}).get('cohort') if os.path.exists(os.path.join(REPO, 'bev_data.json')) else None
        if prev_cohort and prev_cohort.get('tva', {}).get('byMonth'):
            any_rows = any(mm.get('byGL', {}).get('rows') for mm in prev_cohort['tva']['byMonth'].values())
            if any_rows:
                print('[cohort] sheets unavailable -> preserving previous cohort/TvA (no clobber)')
                cohort = prev_cohort

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

    # Weekly 1k-5k metrics for the table under ARR Cohort (1k-5k): HIT1 / HIT2 / HIT1+HIT2 toggle.
    # HIT1 = card 11115, HIT2 = card 11727, HIT1+HIT2 = card 11740 (identical column schema).
    def _wnum(v):
        try: return round(float(v), 2)
        except (TypeError, ValueError): return None
    def _weekly_rows(cid):
        out = []
        for _r in sorted(req(f"{url}/api/card/{cid}/query/json", 'POST', {}, H),
                         key=lambda x: -(int(x.get('year_week') or 0))):
            out.append({
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
        return out
    weekly_by_hit = {'hit1': [], 'hit2': [], 'both': []}
    for _kk, _cid in (('hit1', 11115), ('hit2', 11727), ('both', 11740)):
        try:
            weekly_by_hit[_kk] = _weekly_rows(_cid)
            print(f"[bev] weekly 1k-5k {_kk} (card {_cid}): {len(weekly_by_hit[_kk])} weeks")
        except Exception as _e:
            print(f"[bev] weekly 1k-5k {_kk} (card {_cid}) failed: {_e}")
    weekly_1k5k = weekly_by_hit['hit1']  # backward-compat (default view)

    # Google Metrics Benchmarking (card 11576): metric-wise rows, columns W0..W10.
    # 4 groups of 4 metrics for grouped line charts + adjacent numbers.
    google_wk = None
    try:
        g11576 = req(f"{url}/api/card/11576/query/json", 'POST', {}, H)
        by_metric = {str(r.get('metric') or '').strip(): r for r in g11576}
        weeks = ['W%d' % i for i in range(11)]
        GW_MAP = [
            ('Seller Count', [('bm_seller_count', 'Benchmark Seller Count'), ('bm_seller_count_active', 'Benchmark Active Seller Count'), ('seller_count', 'Seller Count'), ('seller_count_active', 'Active Seller Count')]),
            ('RTO %', [('bm_rto', 'Benchmark RTO'), ('bm_rto_active', 'Benchmark Active RTO'), ('rto', 'RTO'), ('rto_active', 'Active RTO')]),
            ('Spend / GMV', [('bm_s/gmv', 'Benchmark Spend/GMV'), ('bm_s/gmv_active', 'Benchmark Spend/GMV Active'), ('s/gmv', 'Spend/GMV'), ('s/gmv_active', 'Active Spend/GMV')]),
            ('Spend', [('bm_spend', 'Benchmark Spend'), ('bm_spend_active', 'Benchmark Active Spend'), ('spend', 'Spend'), ('spend_active', 'Active Spend')]),
        ]
        def _gnum(v):
            try: return round(float(v), 2)
            except (TypeError, ValueError): return None
        groups = []
        for title, mets in GW_MAP:
            series = [{'label': label, 'vals': [_gnum(by_metric.get(key, {}).get(wk)) for wk in weeks]} for key, label in mets]
            groups.append({'title': title, 'series': series})
        google_wk = {'weeks': weeks, 'groups': groups}
        print(f"[bev] google metrics benchmarking (card 11576): {len(g11576)} metrics -> {len(groups)} groups")
    except Exception as _e:
        print(f"[bev] card 11576 failed: {_e}")

    # Per-seller detail behind the chart: derived from 11576's own bm_weekly / test_weekly CTEs,
    # run ad-hoc (export endpoint, no row cap) so drill-downs reconcile exactly to 11576.
    try:
        c76 = req(f"{url}/api/card/11576", 'GET', None, H)
        _st = c76['dataset_query']['stages'][0]; _nat = _st['native']; _tt = _st['template-tags']
        _base = _nat[:_nat.find('/* ── Final pivot')]
        _sel = ("SELECT 'benchmark' AS cohort, seller_id, week_rel, rto_percentage, awb_nc, ABS(total_marketing_spend) AS spend, total_orders_gmv AS gmv, is_active FROM bm_weekly"
                " UNION ALL "
                "SELECT 'cohort' AS cohort, seller_id, week_rel, rto_percentage, awb_nc, ABS(total_marketing_spend) AS spend, total_orders_gmv AS gmv, is_active FROM test_weekly")
        _dq = {'database': c76['dataset_query'].get('database', 6), 'type': 'native', 'native': {'query': _base + _sel, 'template-tags': _tt}}
        _payload = urllib.parse.urlencode({'query': json.dumps(_dq)}).encode()
        _dreq = urllib.request.Request(f"{url}/api/dataset/json", data=_payload, method='POST',
                                       headers={**AUTH, 'Content-Type': 'application/x-www-form-urlencoded'})
        det_rows = json.loads(urllib.request.urlopen(_dreq, timeout=600).read())
        acc = {'benchmark': defaultdict(list), 'cohort': defaultdict(list)}
        for r in det_rows:
            ch = str(r.get('cohort') or '').strip()
            if ch not in acc:
                continue
            sid = str(r.get('seller_id') or '').strip()
            try:
                wk = int(r.get('week_rel'))
            except (TypeError, ValueError):
                continue
            if wk < 0 or wk > 10:
                continue
            acc[ch][wk].append([sid,
                                (round(fnum(r.get('rto_percentage')), 2) if r.get('rto_percentage') is not None else None),
                                round(fnum(r.get('awb_nc'))), round(fnum(r.get('spend'))), round(fnum(r.get('gmv'))),
                                1 if int(r.get('is_active') or 0) == 1 else 0])
        detail = {ch: {str(wk): acc[ch][wk] for wk in acc[ch]} for ch in acc}
        if google_wk is None:
            google_wk = {'weeks': ['W%d' % i for i in range(11)], 'groups': []}
        google_wk['sellerDetail'] = detail
        google_wk['detailCols'] = ['seller_id', 'rto_%', 'awb_nc', 'spend', 'gmv', 'active']
        print(f"[bev] 11576 seller detail: {len(det_rows)} rows · benchmark wks={len(detail['benchmark'])} cohort wks={len(detail['cohort'])}")
    except Exception as _e:
        print(f"[bev] 11576 seller detail failed ({_e}) -> reuse previous seller detail")
        try:
            prev_gw = (load('bev_data.json').get('cards', {}) or {}).get('googleWk') or {}
            if google_wk is not None and prev_gw.get('sellerDetail'):
                google_wk['sellerDetail'] = prev_gw['sellerDetail']
                google_wk['detailCols'] = prev_gw.get('detailCols')
        except Exception:
            pass

    # ============================================================================
    # bev2: expanded trackers (see BirdEyeView spec). Real data where available;
    # everything else the UI renders as a "data awaiting" sample.
    # ============================================================================
    def isoweek(dstr):
        try:
            y, w, _ = datetime.date.fromisoformat(dstr).isocalendar()
            return f"{y}-W{w:02d}"
        except ValueError:
            return None

    # ---- card 11011: PNL W-1/-2/-3 + weekly spend (keyed by seller) ----
    pnl11 = {}
    try:
        for r in req(f"{url}/api/card/11011/query/json", 'POST', {}, H):
            sid = str(r.get('seller_id') or '').strip()
            if not sid:
                continue
            pnl11[sid] = {'w1p': fnum(r.get('w1_pnl')), 'w2p': fnum(r.get('w2_pnl')), 'w3p': fnum(r.get('w3_pnl')),
                          'w1s': fnum(r.get('w1_spend')), 'w2s': fnum(r.get('w2_spend')), 'w3s': fnum(r.get('w3_spend')),
                          'src': str(r.get('best_source') or '')}
        print(f"[bev2] card 11011: {len(pnl11)} sellers")
    except Exception as _e:
        print(f"[bev2] card 11011 failed: {_e}")

    # channel sets within 1k-5k
    google_sids = {sid for sid in sids if str(rec(sid).get('ga') or '')}

    # ---- card 5207 (Google Seller PNL, per-seller, weekly): Google PNL + spend ----
    # spend = |total_marketing_spend_without_tax| * 1.18 ; pnl = net_profit_percentage.
    # w-1 = latest completed week, w-2 = prior. Queried per google seller.
    def q5207(sid):
        body = {'parameters': [{'type': 'string/=', 'target': ['variable', ['template-tag', 'seller_id']], 'value': sid}]}
        return req(f"{url}/api/card/5207/query/json", 'POST', body, H)
    gpnl = {}
    todayISO = today.isoformat()
    for i, sid in enumerate(sorted(google_sids)):
        try:
            rows = [r for r in q5207(sid) if str(r.get('week_end_date') or '')[:10] and str(r.get('week_end_date'))[:10] < todayISO]
        except Exception as _e:
            continue
        rows.sort(key=lambda r: str(r.get('week_start_date') or ''), reverse=True)
        if not rows:
            continue
        def wk(r):
            return {'p': fnum(r.get('net_profit_percentage')), 's': abs(fnum(r.get('total_marketing_spend_without_tax'))) * 1.18}
        w1 = wk(rows[0]); w2 = wk(rows[1]) if len(rows) > 1 else {'p': 0.0, 's': 0.0}
        gpnl[sid] = {'w1p': w1['p'], 'w1s': w1['s'], 'w2p': w2['p'], 'w2s': w2['s'],
                     'w1w': f"{int(rows[0].get('week_year'))}-W{int(rows[0].get('week_number')):02d}"}
    print(f"[bev2] card 5207 Google PNL: {len(gpnl)}/{len(google_sids)} google sellers with weekly data")

    # ---- (1) HIT2 count month-on-month ----
    from collections import Counter as _C2
    def mon_sort_key(m):
        p = m.split(); return (int(p[1]), MON3.index(p[0]) + 1)
    hit2_mom_counts = _C2(x['mon'] for x in hit2_detail)
    hit2_mom = [{'k': m, 'v': hit2_mom_counts[m]} for m in sorted(hit2_mom_counts, key=mon_sort_key)]

    # ---- HIT1 -> HIT2 conversion cohort (1k-5k) : rows = HIT1 month, cols = M0..M5 conversion % ----
    # Population = sellers on the HITS team OR who reached HIT2 (converters leave the HITS team),
    # grouped by their HIT1 month (hit_month/hit_year). M{age} = HIT2 conversions at (hit2 - hit1) months.
    H2_TARGET_VEC = {0: 0, 1: 9, 2: 15, 3: 4, 4: 5, 5: 0}   # HIT2 per-age target % (from plan)
    GHIT_TARGET_VEC = {0: 10, 1: 10, 2: 10, 3: 10, 4: 5, 5: 0}  # Google-HIT per-age target %
    H2_MCOLS = ['M0', 'M1', 'M2', 'M3', 'M4', 'M5']
    H2_GRAND_TARGET = sum(H2_TARGET_VEC.values())
    GHIT_GRAND_TARGET = sum(GHIT_TARGET_VEC.values())
    def _ymv(y, m):
        try: return int(y) * 100 + int(m)
        except (TypeError, ValueError): return None
    cur_ym = today.year * 100 + today.month
    h2coh = {}  # ym -> {'n':int, 'sellers':[], 'cells':{age:[detail]}}
    for r in hitrows:
        if str(r.get('good_seller')).strip() in ('1', '1.0', 'True', 'true'):
            continue   # exclude good sellers — 1k-5k funnel only
        is_h = str(r.get('team') or '').strip().upper() == 'HITS' or str(r.get('hit2')).strip() in ('1', '1.0', 'True', 'true')
        if not is_h:
            continue
        h1 = _ymv(r.get('hit_year'), r.get('hit_month'))
        if not h1 or h1 < 202602:   # cohorts start Feb-26
            continue
        sid = str(r.get('seller_id') or '').strip()
        nm = str(r.get('seller_name') or '')
        c = h2coh.setdefault(h1, {'n': 0, 'sellers': [], 'cells': defaultdict(list)})
        c['n'] += 1
        c['sellers'].append({'s': sid, 'n': nm})
        if str(r.get('hit2')).strip() in ('1', '1.0', 'True', 'true'):
            h2 = _ymv(r.get('hit2_year'), r.get('hit2_month'))
            if h2:
                age = max(0, (h2 // 100 - h1 // 100) * 12 + (h2 % 100 - h1 % 100))
                if age < len(H2_MCOLS):
                    c['cells'][age].append({'s': sid, 'n': nm, 'hit1': '%d-%02d' % (h1 // 100, h1 % 100), 'hit2': '%d-%02d' % (h2 // 100, h2 % 100), 'age': 'M%d' % age})
    MON3b = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
    hit2_cohort_rows = []
    for ym in sorted(h2coh):
        c = h2coh[ym]; n = c['n']
        maturity = min(len(H2_MCOLS) - 1, (cur_ym // 100 - ym // 100) * 12 + (cur_ym % 100 - ym % 100))
        cells_pct = {('M%d' % a): (round(len(c['cells'][a]) / n * 100) if n else 0) for a in range(len(H2_MCOLS))}
        cells_cnt = {('M%d' % a): len(c['cells'][a]) for a in range(len(H2_MCOLS))}
        conv = sum(len(v) for v in c['cells'].values())
        grand = round(conv / n * 100) if n else 0
        tgt = sum(H2_TARGET_VEC.get(a, 0) for a in range(0, maturity + 1))
        hit2_cohort_rows.append({
            'ym': '%d-%02d' % (ym // 100, ym % 100),
            'label': MON3b[ym % 100 - 1] + '-' + str(ym // 100)[2:],
            'n': n, 'cells': cells_pct, 'counts': cells_cnt, 'conv': conv, 'grand': grand,
            'target': tgt, 'delta': grand - tgt, 'maturity': maturity,
            'detail': {('M%d' % a): c['cells'][a] for a in range(len(H2_MCOLS)) if c['cells'][a]},
            'sellers': c['sellers'],
        })
    hit2_cohort = {'mcols': H2_MCOLS, 'targetVec': {('M%d' % a): H2_TARGET_VEC.get(a, 0) for a in range(len(H2_MCOLS))},
                   'grandTarget': H2_GRAND_TARGET, 'rows': hit2_cohort_rows}
    print(f"[bev2] HIT1->HIT2 cohort: {len(hit2_cohort_rows)} cohort months")

    # ---- HIT1 -> Google HIT conversion cohort (1k-5k): same population/method, conversion = Google HIT ----
    # Google HIT month per seller from card 9104 (google_hit_data / google bhag).
    g_hit_month = {}
    try:
        for r in req(f"{url}/api/card/9104/query/json", 'POST', {}, H):
            sid = str(r.get('seller_id') or '').strip()
            mv = _ymv(str(r.get('hit_month'))[:4], str(r.get('hit_month'))[4:6]) if r.get('hit_month') is not None else None
            if sid and mv and (sid not in g_hit_month or mv < g_hit_month[sid]):
                g_hit_month[sid] = mv
        print(f"[bev2] card 9104 Google HIT: {len(g_hit_month)} sellers")
    except Exception as _e:
        print(f"[bev2] card 9104 failed: {_e}")
    ghcoh = {}
    for r in hitrows:
        if str(r.get('good_seller')).strip() in ('1', '1.0', 'True', 'true'):
            continue
        if not (str(r.get('team') or '').strip().upper() == 'HITS' or str(r.get('hit2')).strip() in ('1', '1.0', 'True', 'true')):
            continue
        h1 = _ymv(r.get('hit_year'), r.get('hit_month'))
        if not h1 or h1 < 202602:
            continue
        sid = str(r.get('seller_id') or '').strip()
        nm = str(r.get('seller_name') or '')
        c = ghcoh.setdefault(h1, {'n': 0, 'sellers': [], 'cells': defaultdict(list)})
        c['n'] += 1
        c['sellers'].append({'s': sid, 'n': nm})
        gm = g_hit_month.get(sid)
        if gm and gm >= h1:
            age = (gm // 100 - h1 // 100) * 12 + (gm % 100 - h1 % 100)
            if 0 <= age < len(H2_MCOLS):
                c['cells'][age].append({'s': sid, 'n': nm, 'hit1': '%d-%02d' % (h1 // 100, h1 % 100), 'hit2': '%d-%02d' % (gm // 100, gm % 100), 'age': 'M%d' % age})
    ghc_rows = []
    for ym in sorted(ghcoh):
        c = ghcoh[ym]; n = c['n']
        maturity = min(len(H2_MCOLS) - 1, (cur_ym // 100 - ym // 100) * 12 + (cur_ym % 100 - ym % 100))
        conv = sum(len(v) for v in c['cells'].values())
        grand = round(conv / n * 100) if n else 0
        tgt = sum(GHIT_TARGET_VEC.get(a, 0) for a in range(0, maturity + 1))
        ghc_rows.append({
            'ym': '%d-%02d' % (ym // 100, ym % 100), 'label': MON3b[ym % 100 - 1] + '-' + str(ym // 100)[2:],
            'n': n, 'cells': {('M%d' % a): (round(len(c['cells'][a]) / n * 100) if n else 0) for a in range(len(H2_MCOLS))},
            'counts': {('M%d' % a): len(c['cells'][a]) for a in range(len(H2_MCOLS))}, 'conv': conv, 'grand': grand,
            'target': tgt, 'delta': grand - tgt, 'maturity': maturity,
            'detail': {('M%d' % a): c['cells'][a] for a in range(len(H2_MCOLS)) if c['cells'][a]}, 'sellers': c['sellers'],
        })
    google_hit_cohort = {'mcols': H2_MCOLS, 'targetVec': {('M%d' % a): GHIT_TARGET_VEC.get(a, 0) for a in range(len(H2_MCOLS))},
                         'grandTarget': GHIT_GRAND_TARGET, 'rows': ghc_rows}
    print(f"[bev2] HIT1->Google HIT cohort: {len(ghc_rows)} cohort months")

    # ---- Google golive cohorts (toggle HIT1/HIT2/HIT1+HIT2/Revenue) + Google-HIT conversion ----
    # golive % = google golives at age M0/M1/M2 after the cohort reference month, / cohort size.
    # Reference month: HIT1 = hit1 month, HIT2 = hit2 month, Revenue = first any-spend month (card 11852).
    GG_MCOLS = ['M0', 'M1', 'M2', 'M3', 'M4']
    gg_month = {}   # seller -> first google-golive month (card 11850, all universes)
    try:
        for r in req(f"{url}/api/card/11850/query/json", 'POST', {}, H):
            sid = str(r.get('seller_id') or '').strip(); gm = r.get('google_golive_month')
            if sid and gm:
                try: gg_month[sid] = int(str(gm)[:4]) * 100 + int(str(gm)[5:7])
                except (ValueError, TypeError): pass
        print(f"[bev2] card 11850 google golive: {len(gg_month)} sellers")
    except Exception as _e:
        print(f"[bev2] card 11850 failed: {_e}")
    first_spend = {}   # seller -> first any-spend month (card 11852, Revenue reference)
    try:
        for r in req(f"{url}/api/card/11852/query/json", 'POST', {}, H):
            sid = str(r.get('seller_id') or '').strip(); fm = r.get('first_spend_month')
            if sid and fm:
                try: first_spend[sid] = int(str(fm)[:4]) * 100 + int(str(fm)[5:7])
                except (ValueError, TypeError): pass
        print(f"[bev2] card 11852 first spend: {len(first_spend)} sellers")
    except Exception as _e:
        print(f"[bev2] card 11852 failed: {_e}")

    _name_by = {str(r.get('seller_id') or '').strip(): str(r.get('seller_name') or '') for r in hitrows}
    _tv = ('1', '1.0', 'True', 'true')

    def build_golive_cohort(pairs, min_ym):
        coh = {}
        for sid, ref, nm in pairs:
            if not ref or ref < min_ym:
                continue
            c = coh.setdefault(ref, {'n': 0, 'sellers': [], 'cells': defaultdict(list)})
            c['n'] += 1; c['sellers'].append({'s': sid, 'n': nm})
            gm = gg_month.get(sid)
            if gm and gm >= ref:
                age = (gm // 100 - ref // 100) * 12 + (gm % 100 - ref % 100)
                if 0 <= age < len(GG_MCOLS):
                    c['cells'][age].append({'s': sid, 'n': nm, 'hit1': '%d-%02d' % (ref // 100, ref % 100),
                                            'hit2': '%d-%02d' % (gm // 100, gm % 100), 'age': 'M%d' % age})
        yms = sorted(coh); rows = []
        for idx, ym in enumerate(yms):
            c = coh[ym]; n = c['n']; conv = sum(len(v) for v in c['cells'].values())
            grand = round(conv / n * 100) if n else 0
            rank = len(yms) - 1 - idx   # 0 = latest
            tgt = 55 if rank == 0 else 65 if rank == 1 else 70   # rolling target
            maturity = min(len(GG_MCOLS) - 1, (cur_ym // 100 - ym // 100) * 12 + (cur_ym % 100 - ym % 100))
            rows.append({
                'ym': '%d-%02d' % (ym // 100, ym % 100), 'label': MON3b[ym % 100 - 1] + '-' + str(ym // 100)[2:],
                'n': n, 'golives': conv,
                'cells': {('M%d' % a): (round(len(c['cells'][a]) / n * 100) if n else 0) for a in range(len(GG_MCOLS))},
                'counts': {('M%d' % a): len(c['cells'][a]) for a in range(len(GG_MCOLS))},
                'grand': grand, 'target': tgt, 'delta': grand - tgt, 'maturity': maturity,
                'detail': {('M%d' % a): c['cells'][a] for a in range(len(GG_MCOLS)) if c['cells'][a]}, 'sellers': c['sellers'],
            })
        return {'mcols': GG_MCOLS, 'targetVec': {m: 0 for m in GG_MCOLS}, 'grandTarget': 0, 'rows': rows}

    hit1_pairs, hit2_pairs, rev_pairs, hit1_sids = [], [], [], set()
    for r in hitrows:
        sid = str(r.get('seller_id') or '').strip()
        if not sid:
            continue
        nm = _name_by.get(sid, '')
        good = str(r.get('good_seller')).strip() in _tv
        h2 = str(r.get('hit2')).strip() in _tv
        hits = str(r.get('team') or '').strip().upper() == 'HITS'
        h1m = _ymv(r.get('hit_year'), r.get('hit_month'))
        h2m = _ymv(r.get('hit2_year'), r.get('hit2_month'))
        if not good and (hits or h2) and h1m:
            hit1_pairs.append((sid, h1m, nm)); hit1_sids.add(sid)
        if h2 and h2m:
            hit2_pairs.append((sid, h2m, nm))
        if not good and not hits and not h2 and h1m:   # Revenue = in 10453 non-HIT/good, by HIT month
            rev_pairs.append((sid, h1m, nm))
    google_golive_toggle = {
        'hit1': build_golive_cohort(hit1_pairs, 202602),
        'hit2': build_golive_cohort(hit2_pairs, 202601),
        'both': build_golive_cohort(hit1_pairs + hit2_pairs, 202601),
        'revenue': build_golive_cohort(rev_pairs, 202601),
    }
    google_golive_cohort = google_golive_toggle['hit1']   # backward-compat
    print("[bev2] golive toggle months: hit1=%d hit2=%d both=%d rev=%d" % tuple(
        len(google_golive_toggle[k]['rows']) for k in ('hit1', 'hit2', 'both', 'revenue')))

    # ---- Google-HIT conversion cohorts (toggle HIT1/HIT2/HIT1+HIT2/Revenue) ----
    # Reusable builder: pairs=[(sid, ref_ym, name)], conv_map=sid->conversion month; % = conv at age / cohort size.
    HCV_MCOLS = ['M0', 'M1', 'M2', 'M3', 'M4']
    def build_conv_cohort(pairs, conv_map, mcols, target_vec, min_ym, fill=False):
        coh = {}
        for sid, ref, nm in pairs:
            if not ref or ref < min_ym:
                continue
            c = coh.setdefault(ref, {'n': 0, 'sellers': [], 'cells': defaultdict(list)})
            c['n'] += 1; c['sellers'].append({'s': sid, 'n': nm})
            cm = conv_map.get(sid)
            if cm and cm >= ref:
                age = (cm // 100 - ref // 100) * 12 + (cm % 100 - ref % 100)
                if 0 <= age < len(mcols):
                    c['cells'][age].append({'s': sid, 'n': nm, 'hit1': '%d-%02d' % (ref // 100, ref % 100),
                                            'hit2': '%d-%02d' % (cm // 100, cm % 100), 'age': 'M%d' % age})
        iter_yms = sorted(coh)
        if fill and iter_yms:   # fill month gaps (e.g. a month with 0 golives) so the range is contiguous
            _lo = iter_yms[0]; _y, _mo = _lo // 100, _lo % 100; iter_yms = []
            while _y * 100 + _mo <= cur_ym:
                iter_yms.append(_y * 100 + _mo)
                _mo += 1
                if _mo > 12:
                    _mo = 1; _y += 1
        rows = []
        for ym in iter_yms:
            if ym not in coh:
                coh[ym] = {'n': 0, 'sellers': [], 'cells': defaultdict(list)}
            c = coh[ym]; n = c['n']; conv = sum(len(v) for v in c['cells'].values())
            grand = round(conv / n * 100) if n else 0
            maturity = min(len(mcols) - 1, (cur_ym // 100 - ym // 100) * 12 + (cur_ym % 100 - ym % 100))
            tgt = sum(target_vec.get(a, 0) for a in range(0, maturity + 1))
            rows.append({
                'ym': '%d-%02d' % (ym // 100, ym % 100), 'label': MON3b[ym % 100 - 1] + '-' + str(ym // 100)[2:],
                'n': n, 'golives': n,
                'cells': {('M%d' % a): (round(len(c['cells'][a]) / n * 100) if n else 0) for a in range(len(mcols))},
                'counts': {('M%d' % a): len(c['cells'][a]) for a in range(len(mcols))},
                'grand': grand, 'target': tgt, 'delta': grand - tgt, 'maturity': maturity,
                'detail': {('M%d' % a): c['cells'][a] for a in range(len(mcols)) if c['cells'][a]}, 'sellers': c['sellers'],
            })
        return {'mcols': mcols, 'targetVec': {('M%d' % a): target_vec.get(a, 0) for a in range(len(mcols))},
                'grandTarget': sum(target_vec.get(a, 0) for a in range(len(mcols))), 'rows': rows}

    _hit2_sids = set(p[0] for p in hit2_pairs)
    _both_sids = hit1_sids | _hit2_sids
    _rev_sids = set(p[0] for p in rev_pairs)
    def _golive_pairs(sids):
        return [(sid, gg_month.get(sid), _name_by.get(sid, '')) for sid in sids if gg_month.get(sid)]

    # Task 2: Google-HIT conversion by HIT month (cohort ref = HIT/HIT2/first month) x google-HIT age
    google_hit_cohort_toggle = {
        'hit1': build_conv_cohort(hit1_pairs, g_hit_month, H2_MCOLS, GHIT_TARGET_VEC, 202602),
        'hit2': build_conv_cohort(hit2_pairs, g_hit_month, H2_MCOLS, GHIT_TARGET_VEC, 202601),
        'both': build_conv_cohort(hit1_pairs + hit2_pairs, g_hit_month, H2_MCOLS, GHIT_TARGET_VEC, 202601),
        'revenue': build_conv_cohort(rev_pairs, g_hit_month, H2_MCOLS, GHIT_TARGET_VEC, 202601),
    }
    google_hit_cohort = google_hit_cohort_toggle['hit1']   # backward-compat binding

    # Task 3: Google-HIT conversion of Google-live sellers, cohort by GOLIVE month x google-HIT age
    google_hit_conv_toggle = {
        'hit1': build_conv_cohort(_golive_pairs(hit1_sids), g_hit_month, HCV_MCOLS, GHIT_TARGET_VEC, 202601, fill=True),
        'hit2': build_conv_cohort(_golive_pairs(_hit2_sids), g_hit_month, HCV_MCOLS, GHIT_TARGET_VEC, 202601, fill=True),
        'both': build_conv_cohort(_golive_pairs(_both_sids), g_hit_month, HCV_MCOLS, GHIT_TARGET_VEC, 202601, fill=True),
        'revenue': build_conv_cohort(_golive_pairs(_rev_sids), g_hit_month, HCV_MCOLS, GHIT_TARGET_VEC, 202601, fill=True),
    }
    google_hit_conv = google_hit_conv_toggle['hit1']
    print("[bev2] hit-conv toggles: hitMonth hit1=%d months · golive hit1=%d months" % (
        len(google_hit_cohort_toggle['hit1']['rows']), len(google_hit_conv_toggle['hit1']['rows'])))

    # ---- ARR cohort matrices (1k-5k) by channel (meta/google/both), 3 populations ----
    # rows = HIT1 month, M{age} = avg ARR/seller at that cohort age. Channel ARR from card 10892
    # (currently ~30d window; will deepen to 6 months later). Populations: HIT1+HIT2, HIT1-only, HIT2-only.
    ARR_MCOLS = ['M0', 'M1', 'M2', 'M3', 'M4', 'M5', 'M6']
    arr_target = cohort.get('target', {})  # per-age ARR target from card 11020 (reuse existing cohort targets)
    # HIT1 month, HIT2 flag, name — from hitrows (10453), 1k-5k = HITS or converted, exclude good sellers
    hit1_of, is_hit2_of, name_of = {}, {}, {}
    for r in hitrows:
        if str(r.get('good_seller')).strip() in ('1', '1.0', 'True', 'true'):
            continue
        if not (str(r.get('team') or '').strip().upper() == 'HITS' or str(r.get('hit2')).strip() in ('1', '1.0', 'True', 'true')):
            continue
        sid = str(r.get('seller_id') or '').strip()
        h1 = _ymv(r.get('hit_year'), r.get('hit_month'))
        if not sid or not h1 or h1 < 202602:
            continue
        hit1_of[sid] = h1
        is_hit2_of[sid] = str(r.get('hit2')).strip() in ('1', '1.0', 'True', 'true')
        name_of[sid] = str(r.get('seller_name') or '')
    # per-seller per-month avg channel ARR from card 10469 (Meta/Google/All ARR, ~6-month history)
    smon = {}  # sid -> monthKey(int YYYYMM) -> {'m':sum,'g':sum,'t':sum,'d':days}
    arr_window = {'from': '', 'to': ''}
    try:
        rows10469 = get10469()   # reuse the single shared fetch from the TvA block
        ad = []
        for r in rows10469:
            sid = str(r.get('seller_id') or '').strip()
            if sid not in hit1_of:
                continue
            am, ag, at = r.get('arr_meta'), r.get('arr_google'), r.get('arr_overall')
            if am is None and ag is None and at is None:
                continue
            ds = str(r.get('date') or '')[:10]
            if not ds:
                continue
            ad.append(ds)
            mk = int(ds[:4]) * 100 + int(ds[5:7])
            cell = smon.setdefault(sid, {}).setdefault(mk, {'m': 0.0, 'g': 0.0, 't': 0.0, 'd': 0})
            cell['m'] += fnum(am); cell['g'] += fnum(ag); cell['t'] += fnum(at); cell['d'] += 1
        if ad:
            arr_window = {'from': min(ad), 'to': max(ad)}
        print(f"[bev2] ARR cohort source card 10469: {len(smon)} cohort sellers with ARR · window {arr_window}")
    except Exception as _e:
        print(f"[bev2] card 10469 failed ({_e}) -> ARR cohort empty")

    def build_arr_variant(member):
        # cohort sizes per hit1 month
        coh_n = defaultdict(int)
        for sid, h1 in hit1_of.items():
            if member(sid):
                coh_n[h1] += 1
        rows = []
        for h1 in sorted(coh_n):
            n = coh_n[h1]
            # accumulate per age per channel
            acc = {a: {'m': 0.0, 'g': 0.0, 't': 0.0, 'det': []} for a in range(len(ARR_MCOLS))}
            for sid, sh1 in hit1_of.items():
                if sh1 != h1 or not member(sid):
                    continue
                for mk, v in smon.get(sid, {}).items():
                    age = (mk // 100 - h1 // 100) * 12 + (mk % 100 - h1 % 100)
                    if 0 <= age < len(ARR_MCOLS) and v['d']:
                        mm, gg, tt = v['m'] / v['d'], v['g'] / v['d'], v['t'] / v['d']
                        acc[age]['m'] += mm; acc[age]['g'] += gg; acc[age]['t'] += tt
                        acc[age]['det'].append({'s': sid, 'n': name_of.get(sid, ''), 'meta': round(mm), 'google': round(gg), 'both': round(tt)})
            cells = {ch: {} for ch in ('meta', 'google', 'both')}
            counts, detail = {}, {}
            for a in range(len(ARR_MCOLS)):
                mc = 'M%d' % a
                has = bool(acc[a]['det'])
                cells['meta'][mc] = round(acc[a]['m'] / n) if (n and has) else None
                cells['google'][mc] = round(acc[a]['g'] / n) if (n and has) else None
                cells['both'][mc] = round(acc[a]['t'] / n) if (n and has) else None
                counts[mc] = len(acc[a]['det'])
                if has:
                    detail[mc] = sorted(acc[a]['det'], key=lambda x: -x['both'])
            rows.append({'ym': '%d-%02d' % (h1 // 100, h1 % 100), 'label': MON3b[h1 % 100 - 1] + '-' + str(h1 // 100)[2:],
                         'n': n, 'cells': cells, 'counts': counts, 'detail': detail})
        return rows
    arr_cohort = {
        'mcols': ARR_MCOLS, 'target': {mc: arr_target.get(mc) for mc in ARR_MCOLS},
        'window': arr_window,
        'variants': {
            'hit12': build_arr_variant(lambda s: True),
            'hit1': build_arr_variant(lambda s: not is_hit2_of.get(s)),
            'hit2': build_arr_variant(lambda s: is_hit2_of.get(s)),
        },
    }
    print(f"[bev2] ARR cohort (channel): hit12={len(arr_cohort['variants']['hit12'])} hit1={len(arr_cohort['variants']['hit1'])} hit2={len(arr_cohort['variants']['hit2'])} rows")
    # HIT1+HIT2 combined 'both' must match the authoritative ARR cohort (card 11020, full history).
    # Use 11020 per-cell values for 'both'; split Meta/Google proportionally via the 10469 ratio.
    _ref11020 = {r['ym']: r['v'] for r in cohort.get('rows', [])}
    for _row in arr_cohort['variants']['hit12']:
        _rv = _ref11020.get(_row['ym'].replace('-', ''), {})
        for _mc in ARR_MCOLS:
            _both = _rv.get(_mc)
            _m0, _g0 = _row['cells']['meta'].get(_mc), _row['cells']['google'].get(_mc)
            if _both is None:
                _row['cells']['both'][_mc] = None; _row['cells']['meta'][_mc] = None; _row['cells']['google'][_mc] = None
            else:
                _row['cells']['both'][_mc] = _both
                if _m0 is not None and _g0 is not None and (_m0 + _g0) > 0:
                    _mm = round(_both * _m0 / (_m0 + _g0)); _row['cells']['meta'][_mc] = _mm; _row['cells']['google'][_mc] = _both - _mm
                else:
                    _row['cells']['meta'][_mc] = None; _row['cells']['google'][_mc] = None
    # Anchor HIT1-only / HIT2-only to the authoritative combined: keep 11020 total, split by the
    # 10469 proportion so (hit1*n1 + hit2*n2)/n_all reconciles to the combined value.
    _h12 = {r['ym']: r for r in arr_cohort['variants']['hit12']}
    _h1 = {r['ym']: r for r in arr_cohort['variants']['hit1']}
    _h2 = {r['ym']: r for r in arr_cohort['variants']['hit2']}
    def _shr(row, mc):
        b = row['cells']['both'].get(mc); m = row['cells']['meta'].get(mc)
        return (m / b) if (b and m is not None and b > 0) else None
    def _setc(row, mc, val):
        if row is None:
            return
        if val is None:
            row['cells']['both'][mc] = None; row['cells']['meta'][mc] = None; row['cells']['google'][mc] = None; return
        sh = _shr(row, mc); row['cells']['both'][mc] = val
        if sh is not None:
            _mm2 = round(val * sh); row['cells']['meta'][mc] = _mm2; row['cells']['google'][mc] = val - _mm2
        else:
            row['cells']['meta'][mc] = None; row['cells']['google'][mc] = None
    for _ym, _c in _h12.items():
        _r1, _r2 = _h1.get(_ym), _h2.get(_ym)
        _n1 = _r1['n'] if _r1 else 0; _n2 = _r2['n'] if _r2 else 0
        # per-cohort fallback split ratio from cells that have both sub-values (original 10469)
        _S1 = _S2 = 0.0
        for _mc in ARR_MCOLS:
            _a = _r1['cells']['both'].get(_mc) if _r1 else None
            _b = _r2['cells']['both'].get(_mc) if _r2 else None
            if _a is not None and _b is not None:
                _S1 += _a * _n1; _S2 += _b * _n2
        _r1fb = (_S1 / (_S1 + _S2)) if (_S1 + _S2) > 0 else ((_n1 / (_n1 + _n2)) if (_n1 + _n2) else 0.5)
        for _mc in ARR_MCOLS:
            _V = _c['cells']['both'].get(_mc)
            _s1 = (_r1['cells']['both'].get(_mc) * _n1) if (_r1 and _r1['cells']['both'].get(_mc) is not None) else None
            _s2 = (_r2['cells']['both'].get(_mc) * _n2) if (_r2 and _r2['cells']['both'].get(_mc) is not None) else None
            _tot = (_s1 or 0) + (_s2 or 0)
            if _V is None:
                _setc(_r1, _mc, None); _setc(_r2, _mc, None); continue
            _total = _V * (_n1 + _n2)
            if _tot <= 0:
                # combined present but no per-cell 10469 split -> use cohort fallback ratio (fills e.g. Feb M0)
                _setc(_r1, _mc, round(_total * _r1fb / _n1) if _n1 else None)
                _setc(_r2, _mc, round(_total * (1 - _r1fb) / _n2) if _n2 else None)
                continue
            _setc(_r1, _mc, round(_total * (_s1 or 0) / _tot / _n1) if _n1 else None)
            _setc(_r2, _mc, round(_total * (_s2 or 0) / _tot / _n2) if _n2 else None)
    # ---- ARR buckets: Top 20% / Mid 20% / Bottom 60% of sellers by weekly ARR (card 10469) ----
    arr_buckets = {'weeks': [], 'variants': {}}
    try:
        _wk = defaultdict(lambda: defaultdict(lambda: [0.0, 0]))  # sid -> yw -> [sumARR, days]
        for r in rows10469:
            sid = str(r.get('seller_id') or '').strip()
            if sid not in hit1_of:
                continue
            at = r.get('arr_overall')
            if at is None:
                continue
            ds = str(r.get('date') or '')[:10]
            if not ds or ds < '2026-02-01':
                continue
            _y, _w, _ = datetime.date.fromisoformat(ds).isocalendar()
            cell = _wk[sid]['%d-W%02d' % (_y, _w)]; cell[0] += fnum(at); cell[1] += 1
        seller_wk = defaultdict(dict); _weeks_set = set()
        for sid, wm in _wk.items():
            for yw, (s, dc) in wm.items():
                if dc:
                    seller_wk[sid][yw] = s / dc; _weeks_set.add(yw)
        bweeks = sorted(_weeks_set, reverse=True)
        def _bkt(pairs):
            pairs = [p for p in pairs if p[2] > 0]; pairs.sort(key=lambda x: -x[2]); n = len(pairs)
            if not n:
                return None
            t = round(n * 0.2); mid = round(n * 0.2)
            top, midb, bot = pairs[:t], pairs[t:t + mid], pairs[t + mid:]
            def _sm(g):
                return {'n': len(g), 'total': round(sum(x[2] for x in g)), 'avg': round(sum(x[2] for x in g) / len(g)) if g else 0, 'rows': [[x[0], x[1], round(x[2])] for x in g]}
            return {'top': _sm(top), 'mid': _sm(midb), 'bottom': _sm(bot)}
        def _bv(member):
            byw = {}
            for yw in bweeks:
                pr = [(sid, name_of.get(sid, ''), seller_wk[sid][yw]) for sid in seller_wk if yw in seller_wk[sid] and member(sid)]
                b = _bkt(pr)
                if b:
                    byw[yw] = b
            sp = []
            for sid in seller_wk:
                if not member(sid):
                    continue
                v = list(seller_wk[sid].values())
                if v:
                    sp.append((sid, name_of.get(sid, ''), sum(v) / len(v)))
            return {'byWeek': byw, 'since': _bkt(sp)}
        arr_buckets = {'weeks': bweeks, 'variants': {
            'hit1': _bv(lambda s: not is_hit2_of.get(s)), 'hit2': _bv(lambda s: is_hit2_of.get(s)), 'hit12': _bv(lambda s: True)}}
        print(f"[bev2] ARR buckets: {len(bweeks)} weeks")
    except Exception as _e:
        print(f"[bev2] ARR buckets failed: {_e}")

    # seller -> earliest hit2 achievement date (first of achievement month) for cumulative cohort
    hit2_ym = {}
    for x in hit2_detail:
        p = x['mon'].split(); ym = '%d%02d' % (int(p[1]), MON3.index(p[0]) + 1)
        sid = x['s']
        if sid and (sid not in hit2_ym or ym < hit2_ym[sid]):
            hit2_ym[sid] = ym

    # ---- (2,3) HIT2 ARR & Spend (Overall/Meta/Google) MoM + WoW, cumulative cohort ----
    def hit2_perf(period_of, cohort_ok):
        agg = {}
        for d in good_dates:
            pk = period_of(d)
            if not pk:
                continue
            for r in by_date[d]:
                sid = str(r.get('seller_id') or '').strip()
                cym = hit2_ym.get(sid)
                if not cym or not cohort_ok(cym, d):
                    continue
                a = agg.setdefault(pk, {'am': 0.0, 'sm': 0.0, 'ag': 0.0, 'sg': 0.0, 's': {}})
                am, sm = fnum(r.get('arr_meta')), fnum(r.get('spend_meta'))
                ag, sg = fnum(r.get('arr_google')), fnum(r.get('spend_google'))
                a['am'] += am; a['sm'] += sm; a['ag'] += ag; a['sg'] += sg
                srow = a['s'].setdefault(sid, {'n': r.get('seller_name') or seller_meta.get(sid, {}).get('n') or '', 'am': 0.0, 'sm': 0.0, 'ag': 0.0, 'sg': 0.0})
                srow['am'] += am; srow['sm'] += sm; srow['ag'] += ag; srow['sg'] += sg
        out_list = []
        for pk in sorted(agg):
            a = agg[pk]
            rows = [[s, v['n'], round(v['am']), round(v['sm']), round(v['ag']), round(v['sg'])] for s, v in a['s'].items()]
            rows.sort(key=lambda x: -(x[2] + x[4]))
            out_list.append({'k': pk, 'am': round(a['am']), 'sm': round(a['sm']), 'ag': round(a['ag']), 'sg': round(a['sg']),
                             'ao': round(a['am'] + a['ag']), 'so': round(a['sm'] + a['sg']), 'rows': rows})
        return out_list
    hit2_mom_perf = hit2_perf(lambda d: d[:7], lambda cym, d: cym <= d[:7].replace('-', ''))
    hit2_wow_perf = hit2_perf(isoweek, lambda cym, d: cym <= d[:7].replace('-', ''))

    # ---- (8) Spends for 1k-5k: Day / Week / Month (from perf_by_date, 1k-5k only) ----
    def spend_series(period_of):
        agg = {}
        for d in good_dates:
            pk = period_of(d)
            if not pk:
                continue
            p = perf_by_date[d]
            a = agg.setdefault(pk, {'sm': 0, 'sg': 0, 'so': 0})
            a['sm'] += p['sm']; a['sg'] += p['sg']; a['so'] += p['so']
        return [{'k': k, 'sm': agg[k]['sm'], 'sg': agg[k]['sg'], 'so': agg[k]['so']} for k in sorted(agg)]
    spends_1k5k = {'dod': spend_series(lambda d: d), 'wow': spend_series(isoweek), 'mom': spend_series(lambda d: d[:7])}

    # ---- (11) Current Potentials: 1k-5k, card 5206 (Facebook Seller PNL) ----
    # w-1 PNL > -5 and (w-1 spend / 7) > 3000.  spend = |marketing_spend_without_tax| * 1.18.
    def q5206(sid):
        body = {'parameters': [{'type': 'string/=', 'target': ['variable', ['template-tag', 'seller_id']], 'value': sid}]}
        return req(f"{url}/api/card/5206/query/json", 'POST', body, H)
    potentials = []
    pot_seen = 0
    for sid in sids:
        try:
            rows = [r for r in q5206(sid) if str(r.get('week_end_date') or '')[:10] and str(r.get('week_end_date'))[:10] < todayISO]
        except Exception:
            continue
        rows.sort(key=lambda r: str(r.get('week_start_date') or ''), reverse=True)
        if not rows:
            continue
        pot_seen += 1
        w1p = fnum(rows[0].get('net_profit_percentage'))
        w1s = abs(fnum(rows[0].get('total_marketing_spend_without_tax'))) * 1.18
        budget = w1s / 7.0
        if w1p > -5 and budget > 3000:
            t = rec(sid)
            ysp = round(fnum(t.get('my')) + fnum(t.get('gy')))
            potentials.append({'s': sid, 'n': team.get(sid, {}).get('n', ''), 'gc': team.get(sid, {}).get('gc', ''),
                               'gm': team.get(sid, {}).get('gm', ''), 'w1p': round(w1p, 1), 'w1s': round(w1s),
                               'budget': round(budget), 'ySpend': ysp})
    potentials.sort(key=lambda x: -x['budget'])
    print(f"[bev2] card 5206 Current Potentials: {len(potentials)} of {pot_seen} 1k-5k sellers with weekly data")

    # ---- (12) D7 Paused: 1k-5k sellers with last spend >= 7 days ago (card 10065) ----
    d7_paused = []
    for sid in sids:
        ls = last_spend.get(sid)
        if not ls or not ls.get('d'):
            continue
        try:
            lsd = datetime.date.fromisoformat(ls['d'])
        except ValueError:
            continue
        days = (today - lsd).days
        if days >= 7:
            d7_paused.append({'s': sid, 'n': ls['c'] or team.get(sid, {}).get('n', ''), 'gc': team.get(sid, {}).get('gc', ''),
                              'gm': team.get(sid, {}).get('gm', ''), 'lastSpend': ls['d'], 'days': days})
    d7_paused.sort(key=lambda x: -x['days'])

    # ---- (13) NPS month-on-month for 1k-5k ----
    nps_1k5k = []
    try:
        nps_rows = [r for r in load('nps_data.json').get('rows', []) if str(r.get('s') or '') in sids]
        bym = {}
        for r in nps_rows:
            mo = str(r.get('d') or '')[:7]
            if not mo:
                continue
            bym.setdefault(mo, []).append({'s': r['s'], 'sc': fnum(r.get('sc')), 'gc': r.get('gc', ''), 'd': r.get('d')})
        for mo in sorted(bym):
            scores = [x['sc'] for x in bym[mo]]
            n = len(scores)
            prom = sum(1 for s in scores if s >= 9); det = sum(1 for s in scores if s <= 6)
            nps_1k5k.append({'k': mo, 'n': n, 'avg': round(sum(scores) / n, 2) if n else None,
                             'nps': round((prom - det) / n * 100, 1) if n else None,
                             'rows': [[x['s'], x['gc'], x['sc'], x['d']] for x in bym[mo]]})
        print(f"[bev2] NPS 1k-5k: {len(nps_1k5k)} months, {sum(m['n'] for m in nps_1k5k)} responses")
    except Exception as _e:
        print(f"[bev2] NPS 1k-5k failed: {_e}")

    # ---- (16) ARR buckets Top 20 / Mid 60 / Bottom 20 for 1k-5k (yesterday ARR) ----
    def build_arr_buckets(pairs):   # renamed: 'arr_buckets' is the channel-buckets dict above
        pairs = [p for p in pairs if p[2] > 0]
        pairs.sort(key=lambda x: -x[2])
        n = len(pairs)
        if not n:
            return None
        t = max(1, round(n * 0.2)); b = max(1, round(n * 0.2))
        top, mid, bot = pairs[:t], pairs[t:n - b], pairs[n - b:]
        def summ(grp, label):
            return {'label': label, 'count': len(grp), 'arr': round(sum(x[2] for x in grp)),
                    'rows': [[x[0], x[1], round(x[2])] for x in grp]}
        return [summ(top, 'Top 20%'), summ(mid, 'Mid 60%'), summ(bot, 'Bottom 20%')]
    arr_1k5k_pairs = [[r[0], seller_meta.get(r[0], {}).get('n', ''), r[5]] for r in perf_by_date.get(as_of, {}).get('rows', [])]
    arr_buckets_1k5k = build_arr_buckets(arr_1k5k_pairs)

    # ---- Google 1k-5k: Bucket Health / Potentials / Objective (card 11011 + google channel) ----
    def google_bucket(pred, label):
        det = []
        for sid in google_sids:
            p = gpnl.get(sid)
            if not p:
                continue
            if pred(p):
                det.append({'s': sid, 'n': team.get(sid, {}).get('n', ''), 'gc': team.get(sid, {}).get('gc', ''),
                            'w1p': round(p['w1p'], 1), 'w2p': round(p['w2p'], 1), 'w1s': round(p['w1s']), 'w2s': round(p['w2s'])})
        det.sort(key=lambda x: -x['w1s'])
        return {'label': label, 'value': len(det), 'detail': det}
    g_bucket_health = google_bucket(lambda p: p['w1p'] > -20 and p['w1s'] > 28000, 'Google Bucket Health (w-1 PNL > -20, spend > 28k)')
    g_potentials = google_bucket(lambda p: p['w1p'] > 5 and p['w1s'] > 28000, 'Google Potentials (w-1 PNL > 5, spend > 28k)')
    g_objective = google_bucket(lambda p: p['w1p'] > 5 and p['w2p'] > 5 and (p['w1s'] + p['w2s']) > 70000, 'Google Objective (w-1 & w-2 PNL > 5, w1+w2 spend > 70k)')

    # ---- Google 1k-5k Spends & Active sellers MoM + WoW (from perf_by_date google fields) ----
    def google_series(period_of):
        agg = {}
        for d in good_dates:
            pk = period_of(d)
            if not pk:
                continue
            act = sum(1 for r in by_date[d] if str(r.get('seller_id') or '') in google_sids and fnum(r.get('spend_google')) > 1)
            a = agg.setdefault(pk, {'sg': 0, 'actSum': 0, 'days': 0})
            a['sg'] += perf_by_date[d]['sg']; a['actSum'] += act; a['days'] += 1
        return [{'k': k, 'sg': agg[k]['sg'], 'activeAvg': round(agg[k]['actSum'] / agg[k]['days'])} for k in sorted(agg)]
    google_spend_mom = google_series(lambda d: d[:7])
    google_spend_wow = google_series(isoweek)

    # ---- Spend/Live history (meta/google/blended) from dated snapshots + today ----
    import gzip
    sl_history = []
    snap_dir = os.path.join(REPO, 'snapshots')
    try:
        snap_dates = sorted(d for d in os.listdir(snap_dir) if re.match(r'\d{4}-\d{2}-\d{2}$', d))
    except OSError:
        snap_dates = []
    for sd in snap_dates:
        fp = os.path.join(snap_dir, sd, 'bev_data.json.gz')
        if not os.path.exists(fp):
            continue
        try:
            with gzip.open(fp, 'rt') as fh:
                sc2 = json.load(fh).get('cards', {})
            sl_history.append({'d': sd,
                               'meta': (sc2.get('sl_meta') or {}).get('pct'),
                               'google': (sc2.get('sl_google') or {}).get('pct'),
                               'blended': (sc2.get('sl_blended') or {}).get('pct')})
        except (OSError, ValueError):
            continue
    # append today's live point (dedupe if a snapshot already covers today)
    if not sl_history or sl_history[-1]['d'] != today.isoformat():
        sl_history.append({'d': today.isoformat(), 'meta': sl_meta_pct, 'google': sl_google_pct, 'blended': sl_blended_pct})
    print(f"[bev2] spend/live history: {len(sl_history)} points ({sl_history[0]['d'] if sl_history else '-'}..{sl_history[-1]['d'] if sl_history else '-'})")

    # ---- (17) Churn comparison HITS vs Revenue (card 11771): MoM churn month x age M0..M12 ----
    # Card 11771 = 1k-5k + revenue sellers. team_mapping is unusable (~all REVENUE), so 1k-5k (HITS)
    # is identified by hit_year_week being populated (HIT1'd); rows without it = Revenue.
    # churn month = calendar month of (last_spend_week + 21 days); age = months(golive .. last_spend).
    # Only churn_flag == 1, and only the last 6 calendar months of churn.
    churn_cmp = {'months': [], 'maxAge': 12, 'rows': {'HIT': [], 'REVENUE': []}, 'totals': {'HIT': 0, 'REVENUE': 0}}
    try:
        def _yw_mon(yw):
            s = str(yw); return datetime.date.fromisocalendar(int(s[:4]), int(s[4:6]), 1)
        _cy, _cmm = today.year, today.month - 5   # 6-month window incl. current month
        while _cmm <= 0:
            _cmm += 12; _cy -= 1
        _churn_cutoff = '%04d-%02d' % (_cy, _cmm)
        _crows = req(f"{url}/api/card/11771/query/json", 'POST', {}, H)
        _cmonths = set()
        for _r in _crows:
            # per-team base (denominator for the % view) = all sellers of that team, both flags
            _tm0 = 'HIT' if _r.get('hit_year_week') is not None else 'REVENUE'
            churn_cmp['totals'][_tm0] += 1
            if _r.get('churn_flag') != 1:
                continue
            gl, ls = _r.get('go_live_week'), _r.get('last_spend_week')
            if not gl or not ls:
                continue
            team = 'HIT' if _r.get('hit_year_week') is not None else 'REVENUE'   # 1k-5k = HIT1'd
            try:
                g = _yw_mon(gl); l = _yw_mon(ls)
                cm = (l + datetime.timedelta(days=21)).strftime('%Y-%m')
                age = (l.year - g.year) * 12 + (l.month - g.month)
            except (ValueError, TypeError):
                continue
            if age < 0 or cm < _churn_cutoff:   # skip noise + anything older than last 6 months
                continue
            sid = str(_r.get('seller_id') or '')
            churn_cmp['rows'][team].append([sid, _r.get('icp_score'), cm, age, name_by_s.get(sid, '')])
            _cmonths.add(cm)
        churn_cmp['months'] = sorted(_cmonths)
        print(f"[bev2] churn cmp (card 11771, last 6mo >= {_churn_cutoff}): HIT={len(churn_cmp['rows']['HIT'])} "
              f"REVENUE={len(churn_cmp['rows']['REVENUE'])} · {len(_cmonths)} churn months")
    except Exception as _e:
        print(f"[bev2] card 11771 churn cmp failed: {_e}")

    # ---- (18) Platform-level 1k-5k weekly metrics (card 11746): RTO / GMV / cancel / COGS / AOV ... ----
    platform_wk = []
    try:
        def _pn(v):
            try: return round(float(v), 2)
            except (TypeError, ValueError): return None
        for _r in sorted(req(f"{url}/api/card/11746/query/json", 'POST', {}, H),
                         key=lambda x: -(int(x.get('year_week') or 0))):
            platform_wk.append({
                'yw': str(_r.get('year_week') or ''),
                'sellers': _r.get('sellers'),
                'orders': _r.get('total_orders'),
                'awbNc': _r.get('awb_nc'),
                'rto': _pn(_r.get('rto_perc')),
                'sGmv': _pn(_r.get('s_gmv')),
                'cogs': _pn(_r.get('cogs_percentage')),
                'cancel': _pn(_r.get('cancel_perc')),
                'aov': _pn(_r.get('aov')),
                'logsCost': _pn(_r.get('per_awb_logs_cost')),
            })
        print(f"[bev2] platform 1k-5k (card 11746): {len(platform_wk)} weeks")
    except Exception as _e:
        print(f"[bev2] card 11746 platform failed: {_e}")
        try:  # no-clobber: keep last-good platform weeks if the card query is erroring
            _prev = (json.load(open(os.path.join(REPO, 'bev_data.json'))).get('bev2', {}) or {}).get('platformWk')
            if _prev:
                platform_wk = _prev
                print(f"[bev2] platform: reused {len(_prev)} prior weeks (no-clobber)")
        except Exception:
            pass

    bev2 = {
        'window': {'from': good_dates[0] if good_dates else '', 'to': as_of},
        'slHistory': sl_history,
        'hit2Mom': hit2_mom,
        'hit2Cohort': hit2_cohort,
        'googleHitCohort': google_hit_cohort,
        'googleGoliveCohort': google_golive_cohort,
        'googleGoliveToggle': google_golive_toggle,
        'googleHitCohortToggle': google_hit_cohort_toggle,
        'googleHitConv': google_hit_conv,
        'googleHitConvToggle': google_hit_conv_toggle,
        'arrCohort': arr_cohort,
        'arrBuckets': arr_buckets,
        'hit2ArrSpendMom': hit2_mom_perf,
        'hit2ArrSpendWow': hit2_wow_perf,
        'spends1k5k': spends_1k5k,
        'potentials': {'value': len(potentials), 'detail': potentials},
        'd7Paused': {'value': len(d7_paused), 'detail': d7_paused},
        'nps1k5k': nps_1k5k,
        'arrBuckets1k5k': arr_buckets_1k5k,
        'churnCmp': churn_cmp,
        'platformWk': platform_wk,
        'google': {
            'bucketHealth': g_bucket_health, 'potentials': g_potentials, 'objective': g_objective,
            'spendMom': google_spend_mom, 'spendWow': google_spend_wow,
            'sellerCount': len(google_sids),
        },
    }
    print(f"[bev2] potentials={len(potentials)} d7paused={len(d7_paused)} gBucketHealth={g_bucket_health['value']} gPotentials={g_potentials['value']} gObjective={g_objective['value']}")

    out = {
        'generatedAt': datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ'),
        'asOfDate': as_of, 'weekMon': monS, 'weekSun': sunS, 'last7Cutoff': cut7, 'last7End': today.isoformat(),
        'dates': good_dates, 'sellerMeta': seller_meta, 'perfByDate': perf_by_date,
        'cards': {
            'accounts':    {'value': len(sids), 'detail': accounts_detail},
            'hit2':        {'value': len(hit2_detail), 'detail': hit2_detail},
            'cohort':      cohort,
            'weekly1k5k':  weekly_1k5k,
            'weeklyByHit': weekly_by_hit,
            'googleWk':    google_wk,
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
        'bev2': bev2,
    }
    # Serialize fully in-memory FIRST, then write — so a serialization error can never leave a
    # truncated bev_data.json on disk (the previous valid file stays intact instead).
    _payload = json.dumps(out, separators=(',', ':'))
    open(OUT, 'w').write(_payload)
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
