#!/usr/bin/env python3
"""Finish the first installation once its background audit completes."""
from pathlib import Path
import subprocess
import time

base=Path(__file__).resolve().parent
while subprocess.run(['systemctl','--user','is-active','--quiet','photo-library-audit-full']).returncode==0:
    time.sleep(15)
if not (base/'data/audit.json').exists():
    raise RuntimeError('Audit did not finish successfully; inspect audit.log and resume audit.py')
for command in [['jpeg_check.py'],['enrich.py'],['catalog.py','--activate']]:
    subprocess.run(['/usr/bin/python3',str(base/command[0])]+command[1:],cwd=base,check=True)
print('LIBRARY READY',flush=True)
