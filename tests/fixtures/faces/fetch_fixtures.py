#!/usr/bin/env python3
"""Download the public-domain face fixtures and record their provenance.

Run from the repository root:

    python3 tests/fixtures/faces/fetch_fixtures.py

Each image is downloaded into this directory. Once a fixture's SHA-256 has been
recorded in manifest.json it is verified on every later run; an existing local
file with different bytes is never silently overwritten. Fill in the expected
boxes by running the real-model acceptance tests after downloading.
"""
import hashlib
import json
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
MANIFEST = HERE / 'manifest.json'


def fetch():
    manifest = json.loads(MANIFEST.read_text())
    for entry in manifest['fixtures']:
        target = HERE / entry['file']
        url = entry['source_url']
        print(f'fetching {entry["file"]} <- {url}', flush=True)
        data = urllib.request.urlopen(url, timeout=60).read()
        digest = hashlib.sha256(data).hexdigest()
        expected = entry.get('sha256') or ''
        if expected and digest != expected:
            raise SystemExit(
                f'checksum mismatch for {entry["file"]}: expected {expected}, got {digest}')
        if target.exists() and hashlib.sha256(target.read_bytes()).hexdigest() != digest:
            raise SystemExit(f'refusing to overwrite {target} with different bytes')
        target.write_bytes(data)
        if not expected:
            entry['sha256'] = digest
    with MANIFEST.open('w') as stream:
        json.dump(manifest, stream, indent=2, ensure_ascii=False)
        stream.write('\n')
    print('done. Record expected boxes by running the real-model acceptance tests.')


if __name__ == '__main__':
    fetch()
