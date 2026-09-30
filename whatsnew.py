#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
whatsnew.py 1.0.0 - "What's new" emails for Synology Photos albums.

Once a day: what changed in the albums each person sees. One email per
person, only on days with news.
  * photos someone else added, with thumbnails
  * albums newly shared with you
  * what the Album Tool (albumtool.py) added or made for you
  * warnings: an album of yours is gone or lost photos
What you added yourself by hand isn't listed.

Author:   Egbert H. Schroeer
License:  MIT, see LICENSE
Needs:    onthisday.py 1.5 or later in the same folder; its settings file
          (onthisday.conf) for mail, people and wording. Set news = no there,
          so On This Day leaves the news to this email.

Run from Task Scheduler as root, one task per person, e.g. daily at 18:00:

    /usr/bin/python3 /volume1/homes/<admin>/Scripts/onthisday/whatsnew.py --user <name>

Notes of what the albums held: state_dir/whatsnew-<user>.json (root only).
The first run starts from On This Day's last notes, if there are any;
otherwise it only takes notes.

Standard library only.
"""

import argparse
import datetime as dt
import glob
import json
import os
import pwd
import re
import smtplib
import ssl
import subprocess
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)
try:
    import onthisday as otd
except ImportError:
    sys.stderr.write("whatsnew.py needs onthisday.py in the same folder (%s)\n" % SCRIPT_DIR)
    sys.exit(1)

__author__ = "Egbert H. Schroeer"
__license__ = "MIT"
VERSION = "1.0.0"
__version__ = VERSION
NEEDS_ONTHISDAY = (1, 5, 0)
JOURNAL_RE = re.compile(r"^journal-(\d{8}-\d{6})(?:-\d+)?\.jsonl$")
JOURNAL_DAYS = 8        # Album Tool journals of the last days are read
log = otd.log


def version_tuple(text):
    return tuple(int(x) for x in re.findall(r"\d+", text)[:3])


def tool_changes(pw, home, folder):
    """What the Album Tool did for this user, from its undo journals in `folder`
    (relative to the home folder): {"added": {album id: {item ids}}, "created": {album ids}}.
    Undone runs (.undone) don't count."""
    out = {"added": {}, "created": set()}
    if not folder:
        return out
    with otd.AsUser(pw):
        path = os.path.realpath(os.path.join(home, folder))
        if not otd.inside(path, os.path.realpath(home)) or not os.path.isdir(path):
            log("note: albumtool_folder %s not found; Album Tool changes not shown" % folder)
            return out
        cutoff = dt.datetime.now() - dt.timedelta(days=JOURNAL_DAYS)
        for p in sorted(glob.glob(os.path.join(path, "journal-*.jsonl"))):
            m = JOURNAL_RE.match(os.path.basename(p))
            if not m or os.path.islink(p):
                continue
            try:
                d = m.group(1)          # no strptime: it imports a module, and this runs with the user's rights
                if dt.datetime(int(d[:4]), int(d[4:6]), int(d[6:8]), int(d[9:11]), int(d[11:13])) < cutoff:
                    continue
                with open(p, encoding="utf-8") as f:
                    ops = [json.loads(line) for line in f if line.strip()]
            except (OSError, ValueError):
                continue
            for op in ops:
                if not isinstance(op, dict):
                    continue
                if op.get("op") == "add":
                    out["added"].setdefault(str(op.get("id")), set()).update(str(i) for i in op.get("items") or [])
                elif op.get("op") == "create":
                    out["created"].add(str(op.get("id")))
    return out


def build_text(cfg, today, news):
    lines = [otd.fmt(cfg.texts["whatsnew_subject"], date=cfg.date_text(today)), ""]
    for nw in news:
        more = (" (%s)" % otd.fmt(cfg.texts["news_more"], n=nw.more)) if nw.more else ""
        lines.append("%s%s%s" % ("! " if nw.warn else "", nw.text, more))
    if cfg.photos_link:
        lines += ["", cfg.photos_link]
    return "\n".join(lines)


