#!/usr/bin/env python3
"""Read-only media audit. Originals are never changed. Resumable JSONL cache."""
import argparse
import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time
import warnings
from datetime import datetime, timezone
from PIL import Image, ImageOps, UnidentifiedImageError

PHOTO = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.heic', '.heif', '.webp', '.tif', '.tiff', '.avif'}
VIDEO = {'.avi', '.mp4', '.mov', '.3gp', '.mts', '.m4v', '.mkv', '.wmv', '.mpg', '.mpeg', '.m2ts'}
SKIP = {'Documents', '.Trash-1000', '_stale', '_rejected', 'System Volume Information', '$RECYCLE.BIN'}
VERSION = 1

def date_value(value):
    if not value:
        return None
    s = str(value).strip().rstrip('\0')
    if re.match(r'^\d{4}:\d{2}:\d{2}', s):
        s = s[:10].replace(':', '-') + s[10:]
    try:
        d = datetime.fromisoformat(s.replace('Z', '+00:00'))
        if 1900 <= d.year <= datetime.now().year and d.date() <= datetime.now().date():
            return d.replace(tzinfo=None).isoformat()
    except ValueError:
        pass
    return None

def name_date(name):
    m = re.search(r'(?<!\d)((?:19|20)\d{2})[-_.]?([01]\d)[-_.]?([0-3]\d)(?!\d)', name)
    if not m: # Date followed immediately by a six digit time.
        m = re.search(r'(?<!\d)((?:19|20)\d{2})([01]\d)([0-3]\d)\d{6}(?!\d)', name)
    if m:
        return date_value('-'.join(m.groups()))
    return None

def resolve_date(r, old):
    candidates = r['date_candidates']
    n = name_date(Path(r['path']).name)
    if n:
        candidates['filename'] = n
    if old.get('date_src') == 'takeout-sidecar' and date_value(old.get('date')):
        candidates['takeout'] = date_value(old['date'])
    # Capture metadata takes priority. Modification times and year ranges are not capture dates.
    order = ['exif_original', 'exif_digitized', 'video_creation', 'takeout', 'filename']
    for source in order:
        if candidates.get(source):
            r['date'], r['date_source'] = candidates[source], source
            break
    r.setdefault('date', None)
    r.setdefault('date_source', None)
    reliable = {k: v for k, v in candidates.items() if k in order}
    r['date_conflict'] = len({v[:4] for v in reliable.values()}) > 1
    r['previous_date'] = old.get('date')
    r['previous_date_source'] = old.get('date_src')
    r['year_changed'] = bool(r['date'] and old.get('date') and r['date'][:4] != old['date'][:4])

def inspect_photo(r, data, thumbdir):
    p = r['path']
    try:
        with warnings.catch_warnings(record=True) as ws:
            warnings.simplefilter('always')
            with Image.open(p) as im:
                r['format'] = im.format
                im.verify()
            with Image.open(p) as im:
                ex = dict(im.getexif())
                try:
                    ex.update(im.getexif().get_ifd(34665))
                except (KeyError, ValueError, SyntaxError, TypeError):
                    pass
                for tag, label in [(36867, 'exif_original'), (36868, 'exif_digitized'), (306, 'exif_modified')]:
                    d = date_value(ex.get(tag))
                    if d:
                        r['date_candidates'][label] = d
                r['camera'] = ' '.join(str(ex.get(k, '')).strip() for k in (271, 272)).strip()
                r['software'] = str(ex.get(305, ''))
                r['frames'] = getattr(im, 'n_frames', 1)
                for frame in range(r['frames']):
                    im.seek(frame)
                    im.load()
                im.seek(0)
                im = ImageOps.exif_transpose(im).convert('RGB')
                r['width'], r['height'] = im.size
                r['pixel_sha256'] = hashlib.sha256(str(im.size).encode() + im.tobytes()).hexdigest() if r['frames'] == 1 else None
                tiny = im.resize((9, 8)).convert('L')
                pixels = list(tiny.getdata())
                bits = [pixels[y*9+x] > pixels[y*9+x+1] for y in range(8) for x in range(8)]
                r['dhash'] = f'{sum(int(b) << i for i,b in enumerate(bits)):016x}'
                im.thumbnail((480, 360))
                im.save(thumbdir / (r['id'] + '.jpg'), quality=82)
            r['warnings'] = list(dict.fromkeys(str(w.message) for w in ws))
        r['health'] = 'ok'
        if r['format'] == 'JPEG' and not data.rstrip().endswith(b'\xff\xd9'):
            r['warnings'].append('JPEG does not end with EOI marker; needs review')
        if r['warnings']:
            r['review_reasons'].append('decoder_warning')
        if max(r['width'], r['height']) < 480:
            r['review_reasons'].append('small_image')
        if not r['camera']:
            r['review_reasons'].append('no_camera_metadata')
        if re.search(r'screenshot|screen.?shot|скриншот|(?:^|[ _-])icon(?:[ _.-]|$)|wallpaper|meme', Path(p).name, re.I):
            r['review_reasons'].append('non_photo_filename')
        if r['frames'] > 1:
            r['review_reasons'].append('animated_image')
    except UnidentifiedImageError as e:
        r['health'] = 'unsupported' if Path(p).suffix.lower() in {'.heic','.heif','.avif'} else 'broken'
        r['error'] = str(e)
    except (OSError, ValueError, SyntaxError, Image.DecompressionBombError, EOFError) as e:
        r['health'], r['error'] = 'broken', str(e)

