# On This Day for Synology Photos

A daily "On this day" email for Synology Photos, like OneDrive and Google Photos send it. Every morning each person gets the photos taken on today's date in earlier years, grouped by year, laid out like OneDrive's memories. Tap a photo and it opens in Synology Photos, inside its album.

What changed in the albums, e.g. "bob added 3 photos to 2026-09 Yellowstone", comes either above the memories (**New in your albums**) or as its own evening email (**What's New**, `whatsnew.py`).

Synology Photos has no such feature. Two Python scripts. They run on the NAS from DSM's Task Scheduler. No Docker, no SSH, no extra Python packages.

Author: Egbert H. Schroeer · License: MIT · onthisday.py 1.5.0, whatsnew.py 1.0.0 (see `CHANGELOG.md`)

## What it does

- Looks at each person's **Personal Space** (`home/Photos`, incl. `MobileBackup`), the **Shared Space** (shared folder `photo`) and **albums shared with them**. Nothing else.
- Each person sees only what they can see in Synology Photos: the script asks Synology Photos *as that user*.
- **Albums first:** a photo that is also in an album shows the album's name and opens inside the album.
- Leaves out WhatsApp, Facebook and Messenger images (adjustable).
- Skips scans that carry their scan date: a photo dated 2024 in the album "2000-07-30 Wedding" is left out.
- Up to 5 photos per year, 12 in total.
- Uses Synology's own thumbnails. A run back to 1950 takes seconds.
- One email per person, or an HTML page in their home folder. Nothing is written into Synology Photos.

## How photos are picked

When a year has more photos on that day than `photos_per_year`:

1. Shots within a minute of each other are one moment; its best shot stands for it.
2. **Starred** photos first, most stars first.
3. Then photos in **albums and own folders**.
4. Then the raw **phone backup** (`backup_folders`, default `MobileBackup`).
5. Each group is spread over the day.
6. **Yearly rotation** (`rotate_yearly`): next year's email shows other photos of the same day. Over the years every photo gets its turn. Starred photos that fit are shown every year.

Synology Photos has no quality score. Stars and your folders and albums are the signals. `--date` with next year's date and `--dry-run` previews next year's picks.

## Layout

**`layout = mosaic`** (default): each year fills a fixed frame. One photo large, the others next to or below it. Portraits in tall places, landscapes in wide ones, all edges aligned, on a phone too. The frame is picked per year so that the least is cut away; years alternate the side of the large photo. Videos get a play sign.

The photos are cut to fit with **ffmpeg** from SynoCommunity:

1. Package Center → Settings → Package Sources → Add: name `SynoCommunity`, location `https://packages.synocommunity.com`.
2. Community tab → **FFmpeg 8** → Install. DSM warns that the package is from a third party; that's expected. Package Center adds SynoCli Video Drivers and SynoCli Video Driver Tools (Intel GPU libraries and test tools; not needed here, not kernel modules).

FFmpeg is a command-line tool: no service, no open ports. It runs only when the script calls it. `ffmpeg = auto` tries the SynoCommunity packages first (newest version first) and DSM's own ffmpeg last, test-cuts one photo with each and uses the first that works. DSM's own ffmpeg changed with DSM 7.2.2; don't count on it.

A cut keeps the middle and a bit more of the top, where faces and skylines usually are.

**`layout = rows`**: photos uncropped, in rows of equal height. Also the fallback when no ffmpeg works: the email then says "Photos shown uncropped: …" at the bottom, and the log says why.

## New in your albums

Each run notes what every album the person sees holds. The next email starts with what changed:

- **Photos someone else added**, with up to `news_photos` thumbnails: "bob added 3 photos to 2026-09 Yellowstone". What you added yourself isn't listed.
- **Albums newly shared with you:** "bob shared a new album with you: 2026-05 High Tea (12 photos)".
- **Warnings** about your own albums: gone, or photos that left them. An album no longer shared with you is noted too.

An album made again under the same name counts as the same album. On a day without memories the email still goes out if there is news. The first run only takes notes. Dry runs and `--date` runs show the news without marking it as seen. Notes are kept in `state_dir` (root only, default `/volume1/@onthisday`). `news = no` switches it off.

## What's New (whatsnew.py)

The same news as its own email, once a day at a fixed time, only on days with news. On This Day stays small.

- Needs `onthisday.py` 1.5 or later in the same folder and uses its `onthisday.conf` (mail, people, wording).
- Set `news = no` there, or both emails list the news.
- **Album tool** (optional): with `albumtool_folder = …` in a person's `[user:…]` section, their email also lists what an album tool did for them: "Album Tool added 3 photos to 2026-09 Yellowstone", "Album Tool made the album 2016–2025 Alaska (40 photos)". It reads the tool's undo journals of the last 8 days, relative to the home folder: `journal-YYYYMMDD-HHMMSS.jsonl`, one JSON line per change, `{"op": "add", "id": <album id>, "items": [<item ids>]}` or `{"op": "create", "id": <album id>}`. Undone runs (`.undone`) don't count. Written for albumtool.py, which isn't part of this package.
- What you added yourself by hand isn't listed.
- Notes: `state_dir/whatsnew-<user>.json`. The first run starts from On This Day's last notes, so nothing is lost when you switch; without them it only takes notes.
- `--dry-run` lists the news; nothing sent, nothing noted.

## Tap a photo

Set `photos_link` to the Synology Photos address your phones reach, e.g. over Tailscale: `photos_link = http://100.64.0.10:5000/photo`. Photos open in the phone's browser; sign in once. A person with another address gets `photos_link = …` in their `[user:…]` section.

- **Shared album** (`album_links = yes`): `…/photo/#/sharing/<share code>/item/<id>`
- **Own album, not shared:** `…/photo/#/album/<album id>/item/<id>`
- **Folder:** `…/photo/#/personal_space/folder/<folder id>/item/<id>` (Shared Space: `shared_space`)

Swiping to the next photo works only for photos early in an album. In tests, photo 149 of an album could be swiped, photo 290 and later not: Synology Photos then opens the photo alone. That's Synology Photos, not the link. The log line `album photos (number in the album by date)` shows where each photo sits.

## Security

- **No DSM password.** DSM's `synowebapi` (root only) asks Synology Photos on behalf of the user.
- **Files:** thumbnails only from inside the photo folders, real JPEGs, no symlinks. Pages are written with the user's rights.
- **ffmpeg** gets a thumbnail's bytes through a pipe, no file names, and runs as the user.
- **Mail password,** if needed: stored once in a root-only folder, never in the settings file.
- **Code:** Python standard library only, built into DSM 7. Read the script before you run it as root.

## Requirements

- Synology NAS with DSM 7 and Synology Photos. Tested on a DS218+ in September 2026.
- For the mosaic: a SynoCommunity FFmpeg package (FFmpeg 8 tested).
- For email: a way for the NAS to send mail (see *Email*).

## Install

1. **Copy the files** into a folder only administrators can change, e.g. `Scripts/onthisday` in your home folder. The scripts run as root, so no folder other users can write to. Copy `onthisday.py` (and `whatsnew.py`), and `onthisday.conf.example` as `onthisday.conf`.
2. **Edit `onthisday.conf`:** one `[user:NAME]` section per DSM user, with their email address. Keep `delivery = file` for the first test.
3. **Create a task:** Control Panel → Task Scheduler → Create → Scheduled Task → User-defined script.
   - General: task `On This Day alice` (plain letters; DSM refuses some characters, e.g. long dashes), user **root**.
   - Schedule: daily, e.g. 06:50.
   - Task Settings: "Send run details by email", "only when the script terminates abnormally". Run command:
     ```
     /usr/bin/python3 /volume1/homes/<admin>/Scripts/onthisday/onthisday.py --user alice
     ```
4. **Test:** select the task, **Run**. With `delivery = file`, today's page is in `OnThisDay` in alice's home folder.
5. **More people:** one task each, with their `--user`.
6. **What's New** (optional): copy `whatsnew.py` next to `onthisday.py`, set `news = no`, and create one task per person the same way, e.g. `What's New alice`, daily 18:00:
   ```
   /usr/bin/python3 /volume1/homes/<admin>/Scripts/onthisday/whatsnew.py --user alice
   ```

Dry run, nothing sent:
```
/usr/bin/python3 /volume1/homes/<admin>/Scripts/onthisday/onthisday.py --user alice --dry-run > /volume1/homes/<admin>/otd-dry.txt 2>&1
```
It lists the news, each year's frame, and per photo whether it opens in its album or folder (`*3` = 3 stars, `backup` = phone backup). The `Layout:` line says whether ffmpeg works. `--date YYYY-MM-DD` pretends another day.

## Email

`delivery = email` (or `file, email`), then the transport:

**`transport = dsm`:** DSM's own alert mail (Control Panel → Notification → Email). Works only if that sender is a custom SMTP server with a password. With a Google or Microsoft sign-in DSM's mail tool fails with `535 Authentication unsuccessful`, and DSM may then report an "OAuth refresh token error" for its own alerts; sign in again under **Set Up** and use `transport = smtp`.

**`transport = smtp`:** a mail server of your choice.
- *Own server* (e.g. Synology MailPlus): `smtp_host`, `smtp_port`, `smtp_security`; `smtp_user` empty if it needs no login.
- *Gmail:*
  1. Google account with 2-Step Verification: create an **app password** at myaccount.google.com/apppasswords.
  2. In `onthisday.conf`:
     ```
     transport = smtp
     smtp_host = smtp.gmail.com
     smtp_port = 587
     smtp_security = starttls
     smtp_user = you@gmail.com
     from = Synology Photos <you@gmail.com>
     ```
  3. Store the app password once. One-off task, user **root**, not enabled, your 16 letters in place of the placeholder:
     ```
     printf '%s' 'PASTE-16-LETTERS-HERE' > /tmp/otd-pw && /usr/bin/python3 /volume1/homes/<admin>/Scripts/onthisday/onthisday.py --import-smtp-password /tmp/otd-pw > /volume1/homes/<admin>/otd-import.txt 2>&1
     ```
     Run it, then **delete the task**: the password is in its command. It ends up in `/volume1/@onthisday`, root only; the temporary copy is wiped.
- *Outlook.com / Hotmail* can't send from scripts (no password logins). Fine as recipients.

The first email often lands in Junk. Mark it "not junk".

## Settings (onthisday.conf)

| Setting | Meaning |
|---|---|
| `delivery` | `file`, `email` or `file, email` |
| `first_year` | `auto` (oldest photo per space, else 1950) or a year |
| `photos_per_year`, `max_photos` | photos per year and in total (5, 12) |
| `include_videos` | include videos (play sign) |
| `layout` | `mosaic` (needs ffmpeg) or `rows` |
| `ffmpeg` | `auto`, a path, or `no` |
| `prefer_starred` | prefer starred photos |
| `backup_folders` | raw phone-backup folders, picked last |
| `rotate_yearly` | other photos of the same day each year |
| `shared_albums` | include albums shared with the user |
| `album_first` | a photo also in an album shows the album's name and opens in it |
| `album_links` | open photos from shared albums inside the album; puts the share code into the email (off by default) |
| `skip_date_mismatch` | skip photos dated after the year their album name starts with |
| `shared_space`, `shared_space_path` | include the Shared Space; `auto` finds `/volumeX/photo` |
| `keep_days` | how long saved pages are kept |
| `photos_link` | Synology Photos address for the links |
| `news`, `news_photos`, `state_dir` | New in your albums: on/off, thumbnails per line, where the notes are kept |
| `[exclude]` | file-name patterns and folder words to leave out |
| `[email]` | transport and mail server |
| `[user:NAME]` | `email` per person, optional `photos_link`; for What's New optional `albumtool_folder` |
| `[texts]` | all wording, e.g. for another language |

## Troubleshooting

- **The log:** set a save folder in Task Scheduler → Settings, or add `> /path/log.txt 2>&1` to the command. One summary line per run: found, chosen, in albums, star ratings, news. Then `layout: mosaic, photos cut with …` or `note: photos shown in rows, because …`.
- **Rows instead of the mosaic:** the email's last lines say "no ffmpeg found on the NAS" (install FFmpeg 8) or "ffmpeg failed, see the task's log" (the log lists each ffmpeg with its error).
- **No swiping from a tapped photo:** see *Tap a photo*.
- **No email:** check Junk. If Gmail's Sent folder has it, the script worked.
- **`535 Authentication unsuccessful`** with `transport = dsm`: see *Email*.
- **"oldest photo unknown":** harmless; it searches back to 1950, or set `first_year`.
- **"star ratings not available":** this Synology Photos version doesn't return ratings; picking goes by album, folder and time.
- **"news: first run":** normal the first time.
- **What's New "nothing new, nothing sent":** normal on quiet days.
- **The first log line** shows the version: `onthisday 1.5.0 --user alice`, `whatsnew 1.0.0 --user alice`.

## Limitations

- **Cutting:** the mosaic cuts photos to fit. It picks the frame that cuts least; a portrait in a wide place loses top and bottom. No face detection. `layout = rows` shows every photo whole.
- **Share codes:** links into shared albums contain the album's share code. Shared with named people only, the code opens nothing without their login. With a public link switched on, anyone who reaches your NAS with the code can view the album.
- A photo in several albums opens in the one with the shortest time span (the trip, not a "best of" collection).
- Owners of shared albums must be listed as `[user:…]`, so their thumbnails and names are found.
- **News:** "who added" is the photo's owner; a Shared Space photo counts as added by the album's owner. Every album is read once per run.
- **Album tool:** after more than 8 days without a What's New run, what the tool added before then counts as added by hand and isn't listed.
- Synology Photos' internal API (`synowebapi`) is undocumented. A DSM or Synology Photos update can change it.
- Photos appear on the right day only if their date is the day taken. Scans usually carry the scan date.

## License

MIT, see `LICENSE`.
