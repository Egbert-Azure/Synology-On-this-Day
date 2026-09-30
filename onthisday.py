#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
onthisday.py 1.5.0 - "On this day" emails for Synology Photos.

Every morning: the photos taken on today's date in earlier years, one email
per person, grouped by year. Tap a photo to open it in Synology Photos.
Above the memories: what changed in the albums since the last email, or,
with news = no, in its own daily email from whatsnew.py.

Author:   Egbert H. Schroeer
License:  MIT, see LICENSE
Needs:    DSM 7 with Synology Photos; Python 3.8 (built in). For the mosaic
          layout a SynoCommunity FFmpeg package.

Run from Task Scheduler as root, one task per person:

    /usr/bin/python3 /volume1/homes/<admin>/Scripts/onthisday/onthisday.py --user <name>

Sources
  Personal Space (home/Photos, incl. MobileBackup), Shared Space (shared
  folder "photo") and albums shared with the user. Nothing else.
  A photo that is also in an album opens inside the album (album_first).

Picking, when a year has more than photos_per_year
  1. Shots within a minute are one moment.
  2. Starred first, then albums and own folders, phone backup last.
  3. Spread over the day.
  4. Rotated yearly: next year, other photos of the same day.

Layout
  mosaic  fixed frames like OneDrive's memories, photos cut to fit with
          ffmpeg. Without a working ffmpeg: rows, and the email says so.
  rows    uncropped, rows of equal height.

Security
  * No DSM password. synowebapi (root only) asks Synology Photos as the user.
  * Thumbnails only from inside the photo roots: real JPEGs, no symlinks.
  * ffmpeg gets bytes through a pipe and runs as the user.
  * Pages are written as the user. Album notes and mail password: root only.
  * Mail: DSM's alert mail (transport = dsm) or an SMTP server of your
    choice (transport = smtp; password stored once, --import-smtp-password).

Standard library only.
"""

import argparse
import base64
import calendar
import configparser
import datetime as dt
import glob
import html
import itertools
import json
import os
import pwd
import re
import shutil
import smtplib
import ssl
import stat
import struct
import subprocess
import sys
import time
import zlib
from collections import OrderedDict
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid, parseaddr

__author__ = "Egbert H. Schroeer"
__license__ = "MIT"
VERSION = "1.5.0"
__version__ = VERSION
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CONFIG = os.path.join(SCRIPT_DIR, "onthisday.conf")
DAY = 86400
THUMB_NAMES = ("SYNOPHOTO_THUMB_M.jpg", "SYNOPHOTO_THUMB_SM.jpg", "SYNOPHOTO_THUMB_XL.jpg")
BIG_THUMB_NAMES = ("SYNOPHOTO_THUMB_XL.jpg", "SYNOPHOTO_THUMB_M.jpg", "SYNOPHOTO_THUMB_SM.jpg")
MAX_THUMB_BYTES = 3 * 1024 * 1024
SECRET_NAME = "smtp-password"
AUTO_FALLBACK_YEAR = 1950  # only if the oldest photo cannot be determined
USER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
PAGE_FILE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}\.html$")

# Synology Photos API names per space: (items, folders)
SPACES = {
    "personal": ("SYNO.Foto.Browse.Item", "SYNO.Foto.Browse.Folder"),
    "shared": ("SYNO.FotoTeam.Browse.Item", "SYNO.FotoTeam.Browse.Folder"),
}

DEFAULTS = {
    "general": {
        "delivery": "file",
        "first_year": "auto",
        "photos_per_year": "5",
        "max_photos": "12",
        "include_videos": "yes",
        "layout": "mosaic",
        "ffmpeg": "auto",
        "prefer_starred": "yes",
        "backup_folders": "MobileBackup",
        "rotate_yearly": "yes",
        "shared_albums": "yes",
        "album_first": "yes",
        "album_links": "no",
        "skip_date_mismatch": "yes",
        "shared_space": "yes",
        "shared_space_path": "auto",
        "keep_days": "14",
        "photos_link": "",
        "news": "yes",
        "news_photos": "4",
        "state_dir": "/volume1/@onthisday",
        "synowebapi": "/usr/syno/bin/synowebapi",
    },
    "exclude": {
        "filename_patterns": "\n-WA\\d{4}\n^WhatsApp (Image|Video)\n^FB_IMG_\n^received_",
        "folder_names": "\nWhatsApp\nFacebook\nMessenger",
    },
    "email": {
        "transport": "dsm",
        "dsm_mail_conf": "/usr/syno/etc/synosmtp.conf",
        "ssmtp": "auto",
        "smtp_host": "",
        "smtp_port": "587",
        "smtp_security": "starttls",
        "smtp_verify_tls": "yes",
        "smtp_user": "",
        "from": "",
        "secret_dir": "/volume1/@onthisday",
    },
    "texts": {
        "subject": "On this day – {date}",
        "heading": "On this day",
        "summary_one": "{date} · 1 memory",
        "summary_many": "{date} · {count} memories",
        "year_one": "1 year ago · {year}",
        "year_many": "{n} years ago · {year}",
        "more_places": "+{n} more",
        "shared": "Shared",
        "video": "Video",
        "open_photos": "Open Synology Photos",
        "footer": "Sent by On This Day on your Synology NAS.",
        "months": "January, February, March, April, May, June, July, August, "
                  "September, October, November, December",
        "date_format": "{day} {month}",
        "news_heading": "New in your albums",
        "news_added_one": "{who} added 1 photo to {album}",
        "news_added_many": "{who} added {n} photos to {album}",
        "news_new_album": "{who} shared a new album with you: {album} ({n} photos)",
        "news_gone_own": "Your album {album} is gone (it had {n} photos)",
        "news_gone_shared": "{who}'s album {album} is no longer shared with you",
        "news_removed_one": "1 photo left your album {album}",
        "news_removed_many": "{n} photos left your album {album}",
        "news_more": "+{n} more",
        "someone": "Someone",
        "album_tool": "Album Tool",
        "news_tool_new_album": "{who} made the album {album} ({n} photos)",
        "fallback_note": "Photos shown uncropped: {reason}.",
        "whatsnew_subject": "What's new – {date}",
        "whatsnew_heading": "What's new",
        "whatsnew_summary_one": "{date} · 1 change in the albums",
        "whatsnew_summary_many": "{date} · {count} changes in the albums",
        "whatsnew_footer": "Sent by What's New on your Synology NAS.",
    },
}


class ApiError(Exception):
    pass


class ConfigError(Exception):
    pass


class DeliveryError(Exception):
    pass


class CropError(Exception):
    pass


def log(msg):
    print("%s %s" % (dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg), flush=True)


class SafeDict(dict):
    """str.format_map helper: unknown {placeholders} are left as they are."""

    def __missing__(self, key):
        return "{" + key + "}"


def fmt(template, **values):
    return template.format_map(SafeDict(values))


def utc_date(ts):
    # Synology Photos stores camera time as if it were UTC: the UTC date is the day taken.
    return dt.datetime.utcfromtimestamp(ts).date()


def utc_hhmm(ts):
    return dt.datetime.utcfromtimestamp(ts).strftime("%H:%M")


# --------------------------------------------------------------------------
# Configuration

class Config(object):
    def __init__(self, path):
        cp = configparser.ConfigParser(interpolation=None)
        cp.read_dict(DEFAULTS)
        if path and os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as f:
                    cp.read_file(f)
            except configparser.Error as e:
                raise ConfigError("problem in %s: %s" % (os.path.basename(path),
                                                         " ".join(str(e).split())))
        elif path != DEFAULT_CONFIG:
            raise ConfigError("config file not found: %s" % path)
        self.cp = cp
        g = cp["general"]
        try:
            self.delivery = [d.strip().lower() for d in g["delivery"].split(",") if d.strip()]
            fy = g["first_year"].strip().lower()
            if fy != "auto" and not fy.isdigit():
                raise ValueError("first_year must be 'auto' or a year like 1975 (got %r)" % fy)
            self.first_year = None if fy == "auto" else int(fy)
            self.photos_per_year = max(1, g.getint("photos_per_year"))
            self.max_photos = max(1, g.getint("max_photos"))
            self.include_videos = g.getboolean("include_videos")
            self.prefer_starred = g.getboolean("prefer_starred")
            self.rotate_yearly = g.getboolean("rotate_yearly")
            self.shared_albums = g.getboolean("shared_albums")
            self.album_first = g.getboolean("album_first")
            self.album_links = g.getboolean("album_links")
            self.shared_space = g.getboolean("shared_space")
            self.skip_date_mismatch = g.getboolean("skip_date_mismatch")
            self.keep_days = g.getint("keep_days")
            self.news = g.getboolean("news")
            self.news_photos = max(0, g.getint("news_photos"))
            self.smtp_port = cp["email"].getint("smtp_port")
            self.smtp_verify_tls = cp["email"].getboolean("smtp_verify_tls")
        except ValueError as e:
            raise ConfigError(str(e))
        for d in self.delivery:
            if d not in ("file", "email"):
                raise ConfigError("delivery must be 'file', 'email' or 'file, email' (got %r)" % d)
        self.layout = g["layout"].strip().lower()
        if self.layout not in ("mosaic", "rows"):
            raise ConfigError("layout must be 'mosaic' or 'rows' (got %r)" % self.layout)
        self.ffmpeg = g["ffmpeg"].strip()
        self.backup_folders = [n.strip().lower() for n in re.split(r"[,\n]", g["backup_folders"])
                               if n.strip() and not n.strip().startswith("#")]
        self.shared_space_path = g["shared_space_path"].strip()
        self.photos_link = g["photos_link"].strip()
        self.state_dir = g["state_dir"].strip()
        self.synowebapi = g["synowebapi"].strip()
        try:
            self.name_patterns = [re.compile(p, re.IGNORECASE)
                                  for p in self._lines(cp["exclude"]["filename_patterns"])]
        except re.error as e:
            raise ConfigError("bad filename pattern: %s" % e)
        self.folder_names = [n.lower() for n in self._lines(cp["exclude"]["folder_names"])]
        self.email = cp["email"]
        self.texts = cp["texts"]
        months = [m.strip() for m in self.texts["months"].split(",")]
        if len(months) != 12:
            raise ConfigError("[texts] months needs 12 comma-separated names")
        self.months = months

    @staticmethod
    def _lines(value):
        out = []
        for line in value.splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                out.append(line)
        return out

    def user_section(self, user):
        for name in self.cp.sections():
            if name.lower() == ("user:" + user).lower():
                return self.cp[name]
        return None

    def date_text(self, day):
        return fmt(self.texts["date_format"], day=day.day, month=self.months[day.month - 1],
                   year=day.year)


# --------------------------------------------------------------------------
# Synology Photos through synowebapi

def extract_json_objects(text):
    """All top-level JSON objects in text (synowebapi mixes in CGI headers
    and appends its own status object)."""
    dec = json.JSONDecoder()
    out, i = [], 0
    while True:
        j = text.find("{", i)
        if j < 0:
            return out
        try:
            obj, end = dec.raw_decode(text, j)
        except ValueError:
            i = j + 1
            continue
        out.append(obj)
        i = end


class FilterIgnored(Exception):
    pass


class PhotosApi(object):
    PAGE = 500
    MAX_PAGES = 20

    def __init__(self, binary, user):
        self.binary = binary
        self.user = user
        self.calls = 0
        self.list_version = {"personal": 1, "shared": 1}
        self._folders = {}
        # star ratings too; dropped if Synology Photos refuses them
        self.additional = ["thumbnail", "rating"]
        self.rating_ok = None

    def call(self, api, method, version, **params):
        args = [self.binary, "--exec", "api=" + api, "method=" + method,
                "version=%d" % version, "runner=" + self.user]
        for key, value in params.items():
            args.append("%s=%s" % (key, json.dumps(value)))
        self.calls += 1
        try:
            proc = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  timeout=180)
        except FileNotFoundError:
            raise ApiError("synowebapi not found at %s" % self.binary)
        except subprocess.TimeoutExpired:
            raise ApiError("%s %s timed out" % (api, method))
        replies = [o for o in extract_json_objects(proc.stdout.decode("utf-8", "replace"))
                   if isinstance(o, dict) and "success" in o]
        for reply in replies:
            if reply.get("success") is True:
                return reply.get("data") or {}
        code = None
        if replies:
            code = (replies[0].get("error") or {}).get("code")
        detail = "error %s" % code if code is not None else "no JSON reply"
        if proc.returncode and os.geteuid() != 0:
            detail += " (the script must run as root)"
        raise ApiError("%s %s failed: %s" % (api, method, detail))

    def list_items(self, api, version, **params):
        """Item list call that also asks for star ratings. If the first one is refused, ratings are dropped."""
        while True:
            try:
                data = self.call(api, "list", version, additional=self.additional, **params)
            except ApiError:
                if self.rating_ok is not None or "rating" not in self.additional:
                    raise
                log("note: star ratings not available, picking without them")
                self.additional = ["thumbnail"]
                self.rating_ok = False
                continue
            if self.rating_ok is None:
                self.rating_ok = "rating" in self.additional
            return data

    def oldest_year(self, space):
        """Year of the oldest photo in the space, or None. Tries list v1, then the newest version
        (older ones may ignore sorting)."""
        versions = [1]
        try:
            newest_version = self.max_version(SPACES[space][0])
            if newest_version > 1:
                versions.append(newest_version)
        except ApiError:
            pass
        for version in versions:
            try:
                year = self._oldest_year(space, version)
            except ApiError:
                year = None
            if year is not None:
                return year
        return None

    def _oldest_year(self, space, version):
        def first(direction):
            data = self.call(SPACES[space][0], "list", version, offset=0, limit=1,
                             sort_by="takentime", sort_direction=direction)
            items = data.get("list") or []
            t = items[0].get("time") if items else None
            return t if isinstance(t, int) else None
        oldest, newest = first("asc"), first("desc")
        if oldest is None or newest is None or oldest > newest:
            return None  # sorting not honoured
        if oldest == newest:
            return utc_date(oldest).year if self.single_item(space) else None
        return utc_date(oldest).year

    def single_item(self, space):
        data = self.call(SPACES[space][0], "list", 1, offset=0, limit=2)
        return len(data.get("list") or []) == 1

    def max_version(self, api):
        data = self.call("SYNO.API.Info", "query", 1, query=api)
        return int((data.get(api) or {}).get("maxVersion") or 1)

    def _list_range(self, space, lo, hi):
        items, offset = [], 0
        for _ in range(self.MAX_PAGES):
            data = self.list_items(SPACES[space][0], self.list_version[space],
                                   offset=offset, limit=self.PAGE, start_time=lo, end_time=hi)
            batch = data.get("list") or []
            for r in batch:
                t = r.get("time")
                if isinstance(t, int) and not (lo - DAY <= t <= hi + DAY):
                    raise FilterIgnored()
            items.extend(batch)
            if len(batch) < self.PAGE:
                return items
            offset += self.PAGE
        raise ApiError("more than %d items for one day in the %s space"
                       % (self.PAGE * self.MAX_PAGES, space))

    def list_day(self, space, day):
        """Raw items of one space whose capture date is `day`."""
        start = calendar.timegm(day.timetuple())
        lo, hi = start - DAY, start + 2 * DAY  # generous window, exact filter below
        while True:
            try:
                raw = self._list_range(space, lo, hi)
                break
            except FilterIgnored:
                if self.list_version[space] != 1:
                    raise ApiError("Synology Photos ignored the date filter (%s space)" % space)
                newer = self.max_version(SPACES[space][0])
                if newer <= 1:
                    raise ApiError("Synology Photos ignored the date filter (%s space)" % space)
                log("date filter not honoured by list v1, retrying with v%d" % newer)
                self.list_version[space] = newer
        return [r for r in raw if isinstance(r.get("time"), int) and utc_date(r["time"]) == day]

    def shared_albums(self):
        """Albums other users shared with this user (share codes included)."""
        out, offset = [], 0
        for _ in range(self.MAX_PAGES):
            data = self.call("SYNO.Foto.Sharing.Misc", "list_shared_with_me_album", 1,
                             offset=offset, limit=100)
            batch = data.get("list") or []
            out.extend(batch)
            if len(batch) < 100:
                break
            offset += 100
        return out

    def own_albums(self):
        """The user's own albums (normal and conditional)."""
        err = None
        for version in (2, 1):
            out, offset = [], 0
            try:
                for _ in range(self.MAX_PAGES):
                    data = self.call("SYNO.Foto.Browse.Album", "list", version, offset=offset, limit=100)
                    batch = data.get("list") or []
                    out.extend(batch)
                    if len(batch) < 100:
                        break
                    offset += 100
                return out
            except ApiError as e:
                err = e
        raise err

    def album_items(self, passphrase=None, album_id=None):
        """All photos of an album: a shared one (with its share code) or one of the user's own."""
        out, offset = [], 0
        key = {"passphrase": passphrase} if passphrase else {"album_id": album_id}
        for _ in range(self.MAX_PAGES):
            data = self.list_items("SYNO.Foto.Browse.Item", 1, offset=offset, limit=self.PAGE, **key)
            batch = data.get("list") or []
            out.extend(batch)
            if len(batch) < self.PAGE:
                break
            offset += self.PAGE
        return out

    def my_id(self):
        return self.call("SYNO.Foto.UserInfo", "me", 1).get("id")

    def folder_name(self, space, folder_id):
        key = (space, folder_id)
        if key not in self._folders:
            data = self.call(SPACES[space][1], "get", 1, id=folder_id)
            self._folders[key] = (data.get("folder") or {}).get("name") or "/"
        return self._folders[key]


