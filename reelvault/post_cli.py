#!/usr/bin/env python3
"""CLI wrapper so posting runs as a normal ReelVault job (streamed log + cancel).

  python3 -m reelvault.post_cli --account _gor8e --files '["a.mp4"]' --publish

Prints machine-readable markers the job stream understands:
  POSTED <file>   DRYRUN <file>   FAILED <file> <reason>   SUMMARY posted=N dry=N failed=N
"""
import argparse
import json
import os
import sys
import traceback

from reelvault import library
from reelvault.poster import Poster


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--account", required=True)
    ap.add_argument("--dir", required=True)
    ap.add_argument("--files", default="[]", help="JSON array of filenames")
    ap.add_argument("--publish", action="store_true")
    ap.add_argument("--template", default="")
    ap.add_argument("--result", default="")
    ap.add_argument("--headless", action="store_true")
    a = ap.parse_args()

    def log(msg, kind="log"):
        print(msg, flush=True)

    files = json.loads(a.files) if a.files else []
    paths = [os.path.join(a.dir, f) for f in files if os.path.isfile(os.path.join(a.dir, f))]
    if not paths:
        log("no files to post", "warn")
        print("SUMMARY posted=0 dry=0 failed=0", flush=True)
        return 0

    log("mode: %s · account: @%s · %d item(s)" % (
        "PUBLISH" if a.publish else "DRY RUN", a.account, len(paths)))

    results = []
    p = Poster(log=log, headless=a.headless)
    try:
        if not p.logged_in():
            log("!! not logged in — the Firefox profile has no live Instagram session", "error")
            print("SUMMARY posted=0 dry=0 failed=%d" % len(paths), flush=True)
            return 2
        who = p.active_account()
        log("session ok — posting as @%s" % who)

        for path in paths:
            meta = library._meta_for(path)
            caption = (meta.get("description") or meta.get("title") or "").strip()
            if not caption:
                caption = (a.template or os.path.basename(path)[:-4].replace("_", " ")).strip()
            try:
                status = p.post(path, caption, publish=a.publish)
            except Exception as e:
                log("✗ %s — %s" % (os.path.basename(path), str(e)[:160]), "error")
                print("FAILED %s %s" % (os.path.basename(path), str(e)[:120].replace("\n", " ")), flush=True)
                results.append({"file": os.path.basename(path), "status": "failed",
                                "error": str(e)[:300], "caption": caption, "account": who})
                continue

            print(("%s %s" % ("POSTED" if status == "posted" else "DRYRUN",
                              os.path.basename(path))), flush=True)
            results.append({"file": os.path.basename(path), "status": status,
                            "caption": caption, "account": who})
            if status == "posted":
                log("  ✓ %s posted" % os.path.basename(path), "done")
    except Exception as e:
        log("posting aborted: %s" % e, "error")
        traceback.print_exc()
    finally:
        p.stop()

    posted = sum(1 for r in results if r["status"] == "posted")
    dry = sum(1 for r in results if r["status"] == "dry_run")
    failed = sum(1 for r in results if r["status"] == "failed")
    print("SUMMARY posted=%d dry=%d failed=%d" % (posted, dry, failed), flush=True)

    if a.result:
        try:
            with open(a.result, "w") as f:
                json.dump(results, f)
        except Exception:
            pass
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
