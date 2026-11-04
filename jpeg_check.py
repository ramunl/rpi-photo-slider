#!/usr/bin/env python3
"""Strict JPEG integrity checking, once per byte-identical content group."""
import argparse
import concurrent.futures
import json
from pathlib import Path
import subprocess
import time
from catalog import atomic_json

BASE=Path(__file__).resolve().parent

def check(batch,binary):
    result=subprocess.run([str(binary),'-c','--json']+[r['path'] for r in batch],capture_output=True,text=True,timeout=600)
    reports=json.loads(result.stdout)
    by_path={r['path']:r for r in batch}
    for report in reports:
        original=by_path[report['filename']]
        report['sha256']=original['sha256']
    return reports

def main():
    p=argparse.ArgumentParser();p.add_argument('--data',type=Path,default=BASE/'data');p.add_argument('--workers',type=int,default=3);args=p.parse_args()
    binary=BASE/'vendor/jpeginfo/usr/bin/jpeginfo'
    if not binary.exists():
        raise RuntimeError('Install the per-user jpeginfo package described in README.md')
    rows=json.loads((args.data/'audit.json').read_text())
    cache={}
    log=args.data/'jpeg-check.jsonl'
    if log.exists():
        for line in log.read_text().splitlines():
            try:
                r=json.loads(line);cache[r['sha256']]=r
            except ValueError:pass
    unique={r['sha256']:r for r in rows if r['kind']=='photo' and r.get('sha256') and (r.get('format')=='JPEG' or (not r.get('format') and Path(r['path']).suffix.lower() in {'.jpg','.jpeg'}))}
    todo=[r for sha,r in unique.items() if sha not in cache]
    print('JPEG contents:',len(unique),'cached:',len(unique)-len(todo),'to check:',len(todo),flush=True)
    start=time.time()
    with log.open('a') as f,concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures=[pool.submit(check,todo[i:i+40],binary) for i in range(0,len(todo),40)]
        for future in concurrent.futures.as_completed(futures):
            for r in future.result():
                cache[r['sha256']]=r;f.write(json.dumps(r,ensure_ascii=False)+'\n')
            f.flush()
            print('Checked',sum(sha in cache for sha in unique),'/',len(unique),'seconds',round(time.time()-start),flush=True)
    for r in rows:
        report=cache.get(r.get('sha256'))
        if not report:continue
        r['jpeg_integrity']=report['status']
        if report['status']!='OK':
            r['jpeg_detail']=report.get('status_detail','')
            if report['status']=='ERROR':
                r['health']='broken';r['error']=r['jpeg_detail']
            elif 'jpeg_integrity_warning' not in r['review_reasons']:
                r['review_reasons'].append('jpeg_integrity_warning')
        else:
            marker='JPEG does not end with EOI marker; needs review'
            if marker in r.get('warnings',[]):
                r['warnings'].remove(marker);r['trailing_data_verified']=True
                if not r['warnings']:
                    r['review_reasons']=[x for x in r['review_reasons'] if x!='decoder_warning']
    atomic_json(args.data/'audit.json',rows)
    print('JPEG CHECK COMPLETE',flush=True)

if __name__=='__main__':main()
