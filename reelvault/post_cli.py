#!/usr/bin/env python3
"""CLI wrapper so posting runs as a normal ReelVault job (streamed log + cancel).

  python3 -m reelvault.post_cli --account _gor8e --dir ~/.reelvault/data/_gor8e \
      --files '["a.mp4"]' --tags '#reels' --publish

Prints machine-readable markers the job stream understands:
  POSTED <file>   DRYRUN <file>   FAILED <file> <reason>   BLOCKED <reason>
  SUMMARY posted=N dry=N failed=N
"""
import argparse
import json
import os
import sys
import traceback

from reelvault import captions, library
from reelvault.poster import Poster, PosterBlocked


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--account", required=True)
    ap.add_argument("--dir", required=True)
    ap.add_argument("--files", default="[]", help="JSON array of filenames")
    ap.add_argument("--publish", action="store_true")
    ap.add_argument("--template", default="")
    ap.add_argument("--tags", default="")
    ap.add_argument("--result", default="")
    ap.add_argument("--headless", action="store_true")
    ap.add_argument("--allow-unknown-account", action="store_true",
                    help="publish even when the logged-in handle cannot be read")
    a = ap.parse_args()

    def log(msg, kind="log"):
        print(msg, flush=True)

    files = json.loads(a.files) if a.files else []
    paths = [os.path.join(a.dir, f) for f in files if os.path.isfile(os.path.join(a.dir, f))]
    results = []

    def dump():
        if not a.result:
            return
        try:
            with open(a.result, "w") as f:
                json.dump(results, f)
        except Exception:
            pass

    if not paths:
        log("no files to post", "warn")
        print("SUMMARY posted=0 dry=0 failed=0", flush=True)
        dump()
        return 0

    log("mode: %s · scraped from @%s · %d item(s)" % (
        "PUBLISH" if a.publish else "DRY RUN", a.account, len(paths)))

    p = Poster(log=log, headless=a.headless)
    code = 0
    try:
        if not p.logged_in():
            reason = p.problem or "the Firefox session cannot use Instagram right now"
            log("!! BLOCKED — %s" % reason, "error")
            print("BLOCKED %s" % reason.replace("\n", " ")[:200], flush=True)
            for path in paths:
                results.append({"file": os.path.basename(path), "status": "failed",
                                "caption": "", "account": "", "error": reason[:300]})
            print("SUMMARY posted=0 dry=0 failed=%d" % len(paths), flush=True)
            dump()
            return 2

        who = p.active_account()
        log("session ok — the logged-in account is @%s" % (who or "UNKNOWN"))
        cap0 = captions.build(library._meta_for(paths[0]), a.template, a.tags,
                              source=a.account)[0]
        log("caption look: %r" % cap0[:140])

        for path in paths:
            name = os.path.basename(path)
            if p.stopped:
                break
            caption, info = captions.build(library._meta_for(path), a.template, a.tags,
                                           source=a.account)
            try:
                if a.publish and not who and not a.allow_unknown_account:
                    raise RuntimeError(
                        "cannot confirm which account is logged in — refusing to publish "
                        "(re-run with --allow-unknown-account to override)")
                status = p.post(path, caption, publish=a.publish)
            except PosterBlocked as e:
                log("!! blocked while posting %s — %s" % (name, str(e)[:200]), "error")
                print("BLOCKED %s" % str(e).replace("\n", " ")[:200], flush=True)
                results.append({"file": name, "status": "failed", "caption": caption,
                                "account": who, "error": str(e)[:300]})
                code = 2
                break
            except Exception as e:
                log("✗ %s — %s" % (name, str(e)[:160]), "error")
                print("FAILED %s %s" % (name, str(e)[:120].replace("\n", " ")), flush=True)
                results.append({"file": name, "status": "failed", "error": str(e)[:300],
                                "caption": caption, "account": who})
                continue

            print(("%s %s" % ("POSTED" if status == "posted" else "DRYRUN", name)), flush=True)
            results.append({"file": name, "status": status, "caption": caption,
                            "account": who})
            if status == "posted":
                log("  ✓ %s posted (%d chars, tags %d)" % (name, info["chars"], len(info["tags"])), "done")
            else:
                log("  dry run ok (%d chars, tags %d)" % (info["chars"], len(info["tags"])))
            dump()
    except PosterBlocked as e:
        log("!! BLOCKED — %s" % str(e)[:200], "error")
        print("BLOCKED %s" % str(e).replace("\n", " ")[:200], flush=True)
        code = 2
    except Exception as e:
        log("posting aborted: %s" % e, "error")
        traceback.print_exc()
        code = 1
    finally:
        p.stop()

    posted = sum(1 for r in results if r["status"] == "posted")
    dry = sum(1 for r in results if r["status"] == "dry_run")
    failed = len(paths) - posted - dry
    print("SUMMARY posted=%d dry=%d failed=%d" % (posted, dry, failed), flush=True)
    dump()
    if code:
        return code
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
