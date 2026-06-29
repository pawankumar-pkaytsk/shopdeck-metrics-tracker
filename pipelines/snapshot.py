#!/usr/bin/env python3
"""Archive the current *_data.json files as a dated, gzip-compressed snapshot.

Creates snapshots/YYYY-MM-DD/<file>.json.gz for every *_data.json in the repo
root, maintains snapshots/index.json (sorted list of available dates), and prunes
snapshots older than RETENTION_DAYS so the working tree stays bounded.

The dashboard reads these snapshots straight from GitHub raw (CORS-open) and
gunzips them in-browser, so they are EXCLUDED from the Vercel deploy via
.vercelignore — they never bloat the served site.

Run: cd ~/shopdeck-metrics-site && python3 pipelines/snapshot.py [--date YYYY-MM-DD]
(the daily refresh workflow runs it right after all *_refresh.py have regenerated data)
"""
import os, sys, glob, gzip, json, shutil, datetime, urllib.request, urllib.parse

REPO = os.path.expanduser(os.environ.get("REPO_DIR", "~/shopdeck-metrics-site"))
SNAP_DIR = os.path.join(REPO, "snapshots")
RETENTION_DAYS = 180

# Core team Google-Sheet sources (mirror build.mjs) — fetched here so the
# sheet-backed views also time-travel. Each: out_name, spreadsheet_id, range, dateRender.
SHEETS = [
    ("spendinputs.json", "1wwfbMVkMKq80Znq1mkpO-NCLI-fc7d2hPIepCp04bQ0", "A2:H", "SERIAL_NUMBER"),
    ("daily_plan.json",  "1QCdVIkKa_4yMb1NZHSkIt50x4qoKaFlw2WXpQZnL6KM", "'Daily Plan'!A:AK", "FORMATTED_STRING"),
    ("handover.json",    "1ZLOcj648aYvVaEGHX_QHB1Qx3OMUT3K_eeW-SBUbCso", "'handover'!A:J", "FORMATTED_STRING"),
    ("escalation.json",  "1eIbQU-odVp6lwBnawIIdSbpVHIrywy98Ib4RsZhEPgk", "'Raw_Suggested'!A:P", "FORMATTED_STRING"),
]
LOCAL_SA_KEY = os.path.expanduser("~/Downloads/metrics-tracker-automation-53ad2cdd4b65.json")


def _sa_info():
    if os.environ.get("GOOGLE_SA_KEY"):
        return json.loads(os.environ["GOOGLE_SA_KEY"])
    if os.path.exists(LOCAL_SA_KEY):
        return json.load(open(LOCAL_SA_KEY))
    return None


def gz_bytes(raw, dst):
    with gzip.open(dst, "wb", compresslevel=9) as gz:
        gz.write(raw)


def fetch_sheets_into(out_dir):
    """Fetch the Core team sheets via the service account and gz them into out_dir.
    Mirrors build.mjs output shape: {generatedAt, range, values:[header, ...non-empty rows]}."""
    sa = _sa_info()
    if not sa:
        print("[snapshot] no GOOGLE_SA_KEY / local key — skipping sheet snapshots")
        return []
    try:
        from google.oauth2 import service_account
        import google.auth.transport.requests as gtr
        creds = service_account.Credentials.from_service_account_info(
            sa, scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"])
        creds.refresh(gtr.Request())
        tok = creds.token
    except Exception as e:
        print(f"[snapshot] sheet auth failed ({e}) — skipping sheet snapshots")
        return []

    done = []
    for out, sid, rng, dr in SHEETS:
        try:
            u = (f"https://sheets.googleapis.com/v4/spreadsheets/{sid}/values/"
                 f"{urllib.parse.quote(rng)}?valueRenderOption=UNFORMATTED_VALUE&dateTimeRenderOption={dr}")
            req = urllib.request.Request(u, headers={"Authorization": "Bearer " + tok})
            vals = json.loads(urllib.request.urlopen(req, timeout=300).read()).get("values", [])
            trimmed = ([vals[0]] + [r for r in vals[1:] if any(str(c).strip() for c in r)]) if vals else []
            payload = json.dumps({"generatedAt": datetime.datetime.utcnow().isoformat() + "Z",
                                  "range": rng, "values": trimmed}, separators=(",", ":")).encode()
            gz_bytes(payload, os.path.join(out_dir, out + ".gz"))
            done.append(out)
            print(f"[snapshot] sheet {out}: {len(trimmed)} rows -> gz")
        except Exception as e:
            print(f"[snapshot] sheet {out} failed: {e}")
    return done


def today_str():
    for i, a in enumerate(sys.argv):
        if a == "--date" and i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return datetime.date.today().isoformat()


def main():
    date = today_str()
    out_dir = os.path.join(SNAP_DIR, date)
    os.makedirs(out_dir, exist_ok=True)

    files = sorted(glob.glob(os.path.join(REPO, "*_data.json")))
    total_raw = total_gz = 0
    for f in files:
        name = os.path.basename(f)
        dst = os.path.join(out_dir, name + ".gz")
        with open(f, "rb") as src, gzip.open(dst, "wb", compresslevel=9) as gz:
            shutil.copyfileobj(src, gz)
        total_raw += os.path.getsize(f)
        total_gz += os.path.getsize(dst)
    print(f"[snapshot] {date}: archived {len(files)} data files "
          f"({total_raw // 1024} KB -> {total_gz // 1024} KB gz)")

    # Core team Google-Sheet sources (so sheet-backed views also time-travel)
    sheet_files = fetch_sheets_into(out_dir)

    # prune snapshots older than retention window
    cutoff = (datetime.date.fromisoformat(date) - datetime.timedelta(days=RETENTION_DAYS)).isoformat()
    pruned = 0
    for d in os.listdir(SNAP_DIR):
        full = os.path.join(SNAP_DIR, d)
        if os.path.isdir(full) and len(d) == 10 and d < cutoff:
            shutil.rmtree(full)
            pruned += 1
    if pruned:
        print(f"[snapshot] pruned {pruned} snapshot(s) older than {cutoff}")

    # rebuild index.json (sorted list of available dates + file list)
    dates = sorted(d for d in os.listdir(SNAP_DIR)
                   if os.path.isdir(os.path.join(SNAP_DIR, d)) and len(d) == 10)
    index = {
        "generatedAt": datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "retentionDays": RETENTION_DAYS,
        "dates": dates,
        "files": [os.path.basename(f) for f in files] + sheet_files,
    }
    with open(os.path.join(SNAP_DIR, "index.json"), "w") as fh:
        json.dump(index, fh, separators=(",", ":"))
    print(f"[snapshot] index.json: {len(dates)} dates available")


if __name__ == "__main__":
    main()
