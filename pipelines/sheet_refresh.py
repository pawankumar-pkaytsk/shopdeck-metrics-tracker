#!/usr/bin/env python3
"""Read the 4 Google Sheets via a service account and dump them to JSON,
so the frontend needs no browser Google login (auth becomes Clerk-only).

Sheets (shared with tracker-sheets-reader@metrics-tracker-automation.iam.gserviceaccount.com):
  - spendinputs  1wwfbMVk… A2:H   (UNFORMATTED, date as serial — matches SheetsAPI.getRows)
  - Daily Plan   1QCdVIk…  A:AK   (UNFORMATTED values, dates as strings — matches getValues)
  - handover     1ZLOcj…   A:J
  - escalation   1eIbQU…   Raw_Suggested!A:P

Credentials: env GOOGLE_SA_KEY (the JSON key as a string) or SA_KEY_PATH (path to the JSON file).
Run: cd ~/shopdeck-metrics-site && SA_KEY_PATH=~/Downloads/metrics-tracker-automation-*.json python3 pipelines/sheet_refresh.py
"""
import json, os, sys, datetime, urllib.request, urllib.parse, subprocess
from google.oauth2 import service_account
import google.auth.transport.requests as gtr

REPO = os.path.expanduser(os.environ.get("REPO_DIR", "~/shopdeck-metrics-site"))
SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]

# (output file, spreadsheetId, range, value_render, datetime_render)
SHEETS = [
    ("spendinputs.json", "1wwfbMVkMKq80Znq1mkpO-NCLI-fc7d2hPIepCp04bQ0", "A2:H", "UNFORMATTED_VALUE", "SERIAL_NUMBER"),
    ("daily_plan.json",  "1QCdVIkKa_4yMb1NZHSkIt50x4qoKaFlw2WXpQZnL6KM", "'Daily Plan'!A:AK", "UNFORMATTED_VALUE", "FORMATTED_STRING"),
    ("handover.json",    "1ZLOcj648aYvVaEGHX_QHB1Qx3OMUT3K_eeW-SBUbCso", "'handover'!A:J", "UNFORMATTED_VALUE", "FORMATTED_STRING"),
    ("escalation.json",  "1eIbQU-odVp6lwBnawIIdSbpVHIrywy98Ib4RsZhEPgk", "'Raw_Suggested'!A:P", "UNFORMATTED_VALUE", "FORMATTED_STRING"),
]


def creds():
    if os.environ.get("GOOGLE_SA_KEY"):
        return service_account.Credentials.from_service_account_info(json.loads(os.environ["GOOGLE_SA_KEY"]), scopes=SCOPES)
    path = os.environ.get("SA_KEY_PATH")
    if path:
        import glob
        matches = glob.glob(os.path.expanduser(path))
        if matches:
            return service_account.Credentials.from_service_account_file(matches[0], scopes=SCOPES)
    raise SystemExit("No credentials: set GOOGLE_SA_KEY or SA_KEY_PATH")


def main():
    c = creds()
    c.refresh(gtr.Request())
    tok = c.token
    for out, sid, rng, vr, dr in SHEETS:
        u = (f"https://sheets.googleapis.com/v4/spreadsheets/{sid}/values/{urllib.parse.quote(rng)}"
             f"?valueRenderOption={vr}&dateTimeRenderOption={dr}")
        data = json.loads(urllib.request.urlopen(urllib.request.Request(u, headers={'Authorization': 'Bearer ' + tok}), timeout=120).read())
        values = data.get("values", [])
        # keep the header row + any row with at least one non-empty cell (drop blank padding rows)
        if values:
            header, body = values[0], values[1:]
            body = [r for r in body if any(str(c).strip() for c in r)]
            values = [header] + body
        payload = {"generatedAt": datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ'), "range": rng, "values": values}
        p = os.path.join(REPO, out)
        json.dump(payload, open(p, "w"), separators=(',', ':'))
        print(f"[sheet] {out}: {len(values)} rows ({os.path.getsize(p)} bytes)")

    if '--push' in sys.argv:
        files = [s[0] for s in SHEETS]
        subprocess.run(['git', '-C', REPO, 'add'] + files, check=True)
        r = subprocess.run(['git', '-C', REPO, 'commit', '-m', 'Refresh sheet data (service account)'], capture_output=True, text=True)
        print(r.stdout.strip() or r.stderr.strip())
        if r.returncode == 0:
            subprocess.run(['git', '-C', REPO, 'push', 'origin', 'main'], check=True); print("[push] deployed")


if __name__ == '__main__':
    main()
