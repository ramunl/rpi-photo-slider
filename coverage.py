#!/usr/bin/env python3
"""Check that every photo elsewhere in Library exists in By-Date by content.

The check is read-only with respect to the photo library. Hashes are cached in
JSONL so an interrupted run resumes without rereading completed files.
"""
import argparse
from collections import Counter, defaultdict
import hashlib
import json
import os
from pathlib import Path
import tempfile


PHOTO = {
    '.jpg', '.jpeg', '.png', '.gif', '.bmp', '.heic', '.heif', '.webp',
    '.tif', '.tiff', '.avif', '.raw', '.dng', '.cr2', '.cr3', '.nef',
    '.arw', '.orf', '.rw2', '.raf',
}
DERIVED_TOP_LEVEL = {'By-Date', 'Slideshow'}


def signature(path):
    st = path.stat()
    return [st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns]


def iter_photos(base):
    if not base.exists():
        return
    for directory, dirs, names in os.walk(base):
        dirs[:] = [d for d in dirs if not d.startswith('.') and not os.path.islink(os.path.join(directory, d))]
        for name in names:
            path = Path(directory, name)
            if path.suffix.lower() in PHOTO and not path.is_symlink():
                yield path


def outside_photos(root):
    for child in sorted(root.iterdir()):
        if child.name in DERIVED_TOP_LEVEL or child.name.startswith('.') or child.is_symlink():
            continue
        if child.is_dir():
            yield from iter_photos(child)


def load_cache(path):
    cached = {}
    if not path.exists():
        return cached
    for line in path.read_text().splitlines():
        try:
            row = json.loads(line)
            cached[row['path']] = row
        except (json.JSONDecodeError, KeyError, TypeError):
            continue
    return cached


def load_audit(path):
    """Reuse source hashes and metadata for hard-linked Library entries."""
    hashes = {}
    records = defaultdict(list)
    if not path.exists():
        return hashes, records
    try:
        rows = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return hashes, records
    for row in rows:
        sig = row.get('signature')
        digest = row.get('sha256')
        if digest and isinstance(sig, list) and len(sig) == 4:
            hashes[(sig[1], sig[2], sig[3])] = digest
            records[(sig[1], sig[2])].append(row)
    return hashes, records