def run(args):
    if not otd.USER_RE.match(args.user):
        raise otd.ConfigError("invalid user name %r" % args.user)
    try:
        pw = pwd.getpwnam(args.user)
    except KeyError:
        raise otd.ConfigError("DSM user %r not found" % args.user)
    cfg = otd.Config(args.config)
    today = dt.date.today()
    home = pw.pw_dir if os.path.isdir(pw.pw_dir) else "/var/services/homes/" + args.user
    roots = otd.photo_roots(cfg, home)
    section = cfg.user_section(args.user)
    if section is not None and section.get("photos_link", "").strip():
        cfg.photos_link = section.get("photos_link").strip()
    to_addr = ""
    if not args.dry_run:
        to_addr = section.get("email", "").strip() if section is not None else ""
        if not to_addr:
            raise otd.ConfigError("no email address for %s: add [user:%s] with email = ..." % (args.user, args.user))
        if cfg.email["transport"].strip().lower() == "dsm":      # fail early, before reading the albums
            otd.find_ssmtp(cfg)
            otd.dsm_sender(cfg)
        elif cfg.email["smtp_user"].strip():
            otd.read_secret(cfg.email["secret_dir"].strip())

    api = otd.PhotosApi(cfg.synowebapi, args.user)
    api.additional = ["thumbnail"]      # no picking here, so no star ratings
    stats = {}
    me = api.my_id()
    albums, complete = otd.read_albums(api, me, stats)
    if not complete:
        log("note: an album list couldn't be read; nothing compared today")
        return 1
    locator = otd.Locator(api, cfg, roots)
    locator.api_me = me

    old = otd.load_news_state(cfg, args.user, "whatsnew")
    if old is None:
        old = otd.load_news_state(cfg, args.user, "news")        # On This Day's notes
        if old is not None:
            log("first run: compared with On This Day's notes of %s" % old.get("date", "?"))
    if old is None:
        log("first run: what the albums hold now is noted; news from the next run on")
        if not args.dry_run:
            otd.save_news_state(cfg, args.user, albums, today, None, "whatsnew")
        return 0

    folder = section.get("albumtool_folder", "").strip() if section is not None else ""
    tool = tool_changes(pw, home, folder)
    news = otd.build_news(cfg, roots, me, old, albums, locator, tool)
    log("%s, %s: %d news; %d albums, %d API calls%s" % (
        args.user, today.isoformat(), len(news), len(albums), api.calls,
        (" [Album Tool journals: %d albums added to, %d made]" % (len(tool["added"]), len(tool["created"]))
         if folder else "")))

    if args.dry_run:
        for nw in news:
            print("  %s%s%s" % ("! " if nw.warn else "", nw.text,
                                (" (%s)" % otd.fmt(cfg.texts["news_more"], n=nw.more)) if nw.more else ""))
            for item in nw.items:
                print("    %s %s/%s" % (otd.utc_hhmm(item.time), item.folder.rstrip("/"), item.filename))
        return 0
    if not news:
        log("nothing new, nothing sent")
        otd.save_news_state(cfg, args.user, albums, today, old, "whatsnew")
        return 0

    images = {}
    for nw in news:
        for item in nw.items:
            try:
                images[id(item)] = otd.read_thumb(item.thumb, item.root)
            except (OSError, ValueError, TypeError) as e:
                log("skipping %s: %s" % (item.thumb, e))
        nw.items = [i for i in nw.items if id(i) in images]
    keep = [i for nw in news for i in nw.items]
    sizes = {id(i): otd.jpeg_size(images[id(i)]) for i in keep}
    cids = {id(i): otd.make_msgid("wn")[1:-1] for i in keep}
    t = cfg.texts
    date_text = cfg.date_text(today)
    summary = otd.fmt(t["whatsnew_summary_one" if len(news) == 1 else "whatsnew_summary_many"],
                      date=date_text, count=len(news))
    page = otd.build_html(cfg, today, [], lambda i: "cid:" + cids[id(i)], lambda i: sizes.get(id(i)), news,
                          heading=t["whatsnew_heading"], summary=summary, footer=t["whatsnew_footer"])
    subject = otd.fmt(t["whatsnew_subject"], date=date_text)
    imgs = [(cids[id(i)], otd.jpg_name(i.filename), images[id(i)], "jpeg") for i in keep]
    size = otd.send_email(cfg, to_addr, subject, build_text(cfg, today, news), page, imgs)
    log("emailed %d news with %d photos to %s (%d KB)" % (len(news), len(keep), to_addr, size // 1024))
    otd.save_news_state(cfg, args.user, albums, today, old, "whatsnew")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="What's new in the Synology Photos albums, by email")
    ap.add_argument("--user", required=True, help="DSM user name, e.g. alice")
    ap.add_argument("--config", default=otd.DEFAULT_CONFIG, help="settings file (default: onthisday.conf)")
    ap.add_argument("--dry-run", action="store_true", help="only list the news; nothing sent, nothing noted")
    ap.add_argument("--version", action="version", version="%(prog)s " + VERSION)
    args = ap.parse_args(argv)
    log("whatsnew %s --user %s%s" % (VERSION, args.user, " --dry-run" if args.dry_run else ""))
    try:
        if version_tuple(otd.VERSION) < NEEDS_ONTHISDAY:
            raise otd.ConfigError("onthisday.py %s is too old, %d.%d.%d or later is needed"
                                  % ((otd.VERSION,) + NEEDS_ONTHISDAY))
        return run(args)
    except (otd.ApiError, otd.ConfigError, otd.DeliveryError, OSError, smtplib.SMTPException, ssl.SSLError,
            subprocess.TimeoutExpired) as e:
        log("ERROR: %s" % e)
        return 1


if __name__ == "__main__":
    sys.exit(main())
