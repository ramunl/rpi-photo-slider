#!/usr/bin/env python3
"""Recheck date provenance and JPEG trailing data after the full audit."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import subprocess
from PIL import Image
from audit import date_value, resolve_date
from catalog import atomic_json

DIRECTORIES={}

def sidecar(path):
    stem,ext=path.stem,path.suffix
    names=[path.name+'.json',path.name+'.supplemental-metadata.json',stem+'.json']
    m=re.match(r'^(.*?)(\(\d+\))$',stem)
    if m:
        names += [m[1]+ext+m[2]+'.json',m[1]+ext+'.supplemental-metadata'+m[2]+'.json']
    base=re.sub(r'[-_](edited|EFFECTS|SMILE|COLLAGE|ANIMATION|MIX|редактированное)$','',stem,flags=re.I)
    if base!=stem:
        names += [base+ext+'.json',base+ext+'.supplemental-metadata.json',base+'.json']
    names += [path.name[:n]+'.json' for n in range(len(path.name)-1,max(len(path.name)-12,3),-1)]
    if path.parent not in DIRECTORIES:
        DIRECTORIES[path.parent]={p.name for p in path.parent.iterdir()}
    for name in names:
        candidate=path.with_name(name)
        if name in DIRECTORIES[path.parent]:
            try:
                meta=json.loads(candidate.read_text())
                timestamp=(meta.get('photoTakenTime') or {}).get('timestamp')
                if timestamp:
                    return datetime.fromtimestamp(int(timestamp),timezone.utc).replace(tzinfo=None).isoformat(),str(candidate)
            except (ValueError,OverflowError,OSError):
                pass
    return None,None

def main():
    p=argparse.ArgumentParser();p.add_argument('--data',type=Path,default=Path(__file__).resolve().parent/'data');args=p.parse_args()
    rows=json.loads((args.data/'audit.json').read_text())
    for i,r in enumerate(rows):
        path=Path(r['path'])
        if path.name.startswith('.trashed-') or any(x.casefold() in {'trash','bin','корзина','удалённые','удаленные'} for x in path.parts):
            r['review_reasons'].append('trash_folder')
        thumb=args.data/'thumbs'/(r['id']+'.jpg')
        if r['kind']=='photo' and r['health']=='ok' and thumb.exists():
            with Image.open(thumb) as im:
                pixels=list(im.convert('RGB').resize((96,72)).getdata())
            white=sum(min(p)>205 and max(p)-min(p)<35 for p in pixels)/len(pixels)
            neutral=sum(max(p)-min(p)<30 for p in pixels)/len(pixels)
            r['white_fraction']=round(white,3)
            r['neutral_fraction']=round(neutral,3)
            if white>.28 and neutral>.65:
                r['review_reasons'].append('suspected_document')
        if '_takeout_staging' in path.parts:
            taken,evidence=sidecar(path)
            r['date_candidates'].pop('takeout',None)
            previous={'date':r.get('previous_date'),'date_src':r.get('previous_date_source')}
            if taken and date_value(taken):
                r['date_candidates']['takeout']=taken
                r['sidecar']=evidence
            previous['date_src']=None # Never reinstate the old creationTime fallback.
            r['date']=None;r['date_source']=None
            resolve_date(r,previous)
            r['previous_date_source']='takeout-sidecar'
            r['review_reasons']=[x for x in r['review_reasons'] if x not in {'unknown_date','date_conflict'}]
            if not r['date']:
                r['review_reasons'].append('unknown_date')
            if r['date_conflict']:
                r['review_reasons'].append('date_conflict')
        # Capture EXIF corroborated by an independently dated filename beats a different export year.
        c=r['date_candidates']
        if r.get('date_conflict') and c.get('exif_original') and c.get('filename') and c['exif_original'][:4]==c['filename'][:4]:
            r['review_reasons']=[x for x in r['review_reasons'] if x!='date_conflict']
            r['date_resolution']='Capture EXIF and filename agree; export year differs'
        marker='JPEG does not end with EOI marker; needs review'
        if marker in r.get('warnings',[]):
            # Motion photos and recovery padding can legitimately follow the JPEG EOI.
            # Use the independent FFmpeg decoder before clearing this structural warning.
            try:
                result=subprocess.run(['ffmpeg','-nostdin','-v','error','-xerror','-threads','1','-i',r['path'],'-frames:v','1','-f','null','-'],stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,timeout=60)
            except subprocess.TimeoutExpired:
                r['health']='unverified';r['error']='Independent JPEG decoding timed out';continue
            if result.returncode==0 and not result.stderr.strip():
                r['warnings'].remove(marker)
                r['trailing_data_verified']=True
                if not r['warnings']:
                    r['review_reasons']=[x for x in r['review_reasons'] if x!='decoder_warning']
            else:
                r['health']='broken';r['error']=result.stderr.decode(errors='replace')[-3000:]
        r['review_reasons']=list(dict.fromkeys(r['review_reasons']))
        if i%1000==0:print('Enriched',i,'/',len(rows),flush=True)
    atomic_json(args.data/'audit.json',rows)
    print('ENRICHMENT COMPLETE',flush=True)

if __name__=='__main__':main()
