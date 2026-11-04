#!/usr/bin/env python3
"""Create an independently curated year view and machine-readable reports."""
import argparse
from collections import Counter, defaultdict
import csv
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import uuid
import tempfile

BASE = Path(__file__).resolve().parent

def atomic_json(path, data):
    with tempfile.NamedTemporaryFile(mode='w',dir=path.parent,prefix='.'+path.name+'-',delete=False) as f:
        json.dump(data,f,ensure_ascii=False,indent=2)
        tmp=Path(f.name)
    tmp.replace(path)

def load_records(data, partial=False):
    if (data / 'audit.json').exists() and not partial:
        return json.loads((data / 'audit.json').read_text())
    if not partial:
        raise RuntimeError('Audit has not completed. Use --partial only for provisional review.')
    records = {}
    for line in (data / 'audit.jsonl').read_text().splitlines():
        try:
            r = json.loads(line); records[r['path']] = r
        except (ValueError, KeyError):
            pass
    return list(records.values())

def compile_catalog(records, overrides):
    parent = list(range(len(records)))
    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]; i = parent[i]
        return i
    hashes = {}
    for i, r in enumerate(records):
        for key in ('sha256', 'pixel_sha256'):
            if r.get(key):
                token = (key, r[key], r['kind'])
                if token in hashes:
                    parent[root(i)] = root(hashes[token])
                else:
                    hashes[token] = i
    groups = defaultdict(list)
    for i, r in enumerate(records):
        groups[root(i)].append(r)
    result = []
    for group in groups.values():
        # Prefer healthy, full-size, dated originals with camera metadata.
        group.sort(key=lambda r:(r['health'] != 'ok', not bool(r.get('camera')), bool(r.get('date_conflict')), not bool(r.get('date')), len(r.get('review_reasons', [])), -r.get('size',0), r['path']))
        keep = group[0]
        known_years = {r['date'][:4] for r in group if r.get('date')}
        for n, original in enumerate(group):
            r = dict(original)
            r['review_reasons'] = list(r.get('review_reasons', []))
            r['duplicate_of'] = keep['id'] if n else None
            r['duplicate_paths'] = [x['path'] for x in group[1:]] if not n else []
            if not r.get('date') and len(known_years) == 1:
                source = next(x for x in group if x.get('date'))
                r['date'], r['date_source'] = source['date'], 'identical_copy'
                r['review_reasons'] = [x for x in r['review_reasons'] if x != 'unknown_date']
            if len(known_years) > 1:
                r['review_reasons'].append('duplicate_date_conflict')
            # Apply a human decision to all identical copies (each copy remains in reports).
            decisions = [overrides[x['id']] for x in group if x['id'] in overrides]
            decision = overrides.get(r['id'], decisions[0] if decisions else {})
            r['decision'] = decision
            if decision.get('year'):
                r['date'] = f"{int(decision['year']):04d}-01-01T00:00:00"
                r['date_source'] = 'manual_year'
                r['review_reasons'] = [x for x in r['review_reasons'] if x not in {'unknown_date','date_conflict','duplicate_date_conflict'}]
            if decision.get('classification') == 'photo':
                r['review_reasons'] = [x for x in r['review_reasons'] if x not in {'no_camera_metadata','non_photo_filename','small_image','animated_image','suspected_document'}]
            r['year'] = r['date'][:4] if r.get('date') else None
            if r['health'] != 'ok':
                r['status'] = r['health']
            elif decision.get('classification') == 'exclude':
                r['status'] = 'excluded'
            elif r['duplicate_of']:
                r['status'] = 'duplicate'
            elif r['review_reasons']:
                r['status'] = 'review'
            else:
                r['status'] = 'included'
            result.append(r)
    return sorted(result, key=lambda r:(r.get('date') or '9999',r['path']))

def near_duplicates(records):
    """BK tree for conservative review suggestions, never automatic exclusions."""
    tree = None
    pairs = []
    for r in records:
        if r['health'] != 'ok' or r.get('duplicate_of') or not r.get('dhash'):
            continue
        value = int(r['dhash'], 16)
        # Almost uniform images yield many meaningless hash collisions.
        if value.bit_count() < 8 or value.bit_count() > 56:
            continue
        stack = [tree] if tree else []
        while stack:
            node = stack.pop(); distance = (value ^ node[0]).bit_count()
            if distance <= 4:
                other = node[1]
                aspect = (r['width']/r['height']) / (other['width']/other['height'])
                if .98 <= aspect <= 1.02:
                    pairs.append({'a':other['id'],'b':r['id'],'distance':distance})
            stack.extend(child for edge, child in node[2].items() if distance-4 <= edge <= distance+4)
        if tree is None:
            tree = [value,r,{}]
        else:
            node = tree
            while True:
                distance = (value ^ node[0]).bit_count()
                if distance not in node[2]:
                    node[2][distance] = [value,r,{}]; break
                node = node[2][distance]
    return pairs

