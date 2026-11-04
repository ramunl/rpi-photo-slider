#!/usr/bin/env python3
"""Record an explicitly inspected contact sheet; never infer decisions from content."""
import argparse
import json
from pathlib import Path
from catalog import atomic_json

p=argparse.ArgumentParser()
p.add_argument('filter');p.add_argument('--page',type=int,default=0)
p.add_argument('--exclude',default='');p.add_argument('--skip',default='')
p.add_argument('--data',type=Path,default=Path(__file__).resolve().parent/'data')
a=p.parse_args()
excluded={int(x) for x in a.exclude.split(',') if x};skipped={int(x) for x in a.skip.split(',') if x}
mapping=json.loads((a.data/f'sheet-{a.filter}-{a.page}-map.json').read_text())
decisions=json.loads((a.data/'decisions.json').read_text()) if (a.data/'decisions.json').exists() else {}
for index,id in mapping.items():
    if id not in decisions and int(index) not in skipped:
        decisions[id]={'classification':'exclude' if int(index) in excluded else 'photo','note':'Visually reviewed contact sheet; year not inferred'}
atomic_json(a.data/'decisions.json',decisions)
print('Total reviewed:',len(decisions))
