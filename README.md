# Raspberry Pi Photo Slider

A local Raspberry Pi slideshow player with a browser review interface, saved folder
albums, year-based photo organization, zoom, and touchscreen panning.

## Installation

The deployed setup uses Raspberry Pi OS with an X11 desktop. Install the runtime
packages, clone this repository, and install the user services from the Pi's
desktop session:

```sh
sudo apt-get update
sudo apt-get install git python3 python3-pil python3-tk ffmpeg zenity
git clone https://github.com/ramunl/rpi-photo-slider.git ~/photo-library
cd ~/photo-library
python3 install.py
```

Before reviewing or playing the year library, build its catalog using the audit
and rebuild commands below. The existing deployment uses `/mnt/backup2tb` for
source media and `/mnt/backup2tb/Library` for the curated library; adjust the
command-line paths for a different disk. The optional manifest defaults to
`/home/roman/media-manifest.json`; audit also works when it is absent. Run
`python3 audit.py --help` and `python3 catalog.py --help` for path options.

The repository contains application code and tests. Local catalogs, saved folder
paths, decisions, thumbnails, logs, backups, and original photos stay on the Pi
and are excluded from version control.

The application lives at `/home/roman/photo-library` on the Raspberry Pi. It uses the installed Python, Pillow, Tk, and FFmpeg packages. No cloud photo service is involved.

## Play a year

Double-click **Photos by year** on the Pi desktop, select a year, then start. The connected HDMI screen shows the slideshow.

```sh
~/bin/photos start 2010
~/bin/photos start 2010 --seconds 10 --shuffle
~/bin/photos pause
~/bin/photos resume
~/bin/photos next
~/bin/photos previous
~/bin/photos stop
~/bin/photos years
~/bin/photos status
```

Space pauses/resumes. Left/right selects the previous/next photo. Escape closes the slideshow. Images retain their proportions and respect EXIF orientation. Photos loop; videos have their own year folders and are not mixed into the photo slideshow.

Use **Zoom In**, **Zoom Out**, and **Reset Zoom** in the review page, or press
`+`, `-`, and `0` in the player. Zoom ranges from 50% to 400% and stays selected
between photos and albums until reset or the player service restarts. When
zoomed in, drag directly on the displayed photo with one finger on a touchscreen
or the left mouse button to pan. Panning stops at the image edges. Each new photo
starts centered, keeping the zoom level. Reset Zoom also centers the image.

The SSH alias has a preset RemoteCommand, so remote commands need this override:

```sh
ssh -T -o RemoteCommand=none rpi5-hdd '/home/roman/bin/photos start 2010'
ssh -T -o RemoteCommand=none rpi5-hdd '/home/roman/bin/photos stop'
```

## Review the library

Double-click **Review photo library** on the Pi desktop, or visit <http://127.0.0.1:8787> in its browser.

From another computer, establish a tunnel, keep it open, and visit the same address locally:

```sh
ssh -T -N -o RemoteCommand=none -L 8787:127.0.0.1:8787 rpi5-hdd
```

Select pictures and choose **Keep as photo**, **Exclude**, or **Set year**. Then click **Apply saved decisions**. **Reset decision** restores automatic classification. Decisions are reversible. A year override expresses a known year; January 1 is a placeholder, not an inferred exact capture day.

To play another local folder, click **Add Folder** between **Play on TV** and
**Pause**, then select a directory in the Pi's folder chooser. Readable images in
that folder and its subfolders become a saved folder album in the playback
dropdown. The new album is selected automatically; click **Play on TV** and use
the usual Pause, Resume, Next, and Stop buttons. Originals are never copied or
changed. Folder albums stay separate from curated year decisions. Add the same
folder again to refresh its photos without creating another entry. Albums are
stored in `data/folders.json` and appear in both the review dropdown and the
**Photos by year** desktop picker after restarting. The review dropdown remembers
your last selection in the same browser. Keep the source drive connected for playback.
When using an SSH tunnel, the native folder chooser opens on the Pi desktop.

Missing camera metadata is a review hint, not proof that an image is not a photograph. Screenshots, documents, graphics, and ambiguous or suspicious images are held out of the slideshow pending review. A document heuristic can also flag snow scenes or bright photographs. Similar-image suggestions may contain distinct burst frames; they require visual judgment and are never automatically removed. Visually confirmed decisions are stored in `data/decisions.json`.

**Keep as photo** clears content hints, but cannot override broken media or uncertain dates. Set the year separately when known. Decoder warnings remain in review. Unsupported formats and timeouts are listed separately from confirmed decoding failures.

## Year folders and source preservation

