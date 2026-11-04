"""Saved local folder albums, independent of the curated year catalog."""
import hashlib
import json
import os
from pathlib import Path
import subprocess

from PIL import Image
from catalog import atomic_json


def albums(data):
    path = data / 'folders.json'
    return json.loads(path.read_text()) if path.exists() else {}


def summaries(data):
    return [dict(id=key, name=value['name'], path=value['path'],
                 count=len(value['items'])) for key, value in albums(data).items()]


def choose_folder():
    env = dict(os.environ)
    env.setdefault('DISPLAY', ':0')
    env.setdefault('XAUTHORITY', str(Path.home() / '.Xauthority'))
    result = subprocess.run(
        ['zenity', '--file-selection', '--directory', '--title=Add photo folder',
         '--filename=' + str(Path.home()) + '/', '--modal'],
        capture_output=True, text=True, env=env)
    if result.returncode == 1:
        return None
    if result.returncode:
        raise ValueError('Could not open the folder chooser on the Pi desktop.')
    return result.stdout.rstrip('\n') or None


def add_folder(data, folder):
    path = Path(folder).expanduser().resolve(strict=True)
    if not path.is_dir():
        raise ValueError('Select a folder.')
    Image.init()
    extensions = set(Image.registered_extensions())
    items = []
    skipped = 0
    def inaccessible(error):
        raise ValueError(f'Cannot read folder: {error.filename}')
    for parent, directories, files in os.walk(path, onerror=inaccessible, followlinks=False):
        directories.sort(key=str.casefold)
        for filename in sorted(files, key=str.casefold):
            photo = Path(parent) / filename
            if photo.suffix.lower() not in extensions:
                continue
            try:
                with Image.open(photo) as image:
                    image.verify()
                items.append({'path': str(photo)})
            except (OSError, ValueError, SyntaxError, Image.DecompressionBombError):
                skipped += 1
    if not items:
        raise ValueError('No readable photos found in this folder or its subfolders.')
    key = hashlib.sha256(os.fsencode(path)).hexdigest()[:20]
    saved = albums(data)
    saved[key] = dict(name=path.name or str(path), path=str(path), items=items)
    atomic_json(data / 'folders.json', saved)
    return dict(id=key, name=saved[key]['name'], count=len(items), skipped=skipped)


def playlist(data, key):
    album = albums(data).get(key)
    if album is None:
        raise ValueError('Folder album not found. Add the folder again.')
    if not Path(album['path']).is_dir():
        raise ValueError('Photo folder is unavailable. Check that its drive is connected.')
    return album['name'], album['items']
