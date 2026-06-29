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
import os, sys, glob, gzip, json, shutil, datetime

REPO = os.path.expanduser(os.environ.get("REPO_DIR", "~/shopdeck-metrics-site"))
SNAP_DIR = os.path.join(REPO, "snapshots")
RETENTION_DAYS = 180


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
    print(f"[snapshot] {date}: archived {len(files)} files "
          f"({total_raw // 1024} KB -> {total_gz // 1024} KB gz)")

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
        "files": [os.path.basename(f) for f in files],
    }
    with open(os.path.join(SNAP_DIR, "index.json"), "w") as fh:
        json.dump(index, fh, separators=(",", ":"))
    print(f"[snapshot] index.json: {len(dates)} dates available")


if __name__ == "__main__":
    main()
