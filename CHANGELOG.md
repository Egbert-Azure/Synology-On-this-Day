# Changelog

## whatsnew.py 1.0.0 (2026-09-30)
- New: "What's new" as its own daily email, only on days with news. Photos others added, albums newly shared, warnings about own albums.
- Optional: what albumtool.py added or made for you, from its undo journals (`albumtool_folder`). What you added by hand stays out.
- The first run starts from On This Day's last notes.

## 1.5.0 (2026-09-30)
- `news = no` leaves the album news to whatsnew.py.
- Version line at the top of the log.

## 1.4.3 (2026-09-30)
- Author, version and license in the script header. Shorter comments.

## 1.4.2 (2026-09-30)
- ffmpeg: tries the SynoCommunity packages first, DSM's own last; uses the first that really cuts a photo.
- If the mosaic falls back to rows, the email says so; the log lists each ffmpeg and its error.
- Shared albums open through their share link; own albums that aren't shared through their album address.
- Log line with each album photo's number in its album.

## 1.4.0 (2026-09-30)
- Mosaic layout: fixed frames like OneDrive's memories, photos cut to fit with ffmpeg. Rows stay as `layout = rows`.
- Albums first: a photo that is also in an album shows the album's name and opens in it.
- Folder links fixed (`item/<id>`), so swiping works there.
- Defaults: 5 photos per year, 12 in total.

## 1.3.0 (2026-09-29)
- New in your albums: photos others added, albums newly shared, warnings about own albums.

## 1.2.0 (2026-09-28)
- Yearly rotation: other photos of the same day each year.

## 1.1.0 (2026-09-28)
- Rows of equal height (justified), no cropping.
- Starred photos first, phone backup last.

## 1.0.10 (2026-09-27)
- Photos from albums shared with the user, tappable.

## 1.0.0 (2026-09-26)
- First version: daily email with the photos of today's date in earlier years.
