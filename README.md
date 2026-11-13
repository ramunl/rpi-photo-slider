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

## Face detection (optional)

Face detection is optional and fully local; nothing is uploaded. It uses
OpenCV's YuNet **2023mar** model from the
[OpenCV Zoo](https://github.com/opencv/opencv_zoo) and stores results in a
persistent, versioned cache at `data/faces.sqlite3`.

To enable it:

```sh
cd /home/roman/photo-library
python3 -m pip install -r requirements-faces.txt
curl -L -o models/faces/face_detection_yunet_2023mar.onnx \
  https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx
python3 face_detector.py --pin models/faces/face_detection_yunet_2023mar.onnx
```

Copy the printed `MODEL_SHA256 = "..."` line into `face_detector.py`. The model
file is not committed; it is verified against that pinned SHA-256 on every load.
On the Raspberry Pi, install the distro runtime instead of the pip wheels:

```sh
sudo apt-get install python3-opencv
```

`python3-opencv` on Raspberry Pi OS ships OpenCV 4.6.0 with `cv2.FaceDetectorYN`.
The pinned `opencv-python-headless` wheel in `requirements-faces.txt` is for
desktop development and test runs; desktop tests alone do not establish Pi
runtime compatibility, so confirm the distro package on the Pi before relying on
it there.

Detections run on a bounded-resolution, EXIF-transposed copy of each photo and
return confidence-filtered boxes normalized to the full orientation-corrected
image. The cache contract:

- A row is keyed by the catalog id where one exists, or by a `path:`-prefixed
  hash of the canonical path for legacy folder-album items. Existing
  `folders.json` and `catalog.json` are only read, never rewritten.
- Each row stores the photo's source-bytes SHA-256 and the detector signature
  (model hash, preprocessing bounds, score/NMS thresholds, top-k). Replacing a
  photo even with preserved timestamps, moving it, upgrading the model, or
  changing a threshold invalidates the entry instead of serving stale boxes.
- An empty result is stored as a real row (`empty = 1`), so a face-less photo is
  not confused with a not-yet-computed miss. Failed reads are never cached and
  stay retryable; a photo that changes during detection is not committed.
- Source bytes are re-hashed before and after detection, so content changes are
  caught without trusting stat metadata. The source is read twice for hashing in
  addition to the decode.
- Playback preparation runs in a single background process. Cache opening,
  SQLite waits, source hashing, detection, EXIF correction, and resizing all run
  there; Tk only commits prepared pixels and their framing together.

Milestone 2 adds internal `Playback.settings(enabled=..., zoom_percent=...)`
controls, defaulting to disabled and 40% (1.4× fit). Automatic framing uses the
union of all normalized face boxes, clamps pan at image edges, and reduces zoom
as needed to retain every face. Manual zoom/reset remains persistent across
photos and albums and takes precedence over automatic framing; effective
automatic zoom does not change the manual zoom setting.

Milestone 3 adds persistent, validated settings and the review-page controls.
The review page has labeled **Detect faces** and **Zoom increase** controls;
disabling face detection disables the zoom input without discarding its value.
Saving writes `data/face_settings.json` atomically and then applies the settings
to the running player over its control socket. The zoom increase must be a finite
number between 0 and 300 percent; invalid values are rejected without changing
the saved settings. The player loads the same file at startup, so durable
settings survive a restart. When the player cannot be reached, the settings are
still saved for the next start and the review page reports that live application
failed instead of pretending it succeeded.

Scheduling favors the requested photo, then the next photo in playback order,
then remaining photos to warm the face cache. Jobs are deduplicated; cached faces
are reused through the existing source/signature validation. Navigation, album,
settings, manual controls, and viewport changes invalidate pending work. There
is one in-flight job, one prepared next frame, and no process-side backlog of
photos. Background cache-only jobs do not transfer images. Pixel transfer uses
bounded shared memory (viewport axes capped at 4096), with only small metadata
on the result queue. Source metadata is checked again before display.

The previous frame stays visible during preparation, and the slideshow timer
starts when the new frame is displayed. Each job has a five-second deadline,
including cache access and hashing. A failed/timed-out requested job gets one
ordinary-framing preparation attempt with its own deadline. Detector errors also
produce ordinary framing. A displayed fallback is final: late results never
reframe it. If ordinary preparation also fails, the photo is skipped; after one
unsuccessful pass through the album playback stops. Hung workers are terminated;
after three hangs/crashes per service lifetime the worker circuit stays closed
until service restart. Failures are logged, and shutdown releases the process
and shared memory. Controls remain available while jobs are pending.

Cold detection cannot promise face framing within a finite deadline. Real
inference still requires the separately owned model pin above; the current empty
`MODEL_SHA256` is intentionally unchanged. Deterministic gated-worker tests cover
scheduling, stale rejection, fallback, and recovery without the model. They do
not establish Raspberry Pi throughput. Detector/cache redesign and deployment
validation remain outside this milestone.

Test fixtures live in `tests/fixtures/faces/`; their provenance and licenses are
documented there. The acceptance tests run real YuNet inference and are skipped
automatically until the model is downloaded and pinned.

## Validation

```sh
cd /home/roman/photo-library
python3 -m unittest discover -v
# Use the PR base commit (or your branch's base) here, in Bash.
BASE_SHA='<base-commit>'
git diff --name-only --diff-filter=ACMR -z "$BASE_SHA" -- '*.py' > /tmp/changed-python
mapfile -d '' -t files < /tmp/changed-python
if ((${#files[@]})); then
  python3 -m ruff check -- "${files[@]}"
  python3 -m ruff format --check -- "${files[@]}"
fi
```

Face settings and the review HTTP endpoints are covered by
`test_review_settings.py` (validation, persistence across restart, invalid
bodies, player failure, and the localhost Host / same-origin protections). The
browser controls are covered by the jsdom suite under `tests/browser/`, which
uses a pinned jsdom version and runs in CI alongside the Python tests:

```sh
npm install --no-audit --no-fund
npm test
```

Tests cover truncated images, exact and pixel duplicates, date conflicts, manual year overrides, preservation of originals, and the face cache and detector contract. Live checks also exercise the Pi player commands and the local review HTTP endpoints.

CI runs unittest discovery and `npm test`. Both Ruff checks cover every added,
copied, modified, or renamed Python file relative to the PR base SHA or push's
previous SHA. Checkout includes history; if the base is unavailable (including
a new branch push), CI checks every tracked Python file. Deleted files are
excluded. Add new files to Git before running the local diff check above.

### Manual Pi smoke check

Run on the Pi as the desktop/player user, using the distro Python with
`python3-opencv` installed. Download and pin the model as described above and
fetch the licensed fixtures. `MODEL_SHA256` is currently empty: model loading
is **unverified** until the artifact is downloaded and pinned on the Pi.
`face_detector.py --verify` checks the checksum only; the fixture inference in
the smoke script also exercises actual OpenCV model loading and `face_frame`.

```sh
cd /home/roman/photo-library
python3 tests/fixtures/faces/fetch_fixtures.py
~/bin/photos start 2010
python3 smoke.py --viewport 1920 1080 > /tmp/pi-smoke-single.json
python3 smoke.py --fixture tests/fixtures/faces/group.jpg \
  --viewport 1920 1080 > /tmp/pi-smoke-group.json
```

Use the display's actual viewport dimensions and an available album. The script
does not start or change playback. It records each check's elapsed time, errors,
status response, detected boxes, zoom and pan, continuing after failures and
exiting nonzero if any check fails. The status round-trip budget defaults to
1000 ms (`--status-budget-ms` overrides it); subprocesses time out after 30
seconds (`--timeout`). A running slideshow is required for a passing status check.

Before merging, attach both JSON reports and record the Pi model, OS version,
`python3 -c 'import cv2; print(cv2.__version__)'` result, and installed
`python3-opencv` package version. Confirm real inference succeeds with that
distro build; CI does not establish Pi OpenCV compatibility. With face detection
enabled in the review page, record cold/warm playback observations: next,
previous, pause and resume remain responsive during preparation, single/group
faces remain visible, and manual zoom/reset still works. Repeat the status
measurement during cold preparation. The numeric crop check and a single status
sample do not replace these display/control observations. Missing model,
fixtures, hardware results, or distro compatibility evidence remain unverified,
even if the local and CI suites pass.
