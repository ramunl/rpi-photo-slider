#!/usr/bin/env python3
"""Install per-user services and desktop launchers on the Raspberry Pi."""
from pathlib import Path
import subprocess

base=Path(__file__).resolve().parent
home=Path.home()
units=home/'.config/systemd/user';units.mkdir(parents=True,exist_ok=True)
for name,script,description in [('player','player.py serve','Photo Years fullscreen player'),('review','review.py','Photo Years local review')]:
    (units/f'photo-library-{name}.service').write_text(f'''[Unit]
Description={description}
After=default.target

[Service]
Type=simple
WorkingDirectory={base}
Environment=DISPLAY=:0
Environment=XAUTHORITY={home}/.Xauthority
ExecStart=/usr/bin/python3 {base}/{script}
StandardOutput=append:{base}/{name}.log
StandardError=append:{base}/{name}.log
Restart=on-failure
RestartSec=15

[Install]
WantedBy=default.target
''')
bin=home/'bin';bin.mkdir(exist_ok=True)
(bin/'photos').write_text(f'#!/bin/sh\nexec /usr/bin/python3 "{base}/player.py" "$@"\n')
(bin/'photos').chmod(0o755)
applications=home/'.local/share/applications';applications.mkdir(parents=True,exist_ok=True)
for filename,name,command,icon in [('photo-years.desktop','Photos by year',f'{bin}/photos picker','folder-pictures'),('photo-review.desktop','Review photo library','chromium http://127.0.0.1:8787','image-x-generic')]:
    entry=f'[Desktop Entry]\nType=Application\nName={name}\nExec={command}\nIcon={icon}\nTerminal=false\nCategories=Graphics;Photography;\n'
    (applications/filename).write_text(entry)
    desktop=home/'Desktop'/filename;desktop.write_text(entry);desktop.chmod(0o755)
subprocess.run(['systemctl','--user','daemon-reload'],check=True)
subprocess.run(['systemctl','--user','enable','--now','photo-library-player','photo-library-review'],check=True)
print('Installed Photos by year and Review photo library desktop launchers.')