- `/mnt/backup2tb/Library/Slideshow/Photos/YYYY/`: curated photos.
- `/mnt/backup2tb/Library/Slideshow/Videos/YYYY/`: verified videos.
- After activation, `Library/By-Date` and `Library/Videos` point to these curated views.
- The previous year views are retained under `/mnt/backup2tb/_stale/Library.before-curation-*/`.
- The source folders, old manifests, and original files are preserved.

The curated files are hard links to source files, and `Slideshow` points to a versioned generation. Do not edit a curated image in place: an in-place edit would also change its source because they share file content. Review decisions only change which links appear in the next generation. Old generations are retained for rollback. This cleanup does not reclaim disk space by deleting originals.

Capture EXIF takes precedence over export metadata. Google sidecars use `photoTakenTime`, never the upload/import `creationTime`. Dated filenames are a fallback. Modification timestamps and ambiguous folder ranges do not establish capture years. Conflicting years are held for review unless capture EXIF and the filename corroborate the correction. Exact duplicates can supply a missing date only when their known years agree.

## Audit and rebuild

```sh
cd /home/roman/photo-library
python3 audit.py --workers 4
python3 jpeg_check.py
python3 enrich.py
python3 catalog.py --activate
```

To verify that every image elsewhere in `Library` is represented in the
year-sorted `By-Date` tree by exact file content, run:

```sh
python3 coverage.py
```

The resumable cache is `data/coverage-cache.jsonl`; the complete result,
including every missing path and its audit metadata, is `data/coverage.json`.
The check does not modify photos or year folders.

Run one audit/rebuild at a time. The audit scans source media, including extracted Takeout files; it skips derived Library trees, retired trees, documents, and filesystem trash folders. Files in Takeout trash or with `.trashed-` names are kept out for review. Unextracted archives and unrelated document folders are outside this library audit.

The audit hashes complete files and decodes each image frame. A hash of orientation-corrected RGB pixels detects identical pictures with different metadata or file bytes. A dedicated `jpeginfo` pass additionally checks the JPEG compression streams, once per byte-identical content group. FFmpeg decodes the complete video and audio streams. A successful decode cannot prove the subject matter, correct a camera clock, or detect every visually damaged recovery image; the contact sheets and review list address those uncertainties without discarding originals.

The Debian `jpeginfo` binary is extracted under `vendor/jpeginfo`; it is installed only for this application, without changing system packages. To reinstall it on this Pi:

```sh
mkdir -p vendor
cd vendor
apt-get download jpeginfo
dpkg-deb -x jpeginfo_1.7.1+dfsg-1+b1_arm64.deb jpeginfo
```

`audit.jsonl` caches results by inode, size, and nanosecond modification time and survives interruption. The final `audit.json` is replaced only after completion. Enrichment rechecks sidecars, trailing JPEG data, and document candidates. Rebuilds validate source fingerprints before publishing a new year generation. Metadata or sidecar changes require the enrichment pass even when image bytes are unchanged.

Reports:

- `data/summary.json`: counts, years, and completion status.
- `data/report.tsv`: every media path, status, date evidence, duplicate reference, and error.
- `data/catalog.json`: complete player/review catalog and similar-image candidates.
- `data/decisions.json`: reversible content and year overrides.
- `audit.log`, `player.log`, `review.log`: runtime logs.

Do not run the legacy `/home/roman/build_library.py` against the activated view; use this audit/rebuild workflow.

## Services and future voice control

```sh
systemctl --user status photo-library-player photo-library-review
systemctl --user restart photo-library-player photo-library-review
```

Both services start with the user session. The player remains hidden until asked to play or show the picker. It expects the current Pi X11 session on `:0`; a change to Wayland would require adapting the display setup. The review service listens only on localhost. Player commands use a Unix socket accessible only to the Pi user.

A future voice-assistant intent such as “show photos from 2010” should validate a four-digit year and invoke `/home/roman/bin/photos start 2010` as `roman` on the Pi (locally or via authenticated SSH). Pause, resume, next, and stop map directly to the commands above. Command results are JSON and nonzero exit codes report failures. No voice-assistant configuration is installed yet.

Implementation references: [Pillow image decoding](https://pillow.readthedocs.io/en/stable/reference/Image.html), [EXIF orientation handling](https://pillow.readthedocs.io/en/stable/reference/ImageOps.html), and [jpeginfo](https://github.com/tjko/jpeginfo).

## Validation

```sh
cd /home/roman/photo-library
python3 -m unittest discover -v
```

Tests cover truncated images, exact and pixel duplicates, date conflicts, manual year overrides, and preservation of originals. Live checks also exercise the Pi player commands and the local review HTTP endpoints.