# --------------------------------------------------------------------------
# Finding and choosing memories

# A date at the start of a folder or album name: "2021-09 ", "2000-04-25 ",
# "1982 ", "2003-2021 "
NAME_DATE_RE = re.compile(r"^(?:18|19|20)\d{2}(?:[-_.]\d{1,2}){0,2}"
                          r"(?:\s*[-–]\s*(?:18|19|20)\d{2})?(?!\d)[\s\-–·:_]*")


def strip_date(name):
    return NAME_DATE_RE.sub("", name.strip(), count=1).strip()


def rating_of(raw):
    extra = raw.get("additional")
    if not isinstance(extra, dict):
        extra = {}
    value = extra.get("rating", raw.get("rating"))
    try:
        value = int(value)
    except (TypeError, ValueError):
        return 0
    return value if 0 <= value <= 5 else 0


class Item(object):
    __slots__ = ("id", "filename", "time", "space", "kind", "folder", "folder_id", "thumb", "big",
                 "root", "album", "album_id", "album_own", "album_code", "album_item_id", "rating", "backup")

    def __init__(self, raw, space):
        self.id = raw.get("id")
        self.folder_id = raw.get("folder_id")
        self.filename = str(raw.get("filename") or "")
        self.time = raw["time"]
        self.space = space
        self.kind = raw.get("type") or "photo"
        self.folder = "/"
        self.thumb = None      # medium thumbnail (rows layout, news)
        self.big = None        # largest thumbnail (cut to fit in the mosaic)
        self.root = None
        self.album = ""
        self.album_id = None      # the album the photo opens in
        self.album_own = False    # one of the user's own albums (else shared with them)
        self.album_code = ""      # share code of an album shared with the user
        self.album_item_id = None
        self.rating = rating_of(raw)
        self.backup = False

    def parts(self):
        return [p for p in self.folder.strip("/").split("/") if p]

    def label(self):
        if self.album:
            return self.album
        parts = self.parts()
        if not parts:
            return ""
        if parts[0].lower() == "mobilebackup" and len(parts) > 1:
            return parts[1]  # the phone's backup folder, e.g. "Galaxy S25"
        return parts[0]

    def place(self, backup_folders):
        """Name for the year heading: the album, or the top folder without its
        leading date ("2019-07 Lake Trip" -> "Lake Trip"). Nothing for
        the phone backup."""
        if self.album:
            return strip_date(self.album) or self.album
        for part in self.parts():
            if part.lower() in backup_folders:
                return ""
            name = strip_date(part)
            if name:
                return name
        return ""


def target_days(today):
    days = [(today.month, today.day)]
    if (today.month, today.day) == (2, 28) and not calendar.isleap(today.year):
        days.append((2, 29))  # leap-day photos get shown on 28 February
    return days


def excluded(item, cfg):
    for pat in cfg.name_patterns:
        if pat.search(item.filename):
            return True
    parts = [p.lower() for p in item.parts()]
    # a word matches inside a folder name: "WhatsApp" catches "WhatsApp Images"
    return any(name in part for part in parts for name in cfg.folder_names)


def in_backup(item, cfg):
    """Is the item in a raw phone-backup folder (e.g. MobileBackup)?"""
    if item.album:
        return False
    return any(part.lower() in cfg.backup_folders for part in item.parts())


FOLDER_YEAR_RE = re.compile(r"^((?:19|20)\d{2})(?:\s*[-–]\s*((?:19|20)\d{2}))?(?!\d)")


def date_mismatch(item, year):
    """True when the photo is dated after the year(s) its album folder starts with, e.g. 2024 in
    '2000-04-25 Wedding': a scan carrying its scan date. Older photos in a later album are kept."""
    parts = item.parts()
    if not parts or parts[0].lower() == "mobilebackup":
        return False
    m = FOLDER_YEAR_RE.match(parts[0])
    if not m:
        return False
    first = int(m.group(1))
    last = int(m.group(2)) if m.group(2) else first
    if last < first:
        first, last = last, first
    return year > last + 1


def inside(path, root):
    return os.path.realpath(path).startswith(root.rstrip("/") + "/")


def find_thumb(root, item, names=THUMB_NAMES):
    """Path of an existing Synology thumbnail for item, or None."""
    folder = item.folder.strip("/")
    if "/" in item.filename or item.filename in ("", ".", ".."):
        return None
    base = os.path.join(root, folder, "@eaDir", item.filename)
    for name in names:
        path = os.path.join(base, name)
        if os.path.isfile(path) and not os.path.islink(path) and inside(path, root):
            return path
    return None