def hash_files(paths, cache_path, audit_hashes):
    cached = load_cache(cache_path)
    inode_hashes = {}
    results = {}
    errors = []
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    with cache_path.open('a') as log:
        for number, path in enumerate(paths, 1):
            name = str(path)
            try:
                sig = signature(path)
                old = cached.get(name)
                if old and old.get('signature') == sig and old.get('sha256'):
                    digest = old['sha256']
                else:
                    inode_key = tuple(sig[:3])
                    audit_key = tuple(sig[1:])
                    digest = inode_hashes.get(inode_key) or audit_hashes.get(audit_key)
                    if not digest:
                        value = hashlib.sha256()
                        with path.open('rb') as source:
                            for block in iter(lambda: source.read(1024 * 1024), b''):
                                value.update(block)
                        digest = value.hexdigest()
                    row = {'path': name, 'signature': sig, 'sha256': digest}
                    log.write(json.dumps(row, ensure_ascii=False) + '\n')
                    log.flush()
                inode_hashes[tuple(sig[:3])] = digest
                results[name] = {'sha256': digest, 'size': sig[2], 'signature': sig}
            except OSError as error:
                errors.append({'path': name, 'error': str(error)})
            if number % 250 == 0:
                print(json.dumps({'hashed': number, 'total': len(paths), 'errors': len(errors)}), flush=True)
    return results, errors


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile('w', dir=path.parent, prefix='.' + path.name + '-', delete=False) as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        temporary = Path(stream.name)
    temporary.replace(path)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, default=Path('/mnt/backup2tb/Library'))
    parser.add_argument('--data', type=Path, default=Path(__file__).resolve().parent / 'data')
    args = parser.parse_args()

    sorted_root = args.root / 'By-Date'
    if not sorted_root.is_dir():
        parser.error(f'year-sorted directory does not exist: {sorted_root}')

    sorted_paths = sorted(iter_photos(sorted_root), key=str)
    outside_paths = sorted(outside_photos(args.root), key=str)
    all_paths = sorted_paths + outside_paths
    print(json.dumps({'sorted_photos': len(sorted_paths), 'outside_photos': len(outside_paths), 'to_compare': len(all_paths)}), flush=True)

    audit_hashes, audit_records = load_audit(args.data / 'audit.json')
    decisions_path = args.data / 'decisions.json'
    decisions = json.loads(decisions_path.read_text()) if decisions_path.exists() else {}
    hashes, errors = hash_files(
        all_paths,
        args.data / 'coverage-cache.jsonl',
        audit_hashes,
    )
    sorted_hashes = {hashes[str(path)]['sha256'] for path in sorted_paths if str(path) in hashes}
    missing = []
    matching = 0
    outside_by_top = Counter()
    missing_by_top = Counter()
    for path in outside_paths:
        row = hashes.get(str(path))
        if not row:
            continue
        relative = path.relative_to(args.root)
        top = relative.parts[0]
        outside_by_top[top] += 1
        if row['sha256'] in sorted_hashes:
            matching += 1
        else:
            missing_by_top[top] += 1
            match = audit_records.get((row['signature'][1], row['signature'][2]), [])
            audit = match[0] if match else None
            decision = decisions.get(audit['id'], {}) if audit else {}
            missing.append({
                'path': str(relative),
                'sha256': row['sha256'],
                'size': row['size'],
                'audit': ({
                    'source_path': audit['path'],
                    'health': audit.get('health'),
                    'date': audit.get('date'),
                    'date_source': audit.get('date_source'),
                    'review_reasons': audit.get('review_reasons', []),
                    'decision': decision,
                } if audit else None),
            })

    missing_groups = defaultdict(list)
    for row in missing:
        missing_groups[row['sha256']].append(row['path'])
    matched_audit = [row['audit'] for row in missing if row['audit']]
    report = {
        'root': str(args.root),
        'sorted': {'paths': len(sorted_paths), 'unique_contents': len(sorted_hashes)},
        'outside': {
            'paths': len(outside_paths),
            'by_top_level': dict(sorted(outside_by_top.items())),
            'paths_already_in_sorted': matching,
            'paths_missing_from_sorted': len(missing),
            'unique_contents_missing_from_sorted': len(missing_groups),
            'missing_by_top_level': dict(sorted(missing_by_top.items())),
            'audit_matches': len(matched_audit),
            'health': dict(sorted(Counter(row.get('health') for row in matched_audit).items(), key=lambda pair: str(pair[0]))),
            'dated_by_year': dict(sorted(Counter(row['date'][:4] for row in matched_audit if row.get('date')).items())),
            'date_sources': dict(sorted(Counter(row.get('date_source') for row in matched_audit).items(), key=lambda pair: str(pair[0]))),
            'manual_years': dict(sorted(Counter(str(row['decision']['year']) for row in matched_audit if row.get('decision', {}).get('year')).items())),
            'decisions': dict(sorted(Counter(row.get('decision', {}).get('classification', 'none') for row in matched_audit).items())),
            'review_reasons': dict(sorted(Counter(reason for row in matched_audit for reason in row.get('review_reasons', [])).items())),
        },
        'missing': missing,
        'missing_content_groups': [
            {'sha256': digest, 'paths': paths}
            for digest, paths in sorted(missing_groups.items())
        ],
        'errors': errors,
        'complete': not errors,
    }
    atomic_json(args.data / 'coverage.json', report)
    print(json.dumps({k: report[k] for k in ('sorted', 'outside', 'errors', 'complete')}, ensure_ascii=False, indent=2), flush=True)


if __name__ == '__main__':
    main()