def build(data, library, partial=False):
    records = load_records(data, partial)
    overrides = json.loads((data/'decisions.json').read_text()) if (data/'decisions.json').exists() else {}
    rows = compile_catalog(records, overrides)
    pairs = near_duplicates(rows)
    summary = {'scanned':len(rows),'complete':not partial,'updated':datetime.now().isoformat(),
               'kinds':dict(Counter(r['kind'] for r in rows)),
               'status':dict(Counter(r['status'] for r in rows)),
               'health':dict(Counter(r['health'] for r in rows)),
               'review_reasons':dict(Counter(x for r in rows if r['status']=='review' for x in r['review_reasons'])),
               'year_changes':sum(r.get('year_changed',False) for r in rows),
               'near_duplicate_pairs':len(pairs),
               'years':dict(sorted(Counter(r['year'] for r in rows if r['status']=='included' and r['kind']=='photo').items()))}
    if library and not partial:
        library = Path(library)
        library.mkdir(parents=True, exist_ok=True)
        generation = library / ('.curated-' + datetime.now().strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:6])
        generation.mkdir()
        (generation/'Photos').mkdir()
        (generation/'Videos').mkdir()
        for r in rows:
            if r['status'] != 'included':
                continue
            st=os.stat(r['path'])
            if r.get('signature') and [st.st_ino,st.st_size,st.st_mtime_ns]!=r['signature'][1:]:
                raise RuntimeError(f"Source changed after auditing; rerun audit: {r['path']}")
            section = 'Photos' if r['kind']=='photo' else 'Videos'
            destdir = generation / section / r['year']; destdir.mkdir(parents=True,exist_ok=True)
            name = r['date'][:10] + '_' + r['id'] + Path(r['path']).suffix.lower()
            os.link(r['path'], destdir/name)
        link = library / ('.Slideshow-' + uuid.uuid4().hex)
        link.symlink_to(generation.name, target_is_directory=True)
        link.replace(library / 'Slideshow')
        summary['year_library'] = str(library / 'Slideshow')
    atomic_json(data/'catalog.json', {'summary':summary,'items':rows,'near_duplicates':pairs})
    atomic_json(data/'summary.json', summary)
    with (data/'report.tsv').open('w') as f:
        writer = csv.writer(f,delimiter='\t')
        writer.writerow(['id','status','kind','year','date_source','previous_date','health','reasons','duplicate_of','path','error'])
        for r in rows:
            writer.writerow([r['id'],r['status'],r['kind'],r['year'],r.get('date_source'),r.get('previous_date'),r['health'],','.join(r['review_reasons']),r.get('duplicate_of'),r['path'],r.get('error','')])
    print(json.dumps(summary,ensure_ascii=False,indent=2))
    return summary

def activate(library):
    """Archive existing year views, then point them at the curated generation."""
    library=Path(library)
    if not (library/'Slideshow').is_dir():
        raise RuntimeError('Build a completed curated library before activation')
    archive=library.parent/'_stale'/('Library.before-curation-'+datetime.now().strftime('%Y%m%d-%H%M%S'))
    for name,target in [('By-Date','Slideshow/Photos'),('Videos','Slideshow/Videos')]:
        current=library/name
        if current.is_symlink() and os.readlink(current)==target:
            continue
        pending=library/('.activate-'+uuid.uuid4().hex)
        pending.symlink_to(target,target_is_directory=True)
        if current.exists() or current.is_symlink():
            archive.mkdir(parents=True,exist_ok=True)
            current.rename(archive/name)
        pending.replace(current)
    print('Year views activated. Previous views archived at:',archive)

if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--data',type=Path,default=BASE/'data')
    p.add_argument('--library',default='/mnt/backup2tb/Library')
    p.add_argument('--partial',action='store_true')
    p.add_argument('--activate',action='store_true',help='Archive old By-Date/Videos views and replace with curated views')
    args = p.parse_args()
    build(args.data,args.library,args.partial)
    if args.activate:
        if args.partial:
            p.error('Cannot activate a partial audit')
        activate(args.library)