def inspect_video(r):
    try:
        probe = subprocess.run(['ffprobe', '-v', 'error', '-show_format', '-show_streams', '-of', 'json', r['path']], capture_output=True, text=True, timeout=60)
        meta = json.loads(probe.stdout or '{}')
        streams = meta.get('streams', [])
        if probe.returncode or not any(s.get('codec_type') == 'video' for s in streams):
            r['health'], r['error'] = 'broken', probe.stderr[-3000:] or 'No video stream'
            return
        r['duration'] = float(meta.get('format', {}).get('duration', 0))
        for container in [meta.get('format', {})] + streams:
            for key, value in container.get('tags', {}).items():
                if key.lower() in {'creation_time','date','datetimeoriginal','icrd'}:
                    d = date_value(value)
                    if d:
                        r['date_candidates'].setdefault('video_creation', d)
        # Decode every video frame and audio sample; never equate readable headers with healthy media.
        check = subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-xerror', '-threads', '1', '-i', r['path'], '-map', '0:v', '-map', '0:a?', '-f', 'null', '-'], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, timeout=max(180, r['duration'] * 3))
        r['health'] = 'ok' if check.returncode == 0 and not check.stderr.strip() else 'broken'
        if r['health'] != 'ok':
            r['error'] = check.stderr[-3000:]
    except subprocess.TimeoutExpired:
        r['health'], r['error'] = 'unverified', 'Full video decoding timed out'
    except (OSError, ValueError) as e:
        r['health'], r['error'] = 'unverified', str(e)

def inspect(item, thumbdir):
    p, old, sig = item
    r = {'path':p, 'id':hashlib.sha256(p.encode()).hexdigest()[:20], 'signature':sig,
         'kind':'video' if Path(p).suffix.lower() in VIDEO else 'photo',
         'date_candidates':{}, 'review_reasons':[], 'size':sig[2]}
    try:
        h = hashlib.sha256()
        with open(p, 'rb') as f:
            if r['kind'] == 'photo':
                data = f.read()
                h.update(data)
            else:
                for b in iter(lambda: f.read(1024*1024), b''):
                    h.update(b)
        r['sha256'] = h.hexdigest()
        if r['kind'] == 'photo':
            inspect_photo(r, data, thumbdir)
        else:
            inspect_video(r)
        resolve_date(r, old)
        if not r['date']:
            r['review_reasons'].append('unknown_date')
        if r['date_conflict']:
            r['review_reasons'].append('date_conflict')
        st = os.stat(p)
        if [VERSION, st.st_ino, st.st_size, st.st_mtime_ns] != sig:
            r['health'], r['error'] = 'unverified', 'File changed during scan; rerun audit'
    except Exception as e:
        r['health'], r['error'] = 'unverified', f'{type(e).__name__}: {e}'
    return r

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--root', default='/mnt/backup2tb')
    ap.add_argument('--manifest', default='/home/roman/media-manifest.json')
    ap.add_argument('--output', default=str(Path(__file__).resolve().parent / 'data'))
    ap.add_argument('--workers', type=int, default=2)
    args = ap.parse_args()
    out = Path(args.output); out.mkdir(parents=True, exist_ok=True)
    thumbs = out / 'thumbs'; thumbs.mkdir(exist_ok=True)
    old = {r['path']:r for r in json.load(open(args.manifest))} if Path(args.manifest).exists() else {}
    paths = set()
    for base, dirs, names in os.walk(args.root):
        dirs[:] = [d for d in dirs if d not in SKIP and not d.startswith('Library') and not d.startswith('.') and not os.path.islink(os.path.join(base,d))]
        for n in names:
            p = os.path.join(base,n)
            if Path(n).suffix.lower() in PHOTO | VIDEO and not os.path.islink(p):
                paths.add(p)
    cache = {}
    if (out / 'audit.jsonl').exists():
        for line in (out / 'audit.jsonl').read_text().splitlines():
            try:
                r = json.loads(line); cache[r['path']] = r
            except (ValueError, KeyError):
                pass
    todo, records = [], []
    for p in sorted(paths):
        st = os.stat(p); sig = [VERSION, st.st_ino, st.st_size, st.st_mtime_ns]
        if p in cache and cache[p].get('signature') == sig and cache[p].get('health') != 'unverified':
            r = cache[p]; r['date_candidates'] = {k:v for k,v in r['date_candidates'].items() if k not in {'takeout','filename'}}
            r['date'] = None; r['date_source'] = None
            resolve_date(r, old.get(p, {})); records.append(r)
        else:
            todo.append((p, old.get(p, {}), sig))
    todo.sort(key=lambda x: Path(x[0]).suffix.lower() in VIDEO)
    print(json.dumps({'discovered':len(paths),'cached':len(records),'to_scan':len(todo),'missing_manifest_files':len(set(old)-paths)}), flush=True)
    start = time.time()
    with (out / 'audit.jsonl').open('a') as log, concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(inspect, item, thumbs) for item in todo]
        for i, future in enumerate(concurrent.futures.as_completed(futures), 1):
            r = future.result(); records.append(r)
            log.write(json.dumps(r, ensure_ascii=False) + '\n'); log.flush()
            if i % 100 == 0 or r['kind'] == 'video':
                print(json.dumps({'done':i,'total':len(todo),'seconds':round(time.time()-start), 'last_kind':r['kind'], 'last_health':r['health']}), flush=True)
    records.sort(key=lambda r:r['path'])
    tmp = out / 'audit.json.tmp'; tmp.write_text(json.dumps(records,ensure_ascii=False)); tmp.replace(out / 'audit.json')
    print('AUDIT COMPLETE', len(records), flush=True)

if __name__ == '__main__':
    main()