def read_thumb(path, root):
    """Read a thumbnail safely: inside root, a regular file, a real JPEG."""
    if not inside(path, root):
        raise ValueError("outside photo root")
    fd = os.open(os.path.realpath(path), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(fd, "rb") as f:
        st = os.fstat(f.fileno())
        if not stat.S_ISREG(st.st_mode) or st.st_size > MAX_THUMB_BYTES:
            raise ValueError("not a regular file or too large")
        data = f.read(MAX_THUMB_BYTES + 1)
    if not data.startswith(b"\xff\xd8\xff"):
        raise ValueError("not a JPEG")
    return data


def jpeg_size(data):
    """(width, height) as displayed, from the JPEG header and EXIF orientation."""
    size, orientation, i = None, 1, 2
    while i + 4 <= len(data) and size is None:
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        if marker == 0xFF:
            i += 1
            continue
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        seg_len = int.from_bytes(data[i + 2:i + 4], "big")
        seg = data[i + 4:i + 2 + seg_len]
        if marker == 0xE1 and seg[:6] == b"Exif\0\0":
            orientation = exif_orientation(seg[6:]) or 1
        elif marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
            if len(seg) >= 5:
                size = (int.from_bytes(seg[3:5], "big"), int.from_bytes(seg[1:3], "big"))
        i += 2 + seg_len
    if size and orientation in (5, 6, 7, 8):
        size = (size[1], size[0])
    return size


def exif_orientation(tiff):
    try:
        order = {b"II": "little", b"MM": "big"}[tiff[:2]]
        ifd = int.from_bytes(tiff[4:8], order)
        count = int.from_bytes(tiff[ifd:ifd + 2], order)
        for k in range(count):
            entry = tiff[ifd + 2 + 12 * k: ifd + 14 + 12 * k]
            if int.from_bytes(entry[0:2], order) == 0x0112:
                return int.from_bytes(entry[8:10], order)
    except (KeyError, IndexError, ValueError):
        pass
    return None


def rank(item, cfg):
    """Higher is better: star rating first, then albums and own folders over the phone backup."""
    return (item.rating if cfg.prefer_starred else 0, 0 if item.backup else 1)


def spread(items, n, turn=None):
    """n of the time-sorted items, one from each of n equal parts of the day: the middle one, or
    with `turn` (the year) the next one each year, so every photo gets its turn."""
    if len(items) <= n:
        return list(items)
    step = len(items) / float(n)
    out = []
    for k in range(n):
        if turn is None:
            out.append(items[int(step * k + step / 2)])
        else:
            lo, hi = int(step * k), int(step * (k + 1))
            out.append(items[lo + turn % max(1, hi - lo)])
    return out


def pick_best(items, n, cfg, turn=None, gap=60):
    """Up to n items of one year. Shots within `gap` seconds are one moment, represented by its
    best shot. Moments are taken by rank group, each group spread over the day."""
    items = sorted(items, key=lambda i: (i.time, i.filename))
    bursts = []
    for it in items:
        if bursts and it.time - bursts[-1][0].time < gap:
            bursts[-1].append(it)
        else:
            bursts.append([it])
    moments = [max(b, key=lambda i: rank(i, cfg)) for b in bursts]  # ties: the first shot
    chosen = []
    for level in sorted(set(rank(m, cfg) for m in moments), reverse=True):
        need = n - len(chosen)
        if need <= 0:
            break
        chosen.extend(spread([m for m in moments if rank(m, cfg) == level], need, turn))
    return chosen


def allocate(by_year, per_year, max_total, cfg, turn=None):
    """Places per year: round-robin, newest year first, up to max_total. Each year's photos are
    then picked for exactly its places."""
    years = sorted(by_year, reverse=True)
    possible = {y: len(pick_best(by_year[y], per_year, cfg)) for y in years}
    places = {y: 0 for y in years}
    total = 0
    for r in range(per_year):
        for y in years:
            if total < max_total and r < possible[y]:
                places[y] += 1
                total += 1
    return [(y, sorted(pick_best(by_year[y], places[y], cfg, turn),
                       key=lambda i: (i.time, i.filename)))
            for y in years if places[y]]


def collect(api, cfg, roots, today, stats, albums=None, locator=None):
    by_year, seen = {}, set()
    for space, root in roots.items():
        first_year = cfg.first_year
        if first_year is None:  # auto: start at the oldest photo in this space
            first_year = api.oldest_year(space)
            if first_year is None:
                first_year = AUTO_FALLBACK_YEAR
                log("note: %s space, oldest photo unknown, searching back to %d"
                    % (space, first_year))
            stats.setdefault("from", []).append("%s from %d" % (space, first_year))
        stop = False
        for year in range(today.year - 1, first_year - 1, -1):
            if stop:
                break
            for month, mday in target_days(today):
                try:
                    day = dt.date(year, month, mday)
                except ValueError:
                    continue
                try:
                    raws = api.list_day(space, day)
                except ApiError as e:
                    if year >= 1970:
                        raise
                    # dates before 1970 are negative timestamps; keep what we have if refused
                    log("note: %s space, dates before 1970 not searchable (%s)" % (space, e))
                    stop = True
                    break
                for raw in raws:
                    item = Item(raw, space)
                    stats["found"] += 1
                    if item.kind == "video" and not cfg.include_videos:
                        stats["skipped_video"] += 1
                        continue
                    key = (item.filename.lower(), item.time)
                    if key in seen:
                        continue
                    seen.add(key)
                    if raw.get("folder_id") is not None:
                        item.folder = api.folder_name(space, raw["folder_id"])
                    if excluded(item, cfg):
                        stats["excluded"] += 1
                        continue
                    if cfg.skip_date_mismatch and date_mismatch(item, year):
                        stats["date_mismatch"] += 1
                        stats.setdefault("mismatched", []).append(item)
                        continue
                    item.thumb = find_thumb(root, item)
                    item.root = root
                    if not item.thumb:
                        stats["no_thumb"] += 1
                        continue
                    item.big = find_thumb(root, item, BIG_THUMB_NAMES)
                    item.backup = in_backup(item, cfg)
                    if item.rating:
                        stats["starred"] += 1
                    by_year.setdefault(year, []).append(item)
    if cfg.shared_albums and albums:
        collect_albums(cfg, roots, today, stats, seen, by_year, albums,
                       locator or Locator(api, cfg, roots))
    return by_year


def personal_root(user):
    """Realpath of a user's Photos folder (Personal Space), or None."""
    try:
        home = pwd.getpwnam(user).pw_dir
    except KeyError:
        return None
    path = os.path.join(home, "Photos")
    if os.path.islink(path) or not os.path.isdir(path):
        return None
    return os.path.realpath(path)


def owner_names(cfg, api):
    """Synology Photos user id -> DSM user name, for the users in the config."""
    names = {api.user}
    for section in cfg.cp.sections():
        if section.lower().startswith("user:"):
            names.add(section.split(":", 1)[1].strip())
    ids = {}
    for name in sorted(names):
        if not USER_RE.match(name):
            continue
        try:
            pwd.getpwnam(name)
            uid = PhotosApi(api.binary, name).my_id()
        except (KeyError, ApiError):
            continue
        if uid is not None:
            ids[uid] = name
    return ids


class Locator(object):
    """Where an album's photo lives on disk: its folder, as its owner sees it."""

    def __init__(self, api, cfg, roots):
        self.api, self.cfg, self.roots = api, cfg, roots
        self.api_me = None
        self.owners = None
        self.owner_apis, self.owner_roots = {}, {}

    def names(self):
        if self.owners is None:
            self.owners = owner_names(self.cfg, self.api)
        return self.owners

    def place(self, item, raw):
        """Sets item.folder; returns the root the item's folder is in, or None."""
        owner_id = raw.get("owner_user_id")
        try:
            if owner_id == 0:  # photo from the Shared Space
                root = self.roots.get("shared")
                if root and raw.get("folder_id") is not None:
                    item.folder = self.api.folder_name("shared", raw["folder_id"])
                return root
            owner = self.names().get(owner_id)
            if owner and owner not in self.owner_roots:
                self.owner_roots[owner] = personal_root(owner)
                self.owner_apis[owner] = self.api if owner == self.api.user else PhotosApi(self.api.binary, owner)
            root = self.owner_roots.get(owner) if owner else None
            if root and raw.get("folder_id") is not None:
                item.folder = self.owner_apis[owner].folder_name("personal", raw["folder_id"])
            return root
        except ApiError:
            return None


def collect_albums(cfg, roots, today, stats, seen, by_year, albums, locator):
    """Photos from albums others shared with the user. The files are in the owner's Personal Space."""
    targets = target_days(today)
    for album in albums.values():
        if not album["shared"] or not album["items"]:
            continue
        for raw in album["items"].values():
            t = raw.get("time")
            if not isinstance(t, int):
                continue
            day = utc_date(t)
            if day.year >= today.year or (day.month, day.day) not in targets:
                continue
            if cfg.first_year and day.year < cfg.first_year:
                continue
            item = Item(raw, "album")
            item.album = album["name"]
            item.album_id = album["id"]
            item.album_code = album["code"]
            item.album_item_id = item.id
            stats["found"] += 1
            stats["album"] = stats.get("album", 0) + 1
            if item.kind == "video" and not cfg.include_videos:
                stats["skipped_video"] += 1
                continue
            key = (item.filename.lower(), t)
            if key in seen:
                continue
            seen.add(key)
            root = locator.place(item, raw)
            if not root:
                stats["no_thumb"] += 1
                continue
            if excluded(item, cfg):
                stats["excluded"] += 1
                continue
            if cfg.skip_date_mismatch and date_mismatch(item, day.year):
                stats["date_mismatch"] += 1
                stats.setdefault("mismatched", []).append(item)
                continue
            item.thumb = find_thumb(root, item)
            item.root = root
            if not item.thumb:
                stats["no_thumb"] += 1
                continue
            item.big = find_thumb(root, item, BIG_THUMB_NAMES)
            if item.rating:
                stats["starred"] += 1
            by_year.setdefault(day.year, []).append(item)


# --------------------------------------------------------------------------
# Albums: read once per run, for album photos, album-first links and news

def norm_album(name):
    return " ".join(str(name or "").lower().split())


def read_albums(api, me, stats):
    """Albums the user sees, own and shared with them, with their photos:
    ({id: {"id", "name", "owner", "shared", "code", "smart", "items"}}, complete).
    complete is False if a list couldn't be read; no news then, to avoid false warnings."""
    out, complete = {}, me is not None
    try:
        own = api.own_albums()
    except ApiError as e:
        log("note: own albums not readable (%s)" % e)
        own, complete = [], False
    for a in own:
        if me is not None and a.get("owner_user_id") not in (None, me):
            continue
        try:
            raws = api.album_items(album_id=a.get("id"))
        except ApiError as e:
            log("note: album %s not readable (%s)" % (a.get("name"), e))
            stats["albums_unreadable"] = stats.get("albums_unreadable", 0) + 1
            raws = None
        aid = str(a.get("id"))
        out[aid] = {"id": aid, "name": str(a.get("name") or ""), "owner": me, "shared": False,
                    "code": str(a.get("passphrase") or ""),
                    "smart": str(a.get("type") or "").lower() == "condition",
                    "items": None if raws is None else {str(r.get("id")): r for r in raws}}
    try:
        shared = api.shared_albums()
    except ApiError as e:
        log("note: shared albums not readable (%s)" % e)
        shared, complete = [], False
    for a in shared:
        if not a.get("passphrase"):
            continue
        try:
            raws = api.album_items(a["passphrase"])
        except ApiError as e:
            log("note: album %s not readable (%s)" % (a.get("name"), e))
            stats["albums_unreadable"] = stats.get("albums_unreadable", 0) + 1
            raws = None
        aid = str(a.get("id"))
        out[aid] = {"id": aid, "name": str(a.get("name") or ""), "owner": a.get("owner_user_id"),
                    "shared": True, "code": str(a.get("passphrase")),
                    "smart": str(a.get("type") or "").lower() == "condition",
                    "items": None if raws is None else {str(r.get("id")): r for r in raws}}
    return out, complete


def album_index(albums):
    """photo -> (album, the album's item), by item id and by (file name, time). In several albums:
    the shortest time span wins (the trip, not a collection of years), then the fewest photos."""
    def span(a):
        times = [r.get("time") for r in a["items"].values() if isinstance(r.get("time"), int)]
        return (max(times) - min(times)) if times else 0

    by_id, by_key = {}, {}
    ordered = sorted((a for a in albums.values() if a["items"]),
                     key=lambda a: (span(a) // DAY, len(a["items"]), norm_album(a["name"])))
    for a in ordered:
        for iid, raw in a["items"].items():
            by_id.setdefault(iid, (a, raw))
            t, name = raw.get("time"), raw.get("filename")
            if isinstance(t, int) and name:
                by_key.setdefault((str(name).lower(), t), (a, raw))
    return by_id, by_key


def tag_albums(by_year, albums):
    """Albums first: a folder photo that is also in an album the user sees gets the album's name
    and link. Matched by item id, or by file name and time (a copy)."""
    by_id, by_key = album_index(albums)
    count = 0
    for items in by_year.values():
        for item in items:
            if item.album:
                continue                    # already from a shared album
            hit = by_id.get(str(item.id)) or by_key.get((item.filename.lower(), item.time))
            if not hit:
                continue
            album, raw = hit
            item.album = album["name"]
            item.album_id = album["id"]
            item.album_own = not album["shared"]
            item.album_code = album["code"]
            item.album_item_id = raw.get("id")
            item.backup = False             # in an album: chosen like the own folders
            count += 1
    return count


def album_positions(chosen, albums):
    """For the log: each album photo's number in its album, by date ('Lake Trip: 212 of 480').
    Synology Photos lets you swipe only from photos early in an album."""
    order, found = {}, OrderedDict()
    for item in chosen:
        a = albums.get(str(item.album_id)) if item.album_id is not None else None
        if not a or not a["items"]:
            continue
        if a["id"] not in order:
            raws = sorted(a["items"].values(), key=lambda r: (r.get("time") or 0, str(r.get("filename") or "")))
            order[a["id"]] = {str(r.get("id")): k + 1 for k, r in enumerate(raws)}
        pos = order[a["id"]].get(str(item.album_item_id))
        if pos:
            found.setdefault((a["name"] + (" (smart album)" if a.get("smart") else ""), len(a["items"])),
                             []).append(pos)
    return "; ".join("%s: %s of %d" % (name, ", ".join(str(p) for p in sorted(ps)), n)
                     for (name, n), ps in found.items())


# --------------------------------------------------------------------------
# New in your albums: what changed since the last email

def state_path(cfg, user, kind="news"):
    return os.path.join(cfg.state_dir, "%s-%s.json" % (kind, user))


def load_news_state(cfg, user, kind="news"):
    try:
        with open(state_path(cfg, user, kind), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def save_news_state(cfg, user, snap, today, old=None, kind="news"):
    """Only what the next run needs: album name, owner and item ids (root only)."""
    os.makedirs(cfg.state_dir, mode=0o700, exist_ok=True)
    data = {"date": today.isoformat(), "time": int(time.time()), "albums": {}}
    for aid, a in snap.items():
        if a["items"] is None:
            # not readable today: keep the last notes, compare again next time
            prev = ((old or {}).get("albums") or {}).get(aid)
            if prev:
                data["albums"][aid] = prev
            continue
        data["albums"][aid] = {"name": a["name"], "owner": a["owner"], "shared": a["shared"],
                               "items": sorted(a["items"])}
    path = state_path(cfg, user, kind)
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, path)


def pair_albums(old, new):
    """[(old id or None, new id or None)]: the same album by id, else by name
    (an album made again, e.g. after it was deleted, has a new id)."""
    pairs, left_old = [], dict(old)
    unmatched = []
    for aid in new:
        if aid in left_old:
            pairs.append((aid, aid))
            del left_old[aid]
        else:
            unmatched.append(aid)
    by_name = {}
    for aid, a in left_old.items():
        by_name.setdefault(norm_album(a.get("name")), []).append(aid)
    for aid in unmatched:
        cands = by_name.get(norm_album(new[aid]["name"])) or []
        if cands:
            o = cands.pop(0)
            del left_old[o]
            pairs.append((o, aid))
        else:
            pairs.append((None, aid))
    pairs += [(aid, None) for aid in left_old]
    return pairs


class News(object):
    def __init__(self, text, items=(), more=0, warn=False):
        self.text, self.items, self.more, self.warn = text, list(items), more, warn


def build_news(cfg, roots, me, old, snap, locator, tool=None):
    """[News]: photos others added to albums the user sees, albums newly shared with the user,
    the user's own albums that are gone or lost photos. With `tool` ({"added": {album id: {item
    ids}}, "created": {album ids}}, from the Album Tool's journals) also what that tool did
    for the user; what the user added by hand stays out."""
    t = cfg.texts
    names = locator.names()
    tool = tool or {"added": {}, "created": set()}
    TOOL = object()

    def who(uid):
        if uid is TOOL:
            return t["album_tool"]
        return names.get(uid) or t["someone"]

    out = []
    for oid, nid in pair_albums(old.get("albums") or {}, snap):
        o = old["albums"].get(oid) if oid else None
        n = snap.get(nid) if nid else None
        if n is not None and n["items"] is None:
            continue                               # not readable today
        if o is None:
            if n["shared"] and n["owner"] != me:
                out.append(News(fmt(t["news_new_album"], who=who(n["owner"]), album=n["name"],
                                    n=len(n["items"])), news_items(n, list(n["items"]), cfg, roots, locator),
                                max(0, len(n["items"]) - cfg.news_photos)))
            elif not n["shared"] and nid in tool["created"]:
                out.append(News(fmt(t["news_tool_new_album"], who=who(TOOL), album=n["name"],
                                    n=len(n["items"])), news_items(n, list(n["items"]), cfg, roots, locator),
                                max(0, len(n["items"]) - cfg.news_photos)))
            continue
        if n is None:
            if o.get("shared") and o.get("owner") != me:
                out.append(News(fmt(t["news_gone_shared"], who=who(o.get("owner")), album=o.get("name")), warn=True))
            elif not o.get("shared"):
                out.append(News(fmt(t["news_gone_own"], album=o.get("name"), n=len(o.get("items") or [])),
                                warn=True))
            continue
        before = set(str(x) for x in o.get("items") or [])
        added = [i for i in n["items"] if i not in before]
        removed = [i for i in before if i not in n["items"]]
        by_who = {}
        for i in added:
            raw = n["items"][i]
            owner = raw.get("owner_user_id")
            adder = n["owner"] if owner in (0, None) else owner     # a Shared Space photo: the album's owner
            if adder == me:
                if i not in tool["added"].get(nid, ()):
                    continue                       # what you added yourself
                adder = TOOL
            by_who.setdefault(adder, []).append(i)
        for adder, ids in sorted(by_who.items(), key=lambda kv: -len(kv[1])):
            key = "news_added_one" if len(ids) == 1 else "news_added_many"
            out.append(News(fmt(t[key], who=who(adder), n=len(ids), album=n["name"]),
                            news_items(n, ids, cfg, roots, locator), max(0, len(ids) - cfg.news_photos)))
        if removed and not n["shared"]:
            key = "news_removed_one" if len(removed) == 1 else "news_removed_many"
            out.append(News(fmt(t[key], n=len(removed), album=n["name"]), warn=True))
    return out


def news_items(album, ids, cfg, roots, locator):
    """Up to news_photos Items with a thumbnail for one news line, newest first."""
    raws = sorted((album["items"][i] for i in ids), key=lambda r: -(r.get("time") or 0))
    out = []
    for raw in raws:
        if len(out) >= cfg.news_photos:
            break
        if not isinstance(raw.get("time"), int):
            continue
        owner = raw.get("owner_user_id")
        if album["shared"]:
            space = "album"
        elif owner == 0:
            space = "shared"
        elif owner == locator.api_me:
            space = "personal"
        else:
            space = "other"                        # another user's photo in your album
        item = Item(raw, space)
        item.album = album["name"]
        item.album_id = album["id"]
        item.album_own = not album["shared"]
        item.album_code = album["code"]
        item.album_item_id = item.id
        root = locator.place(item, raw)
        if not root:
            continue
        item.thumb = find_thumb(root, item)
        item.root = root
        if item.thumb:
            out.append(item)
    return out


# --------------------------------------------------------------------------
# Layout "rows": rows of equal height, photos uncropped ("justified" grid)

GRID_PX = 552.0      # width of the photo area (600px card minus 24px on each side)
GAP_PX = 6.0         # space between photos
ROW_TARGET = 180.0   # preferred row height
ROW_MIN = 120.0      # a row of several photos is never lower than this
ROW_MAX = 260.0      # nor higher; such a row keeps its height and leaves space on the right
MAX_PER_ROW = 4
REORDER_PENALTY = 0.05  # keep the time order unless grouping shapes is clearly better


def _row(ratios):
    """(cost, height) of one row with photos of these width/height ratios,
    or None if the photos would get too small."""
    k = len(ratios)
    total = sum(ratios)
    h = (GRID_PX - GAP_PX * (k - 1)) / total
    if h < ROW_MIN and k > 1:
        return None
    shown = min(h, ROW_MAX)
    unused = GRID_PX - GAP_PX * (k - 1) - shown * total
    cost = ((shown - ROW_TARGET) / ROW_TARGET) ** 2 + 2.0 * (unused / GRID_PX) ** 2
    return cost, shown


def _rows(ratios):
    """Best split of the photos (in this order) into rows: (cost, [(start, end, height)])."""
    n = len(ratios)
    best = [0.0] + [float("inf")] * n
    back = [None] * (n + 1)
    for j in range(1, n + 1):
        for k in range(1, min(MAX_PER_ROW, j) + 1):
            r = _row(ratios[j - k:j])
            if r is None:
                continue
            if best[j - k] + r[0] < best[j]:
                best[j] = best[j - k] + r[0]
                back[j] = (j - k, r[1])
    rows, j = [], n
    while j > 0:
        i, h = back[j]
        rows.append((i, j, h))
        j = i
    return best[n], rows[::-1]


def justify(items, ratio):
    """Rows [(items, height)] for one year. Keeps the time order unless putting
    landscape and portrait photos together makes clearly better rows."""
    wide = [i for i in items if ratio(i) >= 0.9]
    tall = [i for i in items if ratio(i) < 0.9]
    orders = [(0.0, list(items))]
    if wide and tall:
        orders += [(REORDER_PENALTY, wide + tall), (REORDER_PENALTY, tall + wide)]
    best = None
    for penalty, order in orders:
        cost, rows = _rows([ratio(i) for i in order])
        cost += penalty
        if best is None or cost < best[0]:
            best = (cost, [(order[a:b], h) for a, b, h in rows])
    return best[1]


# --------------------------------------------------------------------------
# Layout "mosaic": fixed frames like OneDrive's memories, photos cut to fit

MOSAIC_MAX = 5          # photos per frame; a year with more gets several frames
HEIGHT_WEIGHT = 0.3     # prefer compact frames ...
HERO_BONUS = 0.15       # ... but one large photo when it fits as well
ORDER_WEIGHT = 0.01     # keep the time order when all else is equal
BEST_IN_HERO = 0.2      # the most wanted photo (e.g. starred) in the large place
CROP_FOCUS_Y = 0.4      # when cutting height, keep a bit more of the top (faces, skyline)


class Box(object):
    """A place in a frame: a photo ("s"), a row ("r") or a column ("c") of places."""
    __slots__ = ("kind", "kids", "w", "h")

    def __init__(self, kind, kids, w, h):
        self.kind, self.kids, self.w, self.h = kind, list(kids), float(w), float(h)


def S(w, h):
    return Box("s", (), w, h)


def R(*kids):
    return Box("r", kids, sum(k.w for k in kids) + GAP_PX * (len(kids) - 1), max(k.h for k in kids))


def C(*kids):
    return Box("c", kids, max(k.w for k in kids), sum(k.h for k in kids) + GAP_PX * (len(kids) - 1))


def leaves(box):
    if box.kind == "s":
        return [box]
    return [leaf for kid in box.kids for leaf in leaves(kid)]


def mirrored(box):
    if box.kind == "s":
        return S(box.w, box.h)
    kids = [mirrored(k) for k in box.kids]
    return R(*kids[::-1]) if box.kind == "r" else C(*kids)


def templates(n):
    """[(name, frame, has a large photo)] for n photos; widths add up to GRID_PX.
    Shapes: 3:2 and 4:3 landscape, 3:4 portrait, square."""
    half = (GRID_PX - GAP_PX) / 2                 # 273
    third = (GRID_PX - 2 * GAP_PX) / 3            # 180
    quarter = (GRID_PX - 3 * GAP_PX) / 4          # 133.5
    tall = half / 0.75                            # 364: a 3:4 portrait, half width
    stack = (tall - GAP_PX) / 2                   # 179: two photos stacked next to it
    lp_h = (GRID_PX - GAP_PX) / 2.25              # a 3:2 landscape and a 3:4 portrait side by side

    def wide(r):
        return S(GRID_PX, GRID_PX / r)

    def pair(r):
        return R(S(half, half / r), S(half, half / r))

    def trio(r):
        return R(S(third, third / r), S(third, third / r), S(third, third / r))

    def hero():                                   # OneDrive: tall photo left, two stacked right
        return R(S(half, tall), C(S(half, stack), S(half, stack)))

    def hero4():
        return R(S(half, tall), C(S(half, stack), R(S(quarter, stack), S(quarter, stack))))

    def lp():
        return R(S(lp_h * 1.5, lp_h), S(lp_h * 0.75, lp_h))

    def pl():
        return R(S(lp_h * 0.75, lp_h), S(lp_h * 1.5, lp_h))

    if n == 1:
        return [("wide", wide(1.5), True), ("wide43", wide(4 / 3.0), True), ("square", wide(1.0), True)]
    if n == 2:
        return [("pair32", pair(1.5), False), ("pair43", pair(4 / 3.0), False), ("pair11", pair(1.0), False),
                ("pair34", pair(0.75), False), ("lp", lp(), False)]
    if n == 3:
        h3 = (GRID_PX - 2 * GAP_PX) / 3.0         # a 3:2 and two 3:4 side by side
        return [("hero", hero(), True),
                ("top", C(wide(1.5), pair(1.5)), True),
                ("top43", C(wide(4 / 3.0), pair(4 / 3.0)), True),
                ("trio34", trio(0.75), False),
                ("lpp", R(S(h3 * 1.5, h3), S(h3 * 0.75, h3), S(h3 * 0.75, h3)), False)]
    if n == 4:
        return [("hero4", hero4(), True),
                ("top3", C(wide(1.5), trio(1.5)), True),
                ("top3_43", C(wide(4 / 3.0), trio(4 / 3.0)), True),
                ("grid32", C(pair(1.5), pair(1.5)), False),
                ("grid43", C(pair(4 / 3.0), pair(4 / 3.0)), False),
                ("grid11", C(pair(1.0), pair(1.0)), False),
                ("checker", C(lp(), pl()), False),
                ("lppair", C(lp(), pair(1.5)), False),
                ("lppair43", C(lp(), pair(4 / 3.0)), False),
                ("quad34", R(*[S(quarter, quarter / 0.75) for _ in range(4)]), False)]
    return [("hero5", C(hero(), pair(1.5)), True),
            ("hero5lp", C(hero(), lp()), True),
            ("hero5_43", C(hero(), pair(4 / 3.0)), True),
            ("pairtrio", C(pair(1.5), trio(4 / 3.0)), False),
            ("pairtrio43", C(pair(4 / 3.0), trio(4 / 3.0)), False),
            ("lptrio", C(lp(), trio(4 / 3.0)), False),
            ("trio34pair", C(trio(0.75), pair(1.5)), False),
            ("quad34wide", C(R(*[S(quarter, quarter / 0.75) for _ in range(4)]), wide(1.5)), True),
            ("trio34pair34", C(trio(0.75), pair(0.75)), False)]


def crop_loss(photo, place):
    """Share of the photo cut away to fill a place of another shape (0 = none)."""
    return 1.0 - min(photo, place) / max(photo, place)


def chunks(items, size=MOSAIC_MAX):
    """Split a year's photos (in time order) into frames of at most `size`, evenly."""
    k = -(-len(items) // size)
    base, extra = divmod(len(items), k)
    out, i = [], 0
    for c in range(k):
        n = base + (1 if c < extra else 0)
        out.append(items[i:i + n])
        i += n
    return out


def plan_frame(items, ratio, cfg, mirror=False):
    """(name, frame, [(place, item)]): the template that cuts least, stays compact and has one
    large photo if it fits as well. Time order breaks ties."""
    wanted = [(i.rating if cfg.prefer_starred else 0) + (0 if i.backup else 0.5) for i in items]
    best = None
    for name, frame, hero in templates(len(items)):
        if mirror:
            frame = mirrored(frame)
        places = leaves(frame)
        big = max(range(len(places)), key=lambda k: places[k].w * places[k].h)
        base = HEIGHT_WEIGHT * frame.h / GRID_PX - (HERO_BONUS if hero else 0.0)
        for perm in itertools.permutations(range(len(items))):
            cost = base + sum(crop_loss(ratio(items[p]), pl.w / pl.h) for p, pl in zip(perm, places))
            cost += ORDER_WEIGHT * sum(1 for a in range(len(perm)) for b in range(a + 1, len(perm))
                                       if perm[a] > perm[b])
            if hero:
                cost += BEST_IN_HERO * (max(wanted) - wanted[perm[big]])
            if best is None or cost < best[0] - 1e-9:
                best = (cost, name, frame, [(pl, items[p]) for p, pl in zip(perm, places)])
    return best[1:]


def plan_sections(sections, ratio, cfg):
    """[[(name, frame, [(place, item)]) per frame] per year]. Years alternate the side of the large photo."""
    return [[plan_frame(chunk, ratio, cfg, mirror=n % 2 == 1) for chunk in chunks(items)]
            for n, (_, items) in enumerate(sections)]


def ffmpeg_candidates(cfg):
    """ffmpegs to try, best first: SynoCommunity packages (newest first), then DSM's own,
    which changed with DSM 7.2.2."""
    setting = cfg.ffmpeg
    if setting.lower() in ("", "no", "off", "none"):
        return []
    if setting.lower() != "auto":
        return [setting] if os.path.isfile(setting) and os.access(setting, os.X_OK) else []

    def newest_first(paths):
        def key(p):
            nums = re.findall(r"ffmpeg(\d+)", p)
            return int(nums[-1]) if nums else 0
        return sorted(paths, key=key, reverse=True)

    cands = []
    for pattern in ("/usr/local/bin/ffmpeg[0-9]*", "/var/packages/ffmpeg*/target/bin/ffmpeg",
                    "/volume*/@appstore/ffmpeg*/bin/ffmpeg"):
        cands += newest_first(glob.glob(pattern))
    cands += ["/usr/local/bin/ffmpeg", "/usr/bin/ffmpeg", "/bin/ffmpeg"]
    found = shutil.which("ffmpeg")
    if found:
        cands.append(found)
    out, seen = [], set()
    for path in cands:
        if os.path.isfile(path) and os.access(path, os.X_OK) and os.path.realpath(path) not in seen:
            seen.add(os.path.realpath(path))
            out.append(path)
    return out


def working_ffmpeg(cfg, pw, sample, place):
    """(Cropper, None) for the first ffmpeg that really cuts `sample`, or (None, reason)."""
    cands = ffmpeg_candidates(cfg)
    if not cands:
        return None, ("ffmpeg is switched off (ffmpeg = no)" if cfg.ffmpeg.lower() in ("no", "off", "none")
                      else "no ffmpeg found on the NAS")
    failed = []
    for path in cands:
        cropper = Cropper(path, pw)
        try:
            cropper.crop(sample, place.w, place.h)
            if failed:
                log("note: ffmpeg that could not cut: %s" % "; ".join(failed))
            return cropper, None
        except CropError as e:
            failed.append("%s: %s" % (path, e))
    return None, "ffmpeg could not cut the photos (%s)" % "; ".join(failed)


def play_badge(w, h):
    """ffmpeg drawbox filters for a small play sign in the lower left corner."""
    b = max(14, int(min(w, h) * 0.2))
    m = max(6, b // 3)
    x0, y0 = m, h - m - b
    parts = ["drawbox=x=%d:y=%d:w=%d:h=%d:color=black@0.45:t=%d" % (x0, y0, b, b, b)]
    tw, th = int(b * 0.42), int(b * 0.52)
    tx, ty = x0 + (b - tw) // 2 + b // 16, y0 + (b - th) // 2
    steps = 8
    for k in range(steps):
        sx = tx + tw * k // steps
        sw = max(1, tw // steps + 1)
        sh = int(th * (1 - (k + 0.5) / steps))
        if sh > 0:
            parts.append("drawbox=x=%d:y=%d:w=%d:h=%d:color=white@0.9:t=%d"
                         % (sx, ty + (th - sh) // 2, sw, sh, max(sw, sh)))
    return ",".join(parts)


class Cropper(object):
    """Cuts a thumbnail to a place's shape with ffmpeg. ffmpeg gets bytes through a pipe and runs
    as the user, not root."""
    SCALE = 2  # sharp on phone screens

    def __init__(self, binary, pw=None):
        self.binary, self.pw = binary, pw

    def _drop(self):
        if self.pw is not None and os.geteuid() == 0:
            os.setgroups([])
            os.setgid(self.pw.pw_gid)
            os.setuid(self.pw.pw_uid)

    def crop(self, data, w, h, video=False):
        ow, oh = int(round(w * self.SCALE)), int(round(h * self.SCALE))
        r = ow / float(oh)
        vf = ("crop=w='min(iw,ih*%.6f)':h='min(ih,iw/%.6f)':x='(iw-ow)/2':y='(ih-oh)*%.2f',"
              "scale=%d:%d:flags=lanczos" % (r, r, CROP_FOCUS_Y, ow, oh))
        if video:
            vf += "," + play_badge(ow, oh)
        vf += ",format=yuvj420p"
        args = [self.binary, "-hide_banner", "-loglevel", "error", "-f", "image2pipe", "-c:v", "mjpeg",
                "-i", "pipe:0", "-vf", vf, "-frames:v", "1", "-q:v", "4",
                "-f", "image2pipe", "-c:v", "mjpeg", "pipe:1"]
        try:
            proc = subprocess.run(args, input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  timeout=60, preexec_fn=self._drop)
        except (OSError, subprocess.TimeoutExpired, subprocess.SubprocessError) as e:
            raise CropError(str(e))
        out = proc.stdout
        if proc.returncode != 0 or not out.startswith(b"\xff\xd8\xff"):
            detail = proc.stderr.decode("utf-8", "replace").strip().splitlines()
            raise CropError(detail[-1][:200] if detail else "exit %d" % proc.returncode)
        return out


def load_big(sections, roots):
    """({id(item): largest thumbnail}, sections without unreadable photos)."""
    data, kept = {}, []
    for year, items in sections:
        ok = []
        for item in items:
            path = item.big or item.thumb
            try:
                data[id(item)] = read_thumb(path, item.root or roots[item.space])
                ok.append(item)
            except (OSError, ValueError, KeyError) as e:
                log("skipping %s: %s" % (path, e))
        if ok:
            kept.append((year, ok))
    return data, kept


def make_mosaic(cfg, sections, roots, pw):
    """((sections, plans, {id(item): cut JPEG}), ffmpeg used), or (None, reason) to fall back to rows."""
    data, sections = load_big(sections, roots)
    if not sections:
        return (sections, [], {}), ""
    sizes = {k: jpeg_size(v) for k, v in data.items()}

    def ratio(item):
        w, h = sizes.get(id(item)) or (3, 2)
        return min(10.0, max(0.1, w / float(h))) if h else 1.5

    plans = plan_sections(sections, ratio, cfg)
    place, first = plans[0][0][2][0]
    cropper, reason = working_ffmpeg(cfg, pw, data[id(first)], place)
    if cropper is None:
        return None, reason
    images = {}
    for frames in plans:
        for _, _, placed in frames:
            for place, item in placed:
                try:
                    images[id(item)] = cropper.crop(data[id(item)], place.w, place.h, item.kind == "video")
                except CropError as e:
                    return None, "ffmpeg (%s) could not cut %s (%s)" % (cropper.binary, item.filename, e)
    return (sections, plans, images), cropper.binary


def spacer_png(w, h):
    """Transparent w x h PNG: the gap between stacked photos. It scales with them, so the edges
    stay aligned on a phone."""
    raw = b"".join(b"\x00" + b"\x00" * (4 * w) for _ in range(h))

    def chunk(kind, body):
        return (struct.pack(">I", len(body)) + kind + body
                + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


# --------------------------------------------------------------------------
# Rendering

FONT = "-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"
SERIF = "Georgia,'Times New Roman',serif"


def caption(item, cfg):
    bits = [utc_hhmm(item.time)]
    label = item.label()
    if item.space == "shared" and not item.album:
        bits.append(cfg.texts["shared"] + (" · " + label if label else ""))
    elif label:
        bits.append(label)
    if item.kind == "video":
        bits.append("▶ " + cfg.texts["video"])
    return " · ".join(bits)


def year_places(cfg, items):
    """'Lake Trip · Harbor Walk' for a year's heading: albums/folders in time order."""
    names = []
    for item in sorted(items, key=lambda i: (i.time, i.filename)):
        name = item.place(cfg.backup_folders)
        if name and name not in names:
            names.append(name)
    if len(names) > 3:
        return " · ".join(names[:3] + [fmt(cfg.texts["more_places"], n=len(names) - 3)])
    return " · ".join(names)


def photo_url(cfg, item):
    """Link that opens the photo in Synology Photos, inside its album or folder:
      shared album          <photos_link>/#/sharing/<share code>/item/<id>   (album_links = yes)
      own album, not shared <photos_link>/#/album/<album id>/item/<id>
      folder                <photos_link>/#/personal_space/folder/<folder id>/item/<id>
                            (Shared Space: shared_space)"""
    base = cfg.photos_link.rstrip("/")
    if not base or item.id is None:
        return ""
    target = item.album_item_id if item.album_item_id is not None else item.id
    code = item.album_code or ""
    if code and cfg.album_links and re.match(r"^[A-Za-z0-9_-]+$", code):
        return "%s/#/sharing/%s/item/%s" % (base, code, target)
    if item.album_own and re.match(r"^\d+$", str(item.album_id or "")):
        return "%s/#/album/%s/item/%s" % (base, item.album_id, target)
    if item.folder_id is None or item.space not in ("personal", "shared"):
        return ""
    space = "shared_space" if item.space == "shared" else "personal_space"
    return "%s/#/%s/folder/%s/item/%s" % (base, space, item.folder_id, item.id)


def year_heading(cfg, today, year):
    n = today.year - year
    return fmt(cfg.texts["year_one" if n == 1 else "year_many"], n=n, year=year)


def build_html(cfg, today, sections, src_for, size_for, news=(), plans=None, spacer_src=None, note="",
               heading=None, summary=None, footer=None):
    """The page. With heading and summary given (What's New): the news on their own, no box."""
    count = sum(len(items) for _, items in sections)
    date_text = cfg.date_text(today)
    own_page = heading is not None
    heading = heading or cfg.texts["heading"]
    if summary is None and count:
        summary = fmt(cfg.texts["summary_one" if count == 1 else "summary_many"],
                      date=date_text, count=count)
    elif summary is None:
        summary = date_text
    e = html.escape

    def ratio(item):
        w, h = size_for(item) or (3, 2)
        return min(10.0, max(0.1, w / float(h))) if h else 1.5

    def pct(part, whole=GRID_PX):
        return "%.3f%%" % (100.0 * part / whole)

    def spacer(px, whole=GRID_PX):
        return ('<td width="%s" style="width:%s;padding:0;font-size:0;line-height:0;">&nbsp;</td>'
                % (pct(px, whole), pct(px, whole)))

    def table(cells):
        return ('<table role="presentation" width="100%%" cellpadding="0" cellspacing="0" border="0" '
                'style="width:100%%;table-layout:fixed;border-collapse:collapse;"><tr>%s</tr></table>'
                % "".join(cells))

    def photo(item, px, label=True):
        img = ('<img src="%s" width="%d" alt="%s" style="display:block;width:100%%;height:auto;'
               'border:0;outline:none;border-radius:6px;">'
               % (e(src_for(item), quote=True), round(px), e(item.filename, quote=True)))
        url = photo_url(cfg, item)
        if url:
            img = ('<a href="%s" style="display:block;text-decoration:none;">%s</a>'
                   % (e(url, quote=True), img))
        if label and item.kind == "video":
            img += ('<div style="font-family:%s;font-size:11px;line-height:15px;color:#7a7066;'
                    'padding:4px 2px 0;">&#9654;&nbsp;%s</div>' % (FONT, e(cfg.texts["video"])))
        return img

    def cell(item, px):
        return ('<td width="%s" valign="top" style="width:%s;padding:0;vertical-align:top;">%s</td>'
                % (pct(px), pct(px), photo(item, px)))

    def row_table(row_items, height):
        cells, used = [], 0.0
        for n, item in enumerate(row_items):
            if n:
                cells.append(spacer(GAP_PX))
                used += GAP_PX
            px = height * ratio(item)
            cells.append(cell(item, px))
            used += px
        if GRID_PX - used > 0.5:
            cells.append(spacer(GRID_PX - used))
        return table(cells)

    row_gap = '<div style="height:%dpx;line-height:%dpx;font-size:1px;">&nbsp;</div>' % (GAP_PX, GAP_PX)

    def gap_img(width):
        # gap between stacked photos: an image, so it scales with them on a phone
        return ('<img src="%s" width="%d" alt="" style="display:block;width:100%%;height:auto;'
                'border:0;outline:none;">' % (e(spacer_src(int(round(width))), quote=True), round(width)))

    def frame_html(box, where, nested=False):
        if box.kind == "s":
            return photo(where[id(box)], box.w, label=False)
        if box.kind == "r":
            cells = []
            for n, kid in enumerate(box.kids):
                if n:
                    cells.append(spacer(GAP_PX, box.w))
                cells.append('<td width="%s" valign="top" style="width:%s;padding:0;vertical-align:top;">%s</td>'
                             % (pct(kid.w, box.w), pct(kid.w, box.w), frame_html(kid, where, True)))
            return table(cells)
        out = []
        for n, kid in enumerate(box.kids):
            if n:
                out.append(gap_img(box.w) if nested and spacer_src else row_gap)
            out.append(frame_html(kid, where, nested))
        return "".join(out)

    parts = []
    if news and own_page:
        for k, nw in enumerate(news):
            color = "#9a4b2e" if nw.warn else "#1f1f1f"
            more = (" <span style=\"font-weight:400;color:#8a7866;\">%s</span>"
                    % e(fmt(cfg.texts["news_more"], n=nw.more)) if nw.more else "")
            parts.append('<tr><td style="padding:%dpx 24px 10px;font-family:%s;font-size:16px;line-height:21px;'
                         'font-weight:600;color:%s;">%s%s%s</td></tr>'
                         % (20 if k else 16, FONT, color, "&#9888;&nbsp;" if nw.warn else "", e(nw.text), more))
            if nw.items:
                rows = [row_table(r, h * 0.8) for r, h in justify(nw.items, ratio)]
                parts.append('<tr><td style="padding:0 24px;">%s</td></tr>' % row_gap.join(rows))
    elif news:
        parts.append('<tr><td style="padding:18px 24px 0;"><div style="background:#f7f3ed;border-radius:10px;'
                     'padding:14px 16px 12px;"><div style="font-family:%s;font-size:15px;line-height:20px;'
                     'font-weight:600;color:#1f1f1f;padding-bottom:4px;">%s</div>'
                     % (FONT, e(cfg.texts["news_heading"])))
        for nw in news:
            color = "#9a4b2e" if nw.warn else "#3d3a36"
            more = (" <span style=\"color:#8a7866;\">%s</span>" % e(fmt(cfg.texts["news_more"], n=nw.more))
                    if nw.more else "")
            parts.append('<div style="font-family:%s;font-size:14px;line-height:19px;color:%s;padding:8px 0 6px;">'
                         '%s%s%s</div>' % (FONT, color, "&#9888;&nbsp;" if nw.warn else "", e(nw.text), more))
            if nw.items:
                rows = [row_table(r, h * 0.6) for r, h in justify(nw.items, ratio)]
                parts.append(row_gap.join(rows))
        parts.append('</div></td></tr>')
    for n, (year, items) in enumerate(sections):
        if n:
            parts.append('<tr><td style="padding:26px 24px 0;"><div style="border-top:1px solid '
                         '#ebe4da;height:1px;line-height:1px;font-size:1px;">&nbsp;</div></td></tr>')
        places = year_places(cfg, items)
        head = ('<div style="font-family:%s;font-size:17px;line-height:22px;font-weight:600;'
                'color:#1f1f1f;">%s</div>' % (FONT, e(year_heading(cfg, today, year))))
        if places:
            head += ('<div style="font-family:%s;font-size:13px;line-height:18px;color:#8a7866;'
                     'padding-top:2px;">%s</div>' % (FONT, e(places)))
        parts.append('<tr><td style="padding:%dpx 24px 12px;">%s</td></tr>'
                     % (20 if n or news else 16, head))
        if plans is not None:
            blocks = [frame_html(frame, {id(place): item for place, item in placed})
                      for _, frame, placed in plans[n]]
        else:
            blocks = [row_table(r, h) for r, h in justify(items, ratio)]
        parts.append('<tr><td style="padding:0 24px;">%s</td></tr>' % row_gap.join(blocks))
    link = ""
    if cfg.photos_link:
        link = ('<p style="margin:0 0 10px;"><a href="%s" style="color:#8a5a2b;">%s</a></p>'
                % (e(cfg.photos_link, quote=True), e(cfg.texts["open_photos"])))
    if note:
        # fell back to rows: say so in the email, not only in the log
        link += '<p style="margin:0 0 6px;">%s</p>' % e(note)
    return (
        '<!DOCTYPE html><html><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta name="color-scheme" content="light only"><title>%s</title></head>'
        '<body style="margin:0;padding:0;background:#f3efe9;">'
        '<table role="presentation" width="100%%" cellpadding="0" cellspacing="0" border="0" '
        'style="background:#f3efe9;"><tr><td align="center" style="padding:24px 10px;">'
        '<table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0" '
        'style="width:100%%;max-width:600px;background:#ffffff;border-radius:14px;">'
        '<tr><td style="padding:28px 24px 2px;font-family:%s;font-size:28px;line-height:34px;'
        'color:#1f1f1f;">%s</td></tr>'
        '<tr><td style="padding:0 24px 4px;font-family:%s;font-size:14px;color:#6b6b6b;">%s</td></tr>'
        '%s'
        '<tr><td style="padding:26px 24px 26px;font-family:%s;font-size:12px;color:#8c8c8c;">'
        '%s<p style="margin:0;">%s</p></td></tr>'
        '</table></td></tr></table></body></html>'
        % (e(heading), SERIF, e(heading), FONT, e(summary),
           "".join(parts), FONT, link, e(footer or cfg.texts["footer"])))


def build_text(cfg, today, sections, news=()):
    lines = [fmt(cfg.texts["subject"], date=cfg.date_text(today)), ""]
    if news:
        lines.append(cfg.texts["news_heading"])
        for nw in news:
            lines.append("  %s%s" % (nw.text, (" (%s)" % fmt(cfg.texts["news_more"], n=nw.more)) if nw.more else ""))
        lines.append("")
    for year, items in sections:
        lines.append(year_heading(cfg, today, year))
        places = year_places(cfg, items)
        if places:
            lines.append(places)
        for item in items:
            lines.append("  %s  %s" % (caption(item, cfg), item.filename))
        lines.append("")
    if cfg.photos_link:
        lines.append(cfg.photos_link)
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Delivery

class AsUser(object):
    """Temporarily act with the user's rights (effective uid/gid/groups)."""

    def __init__(self, pw):
        self.pw = pw
        self.active = False

    def __enter__(self):
        if os.geteuid() != 0:
            return self
        self.saved_groups = os.getgroups()
        try:
            groups = os.getgrouplist(self.pw.pw_name, self.pw.pw_gid)
        except (KeyError, OSError):
            groups = [self.pw.pw_gid]
        os.setgroups(groups)
        os.setegid(self.pw.pw_gid)
        os.seteuid(self.pw.pw_uid)
        self.active = True
        return self

    def __exit__(self, *exc):
        if self.active:
            os.seteuid(0)
            os.setegid(0)
            os.setgroups(self.saved_groups)
        return False


def save_page(pw, home, page, today, keep_days):
    with AsUser(pw):
        out_dir = os.path.join(home, "OnThisDay")
        os.makedirs(out_dir, exist_ok=True)
        path = os.path.join(out_dir, today.isoformat() + ".html")
        with open(path, "w", encoding="utf-8") as f:
            f.write(page)
        if keep_days > 0:
            cutoff = today - dt.timedelta(days=keep_days)
            for name in os.listdir(out_dir):
                if PAGE_FILE_RE.match(name):
                    try:
                        if dt.date.fromisoformat(name[:10]) < cutoff:
                            os.remove(os.path.join(out_dir, name))
                    except (ValueError, OSError):
                        pass
    return path


def read_secret(secret_dir):
    path = os.path.join(secret_dir, SECRET_NAME)
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        raise ConfigError("no SMTP password stored yet; run with --import-smtp-password FILE")
    if not stat.S_ISREG(st.st_mode) or st.st_uid != 0 or st.st_mode & 0o077:
        raise ConfigError("%s must be a root-owned file with mode 600; import it again" % path)
    with open(path, encoding="utf-8") as f:
        return f.read().strip()


def import_secret(src, secret_dir):
    if os.geteuid() != 0:
        raise ConfigError("--import-smtp-password must run as root (Task Scheduler, user root)")
    with open(src, encoding="utf-8") as f:
        secret = f.read().strip()
    if not secret:
        raise ConfigError("%s is empty" % src)
    os.makedirs(secret_dir, mode=0o700, exist_ok=True)
    st = os.lstat(secret_dir)
    if not stat.S_ISDIR(st.st_mode):
        raise ConfigError("%s is not a directory" % secret_dir)
    os.chown(secret_dir, 0, 0)
    os.chmod(secret_dir, 0o700)
    dest = os.path.join(secret_dir, SECRET_NAME)
    tmp = dest + ".tmp"
    if os.path.lexists(tmp):
        os.remove(tmp)
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(secret + "\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, dest)
    size = os.path.getsize(src)
    with open(src, "r+b") as f:
        f.write(b"\0" * size)
        f.flush()
        os.fsync(f.fileno())
    os.remove(src)
    return dest


def find_ssmtp(cfg):
    path = cfg.email["ssmtp"].strip()
    if path.lower() == "auto":
        path = shutil.which("ssmtp", path="/usr/bin:/usr/sbin:/bin:/sbin:/usr/syno/bin:/usr/local/bin")
    if not path or not os.path.isfile(path):
        raise ConfigError("DSM's mail tool (ssmtp) was not found")
    return path


def dsm_sender(cfg):
    """Sender from Control Panel > Notification > Email (name and address only)."""
    conf = cfg.email["dsm_mail_conf"].strip()
    if not os.path.isfile(conf):
        raise ConfigError("DSM mail is not set up (Control Panel > Notification > Email)")
    values = {}
    with open(conf, encoding="utf-8", errors="replace") as f:
        for line in f:
            key, sep, value = line.partition("=")
            key = key.strip()
            if sep and key in ("smtp_from_mail", "smtp_from_name"):
                values[key] = value.strip().strip('"')
    mail = values.get("smtp_from_mail", "")
    return formataddr((values.get("smtp_from_name", ""), mail)) if mail else ""


def build_message(sender, to_addr, subject, text, page_cid, images):
    """images: [(cid, file name, bytes, subtype)] shown inside the HTML part."""
    domain = parseaddr(sender)[1].rpartition("@")[2] or "onthisday.local"
    msg = EmailMessage()
    msg["Subject"] = subject
    if sender:
        msg["From"] = sender
    msg["To"] = to_addr
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid("onthisday", domain=domain)
    msg.set_content(text)
    msg.add_alternative(page_cid, subtype="html")
    html_part = msg.get_payload()[-1]
    for cid, name, data, subtype in images:
        html_part.add_related(data, "image", subtype, cid="<%s>" % cid,
                              disposition="inline", filename=name)
    return msg


def send_email(cfg, to_addr, subject, text, page_cid, images):
    transport = cfg.email["transport"].strip().lower()
    if transport == "dsm":
        sender = cfg.email["from"].strip() or dsm_sender(cfg)
        msg = build_message(sender, to_addr, subject, text, page_cid, images)
        data = msg.as_bytes()
        proc = subprocess.run([find_ssmtp(cfg), to_addr], input=data, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, timeout=300)
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout).decode("utf-8", "replace").strip()
            raise DeliveryError("DSM mail refused the message (%s)" % (detail[:300] or
                                                                     "exit %d" % proc.returncode))
        return len(data)
    if transport != "smtp":
        raise ConfigError("[email] transport must be dsm or smtp")

    em = cfg.email
    host = em["smtp_host"].strip()
    sender = em["from"].strip()
    if not host or not sender:
        raise ConfigError("[email] transport = smtp needs smtp_host and from")
    msg = build_message(sender, to_addr, subject, text, page_cid, images)
    user = em["smtp_user"].strip()
    password = read_secret(em["secret_dir"].strip()) if user else None
    security = em["smtp_security"].strip().lower()
    ctx = ssl.create_default_context()
    if not cfg.smtp_verify_tls:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    if security == "ssl":
        server = smtplib.SMTP_SSL(host, cfg.smtp_port, context=ctx, timeout=60)
    elif security in ("starttls", "none"):
        server = smtplib.SMTP(host, cfg.smtp_port, timeout=60)
    else:
        raise ConfigError("smtp_security must be starttls, ssl or none")
    try:
        server.ehlo()
        if security == "starttls":
            server.starttls(context=ctx)
            server.ehlo()
        if user:
            server.login(user, password)
        server.send_message(msg)
    finally:
        try:
            server.quit()
        except Exception:
            pass
    return len(msg.as_bytes())


# --------------------------------------------------------------------------

def photo_roots(cfg, home):
    roots = {}
    personal = os.path.join(home, "Photos")
    if os.path.islink(personal) or not os.path.isdir(personal):
        raise ConfigError("%s is missing or is a symlink" % personal)
    roots["personal"] = os.path.realpath(personal)
    if cfg.shared_space:
        path = cfg.shared_space_path
        if path.lower() == "auto":
            found = [p for p in sorted(glob.glob("/volume*/photo")) if os.path.isdir(p)]
            path = found[0] if found else ""
        if path and os.path.isdir(path) and not os.path.islink(path):
            roots["shared"] = os.path.realpath(path)
        else:
            log("warning: Shared Space folder not found, only the Personal Space is used")
    return roots


def jpg_name(filename):
    return (os.path.splitext(filename)[0] or "photo") + ".jpg"


def dry_run_report(cfg, today, sections, news, stats, roots, pw):
    if news:
        print("  " + cfg.texts["news_heading"])
        for nw in news:
            print("    %s%s%s" % ("! " if nw.warn else "", nw.text,
                                  (" (%s)" % fmt(cfg.texts["news_more"], n=nw.more)) if nw.more else ""))
            for item in nw.items:
                print("      %s %s/%s" % (utc_hhmm(item.time), item.folder.rstrip("/"), item.filename))
    plans = None
    if cfg.layout == "mosaic" and sections:
        data, sections = load_big(sections, roots)
        sizes = {k: jpeg_size(v) for k, v in data.items()}

        def ratio(item):
            w, h = sizes.get(id(item)) or (3, 2)
            return min(10.0, max(0.1, w / float(h))) if h else 1.5
        plans = plan_sections(sections, ratio, cfg)
        print("  ffmpeg found: %s" % (", ".join(ffmpeg_candidates(cfg)) or "none"))
        if sections:
            place, first = plans[0][0][2][0]
            cropper, reason = working_ffmpeg(cfg, pw, data[id(first)], place)
            if cropper:
                print("  Layout: mosaic, cut with %s" % cropper.binary)
            else:
                print("  Layout: rows, because %s" % reason)
    for n, (year, items) in enumerate(sections):
        places = year_places(cfg, items)
        frames = (" [%s]" % ", ".join(name for name, _, _ in plans[n])) if plans else ""
        print("  " + year_heading(cfg, today, year) + (" (%s)" % places if places else "") + frames)
        for item in items:
            marks = ("*%d" % item.rating if item.rating else "") + (" backup" if item.backup else "")
            url = photo_url(cfg, item)
            if "/#/sharing/" in url:
                opens = "in album %s (share link)" % item.album
            elif "/#/album/" in url:
                opens = "in own album %s (not shared)" % item.album
            else:
                opens = "in folder" if url else "no link"
            print("    %-8s %-9s %s  %s/%s  -> %s" % (item.space, marks.strip(), utc_hhmm(item.time),
                                                  item.folder.rstrip("/"), item.filename, opens))
    skipped = stats.get("mismatched") or []
    if skipped:
        print("  Skipped, dated after their album's year:")
        for item in sorted(skipped, key=lambda i: i.time):
            print("    %s  %s/%s" % (utc_date(item.time).isoformat(), item.folder.rstrip("/"),
                                    item.filename))


def run(args):
    if not USER_RE.match(args.user):
        raise ConfigError("invalid user name %r" % args.user)
    try:
        pw = pwd.getpwnam(args.user)
    except KeyError:
        raise ConfigError("DSM user %r not found" % args.user)
    cfg = Config(args.config)
    today = dt.date.fromisoformat(args.date) if args.date else dt.date.today()
    home = pw.pw_dir if os.path.isdir(pw.pw_dir) else "/var/services/homes/" + args.user
    roots = photo_roots(cfg, home)
    section = cfg.user_section(args.user)
    if section is not None and section.get("photos_link", "").strip():
        cfg.photos_link = section.get("photos_link").strip()  # this person's own address
    to_addr = ""
    if "email" in cfg.delivery and not args.dry_run:
        to_addr = section.get("email", "").strip() if section is not None else ""
        if not to_addr:
            raise ConfigError("no email address for %s: add [user:%s] with email = ..."
                              % (args.user, args.user))
        # fail early, before scanning
        if cfg.email["transport"].strip().lower() == "dsm":
            find_ssmtp(cfg)
            dsm_sender(cfg)
        elif cfg.email["smtp_user"].strip():
            read_secret(cfg.email["secret_dir"].strip())

    api = PhotosApi(cfg.synowebapi, args.user)
    stats = {"found": 0, "excluded": 0, "date_mismatch": 0, "no_thumb": 0, "skipped_video": 0,
             "starred": 0}
    locator = Locator(api, cfg, roots)
    me, albums, complete = None, {}, False
    if cfg.album_first or cfg.shared_albums or cfg.news:
        try:
            me = api.my_id()
        except ApiError as e:
            log("note: Synology Photos user id not readable (%s)" % e)
        albums, complete = read_albums(api, me, stats)
    locator.api_me = me

    by_year = collect(api, cfg, roots, today, stats, albums, locator)
    linked = tag_albums(by_year, albums) if cfg.album_first else 0
    sections = allocate(by_year, cfg.photos_per_year, cfg.max_photos, cfg,
                        today.year if cfg.rotate_yearly else None)
    chosen = [i for _, items in sections for i in items]

    news, old, keep_state = [], None, False
    if cfg.news:
        if not complete:
            log("note: what's new in the albums could not be checked (album list incomplete)")
        else:
            old = load_news_state(cfg, args.user)
            if old is None:
                log("news: first run, what the albums hold now is noted for the next email")
            else:
                news = build_news(cfg, roots, me, old, albums, locator)
            # a test run (--date or --dry-run) shows the news but doesn't note them as seen
            keep_state = not args.dry_run and not args.date

    ratings = {True: "read", False: "not available", None: "not asked"}[api.rating_ok]
    log("%s, %s: %d found, %d excluded, %d dated after their album's year, "
        "%d without thumbnail, %d starred (ratings %s), %d chosen from %d year(s), %d in albums; "
        "%d news; %d API calls%s"
        % (args.user, today.isoformat(), stats["found"], stats["excluded"], stats["date_mismatch"],
           stats["no_thumb"], stats["starred"], ratings, len(chosen), len(sections),
           sum(1 for i in chosen if i.album), len(news), api.calls,
           (" (searched %s)" % ", ".join(stats["from"]) if stats.get("from") else "")
           + (" [%d from shared albums]" % stats["album"] if stats.get("album") else "")
           + (" [%d photos matched to albums]" % linked if linked else "")))
    where = album_positions(chosen, albums)
    if where:
        log("album photos (number in the album by date): %s" % where)

    if args.dry_run:
        dry_run_report(cfg, today, sections, news, stats, roots, pw)
        return 0
    if not chosen and not news:
        log("nothing on this day, nothing sent")
        if keep_state:
            save_news_state(cfg, args.user, albums, today, old)
        return 0

    # the photos: cut to fit (mosaic) or the medium thumbnails (rows)
    plans, images, fallback = None, {}, ""
    if cfg.layout == "mosaic" and sections:
        mosaic, how = make_mosaic(cfg, sections, roots, pw)
        if mosaic is not None:
            sections, plans, images = mosaic
            chosen = [i for _, items in sections for i in items]
            if how:
                log("layout: mosaic, photos cut with %s" % how)
        else:
            log("note: photos shown in rows, because %s" % how)
            fallback = ("no ffmpeg found on the NAS" if how.startswith("no ffmpeg")
                        else "ffmpeg is switched off" if "switched off" in how
                        else "ffmpeg failed, see the task's log")
    note = fmt(cfg.texts["fallback_note"], reason=fallback) if fallback else ""
    news_items_all = [i for nw in news for i in nw.items]
    for item in (news_items_all if plans is not None else chosen + news_items_all):
        try:
            images[id(item)] = read_thumb(item.thumb, item.root or roots[item.space])
        except (OSError, ValueError, KeyError) as e:
            log("skipping %s: %s" % (item.thumb, e))
    if plans is None:
        sections = [(y, [i for i in items if id(i) in images]) for y, items in sections]
        sections = [(y, items) for y, items in sections if items]
    for nw in news:
        nw.items = [i for i in nw.items if id(i) in images]
    if not sections and not news:
        log("no readable thumbnails, nothing sent")
        return 0
    keep = [i for _, items in sections for i in items] + [i for nw in news for i in nw.items]
    sizes = {id(i): jpeg_size(images[id(i)]) for i in keep}

    def size_for(item):
        return sizes.get(id(item))

    layout = "mosaic" if plans is not None else "rows"
    if "file" in cfg.delivery:
        page = build_html(cfg, today, sections, lambda i: "data:image/jpeg;base64,"
                          + base64.b64encode(images[id(i)]).decode("ascii"), size_for, news, plans,
                          lambda w: "data:image/png;base64," + base64.b64encode(spacer_png(w, int(GAP_PX))).decode("ascii"),
                          note)
        # a test run with --date never cleans up old pages
        path = save_page(pw, home, page, today, 0 if args.date else cfg.keep_days)
        log("saved %s (%s)" % (path, layout))

    if "email" in cfg.delivery:
        cids = {id(i): make_msgid("otd")[1:-1] for i in keep}
        gaps = {}

        def gap_src(w):
            if w not in gaps:
                gaps[w] = (make_msgid("otd")[1:-1], spacer_png(w, int(GAP_PX)))
            return "cid:" + gaps[w][0]

        page = build_html(cfg, today, sections, lambda i: "cid:" + cids[id(i)], size_for, news, plans, gap_src,
                          note)
        text = build_text(cfg, today, sections, news)
        subject = fmt(cfg.texts["subject"], date=cfg.date_text(today))
        imgs = [(cids[id(i)], jpg_name(i.filename), images[id(i)], "jpeg") for i in keep]
        imgs += [(cid, "gap.png", data, "png") for cid, data in gaps.values()]
        size = send_email(cfg, to_addr, subject, text, page, imgs)
        log("emailed %d photos to %s (%d KB, %s)" % (len(keep), to_addr, size // 1024, layout))
    if keep_state:
        save_news_state(cfg, args.user, albums, today, old)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="On this day memories for Synology Photos")
    ap.add_argument("--user", help="DSM user name, e.g. alice")
    ap.add_argument("--config", default=DEFAULT_CONFIG, help="config file (default: next to the script)")
    ap.add_argument("--date", help="pretend today is YYYY-MM-DD (for testing)")
    ap.add_argument("--dry-run", action="store_true", help="only list what would be sent")
    ap.add_argument("--import-smtp-password", metavar="FILE",
                    help="move the SMTP password from FILE into the root-only secret folder")
    ap.add_argument("--version", action="version", version="%(prog)s " + VERSION)
    args = ap.parse_args(argv)
    try:
        if args.import_smtp_password:
            cfg = Config(args.config)
            dest = import_secret(args.import_smtp_password, cfg.email["secret_dir"].strip())
            log("SMTP password stored in %s (root only); %s was wiped and deleted"
                % (dest, args.import_smtp_password))
            return 0
        if not args.user:
            ap.error("--user is required")
        log("onthisday %s --user %s%s%s" % (VERSION, args.user, " --date " + args.date if args.date else "",
                                            " --dry-run" if args.dry_run else ""))
        return run(args)
    except (ApiError, ConfigError, DeliveryError, OSError, smtplib.SMTPException, ssl.SSLError,
            subprocess.TimeoutExpired) as e:
        log("ERROR: %s" % e)
        return 1


if __name__ == "__main__":
    sys.exit(main())
