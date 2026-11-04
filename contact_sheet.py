#!/usr/bin/env python3
"""Make labeled local-only contact sheets for reviewing audit results."""
import argparse
import base64
from pathlib import Path
import json
from PIL import Image, ImageDraw, ImageFont
from catalog import load_records, compile_catalog

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--data',type=Path,default=Path(__file__).resolve().parent/'data')
    p.add_argument('--filter',default='no_camera_metadata')
    p.add_argument('--page',type=int,default=0)
    p.add_argument('--size',type=int,default=80)
    p.add_argument('--stdout',action='store_true')
    args=p.parse_args()
    overrides=json.loads((args.data/'decisions.json').read_text()) if (args.data/'decisions.json').exists() else {}
    rows=compile_catalog(load_records(args.data,partial=not (args.data/'audit.json').exists()),overrides)
    if args.filter=='camera':
        rows=[r for r in rows if r.get('camera') and r['health']=='ok' and not r['duplicate_of']]
    elif args.filter=='all':
        rows=[r for r in rows if r['health']=='ok' and not r['duplicate_of'] and r['kind']=='photo']
    else:
        rows=[r for r in rows if args.filter in r.get('review_reasons',[]) and not r['duplicate_of'] and r['health']=='ok' and r['status']!='excluded']
    rows.sort(key=lambda r:r['id'])
    selected=rows[args.page*args.size:(args.page+1)*args.size]
    cols=8;cw=200;ch=174
    sheet=Image.new('RGB',(cols*cw,40+((len(selected)+cols-1)//cols)*ch),'#151b20')
    draw=ImageDraw.Draw(sheet)
    try:font=ImageFont.truetype('DejaVuSans.ttf',13)
    except OSError:font=ImageFont.load_default()
    draw.text((10,8),f'{args.filter}: page {args.page} | {len(rows)} total | IDs correspond to sheet-map.json',font=font,fill='white')
    mapping={}
    for i,r in enumerate(selected):
        x=(i%cols)*cw;y=40+(i//cols)*ch
        path=args.data/'thumbs'/(r['id']+'.jpg')
        if path.exists():
            with Image.open(path) as im:
                im.thumbnail((196,140));sheet.paste(im,(x+(cw-im.width)//2,y))
        index=args.page*args.size+i
        mapping[str(index)]=r['id']
        draw.text((x+4,y+141),f'{index}: {r.get("year") or "?"} {r["id"][:6]}',font=font,fill='white')
        draw.text((x+4,y+157),Path(r['path']).name[:23],font=font,fill='#a5b7be')
    filename=args.data/f'sheet-{args.filter}-{args.page}.jpg'
    sheet.save(filename,quality=68)
    (args.data/f'sheet-{args.filter}-{args.page}-map.json').write_text(json.dumps(mapping))
    if args.stdout:
        print(base64.b64encode(filename.read_bytes()).decode())
    else:
        print(filename)

if __name__=='__main__':main()
