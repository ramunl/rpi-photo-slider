#!/usr/bin/env python3
"""Persistent, versioned photo-to-face cache backed by data/faces.sqlite3.

Each row stores a photo identity, its source-bytes SHA-256, the detector
signature that produced the boxes, the image dimensions, and the boxes as JSON.
Replacing a photo (even while preserving timestamps), moving it, upgrading the
model, or tweaking a threshold invalidates the entry instead of serving stale
boxes. An empty result is stored as a real row (``empty = 1``) so it is never
confused with a never-computed miss; failed reads are never cached and stay
retryable. Existing ``folders.json`` and ``catalog.json`` are only read, never
rewritten.
"""
import hashlib
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import face_detector

SCHEMA_VERSION = 1


class CacheError(RuntimeError):
    """Base class for face-cache failures."""


class SourceChangedError(CacheError):
    """The source photo changed while detection was running; nothing was cached."""


def sha256_file(path, chunk_size=1024 * 1024):
    digest = hashlib.sha256()
    with open(path, 'rb') as stream:
        for chunk in iter(lambda: stream.read(chunk_size), b''):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_path(path):
    """Resolve symlinks without requiring the path to exist (unlike Path.resolve)."""
    return os.path.realpath(os.fspath(path))


def photo_id(photo):
    """Stable cache identity for a catalog row or a legacy folder-album item.

    Catalog rows already carry their audit id and use it unchanged. Legacy
    folder-album rows only have a path, so they get a namespaced id derived from
    the canonical path; the ``path:`` prefix keeps the two namespaces apart.
    """
    catalog_id = photo.get('id')
    if catalog_id:
        return str(catalog_id)
    return 'path:' + hashlib.sha256(os.fsencode(canonical_path(photo['path']))).hexdigest()[:20]


class FaceCache:
    def __init__(self, data, model_dir=None, *,
                 score_threshold=face_detector.DEFAULT_SCORE_THRESHOLD,
                 nms_threshold=face_detector.DEFAULT_NMS_THRESHOLD,
                 top_k=face_detector.DEFAULT_TOP_K,
                 max_dim=face_detector.DEFAULT_MAX_DIM,
                 max_pixels=face_detector.DEFAULT_MAX_PIXELS):
        self.data = Path(data)
        self.data.mkdir(parents=True, exist_ok=True)
        self.path = self.data / 'faces.sqlite3'
        self.model_dir = Path(model_dir) if model_dir is not None else face_detector.MODEL_DIR
        self.score_threshold = score_threshold
        self.nms_threshold = nms_threshold
        self.top_k = top_k
        self.max_dim = max_dim
        self.max_pixels = max_pixels
        self.signature = face_detector.detector_signature(
            score_threshold, nms_threshold, top_k, max_dim, max_pixels)
        self._conn = sqlite3.connect(str(self.path), timeout=30.0)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute('PRAGMA journal_mode=WAL')
        self._conn.execute('PRAGMA busy_timeout=30000')
        self._migrate()

    def _migrate(self):
        self._conn.execute(
            'CREATE TABLE IF NOT EXISTS schema_meta ('
            'key TEXT PRIMARY KEY, value TEXT NOT NULL)')
        self._conn.execute(
            'CREATE TABLE IF NOT EXISTS faces ('
            'photo_id TEXT PRIMARY KEY,'
            'path TEXT NOT NULL,'
            'source_sha256 TEXT NOT NULL,'
            'signature TEXT NOT NULL,'
            'width INTEGER NOT NULL,'
            'height INTEGER NOT NULL,'
            'boxes TEXT NOT NULL,'
            'empty INTEGER NOT NULL,'
            'created_at TEXT NOT NULL,'
            'updated_at TEXT NOT NULL)')
        row = self._conn.execute(
            "SELECT value FROM schema_meta WHERE key = 'schema_version'").fetchone()
        version = int(row['value']) if row else 0
        if version > SCHEMA_VERSION:
            raise CacheError(
                f'face cache schema {version} is newer than this code supports '
                f'({SCHEMA_VERSION}); upgrade the application.')
        if version != SCHEMA_VERSION:
            with self._conn:
                self._conn.execute(
                    "INSERT INTO schema_meta(key, value) VALUES('schema_version', ?) "
                    'ON CONFLICT(key) DO UPDATE SET value = excluded.value',
                    (str(SCHEMA_VERSION),))
        self._conn.commit()

    def close(self):
        self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()

    def get(self, photo):
        """Return ``(found, result)``.

        ``found`` is False for a miss (compute needed) and True for a cached
        result. A cached empty result is ``(True, {'faces': []})`` and is never
        confused with a miss. A row whose source bytes, canonical path, or
        detector signature no longer match is treated as a miss, and a source
        that is unreadable right now is also a miss (so the failed read is
        retried) without dropping the stored row.
        """
        pid = photo_id(photo)
        canonical = canonical_path(photo['path'])
        row = self._conn.execute('SELECT * FROM faces WHERE photo_id = ?', (pid,)).fetchone()
        if row is None:
            return False, None
        if row['signature'] != self.signature or row['path'] != canonical:
            return False, None
        try:
            current = sha256_file(canonical)
        except OSError:
            return False, None
        if current != row['source_sha256']:
            return False, None
        return True, {'width': row['width'], 'height': row['height'],
                      'faces': json.loads(row['boxes'])}

    def put(self, photo, result, source_sha256):
        """Transactionally store a detection result.

        An empty ``result['faces']`` is stored as a real row with ``empty = 1``.
        If the transaction is interrupted, the previous row (if any) is kept.
        """
        pid = photo_id(photo)
        canonical = canonical_path(photo['path'])
        now = datetime.now(timezone.utc).isoformat()
        values = (pid, canonical, source_sha256, self.signature,
                  result['width'], result['height'],
                  json.dumps(result['faces'], ensure_ascii=False),
                  int(not result['faces']), now, now)
        with self._conn:
            self._upsert(values)
        return pid

    def _upsert(self, values):
        self._conn.execute(
            'INSERT INTO faces (photo_id, path, source_sha256, signature, width, height, '
            'boxes, empty, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) '
            'ON CONFLICT(photo_id) DO UPDATE SET path = excluded.path, '
            'source_sha256 = excluded.source_sha256, signature = excluded.signature, '
            'width = excluded.width, height = excluded.height, boxes = excluded.boxes, '
            'empty = excluded.empty, updated_at = excluded.updated_at',
            values)

    def compute(self, photo):
        """Detect faces and cache them, checking source stability first.

        The source is hashed before and after detection. A photo that changes
        during detection raises :class:`SourceChangedError` and is not cached; a
        photo that cannot be read raises its underlying ``OSError`` and is also
        not cached, so both remain retryable.
        """
        canonical = canonical_path(photo['path'])
        before = sha256_file(canonical)
        result = face_detector.detect(
            canonical, model_dir=self.model_dir, score_threshold=self.score_threshold,
            nms_threshold=self.nms_threshold, top_k=self.top_k,
            max_dim=self.max_dim, max_pixels=self.max_pixels)
        after = sha256_file(canonical)
        if after != before:
            raise SourceChangedError(
                f'source changed during face detection and was not cached: {canonical}')
        self.put(photo, result, after)
        return result

    def faces(self, photo):
        """Return ``(result, cached)``, computing and storing on a miss."""
        found, result = self.get(photo)
        if found:
            return result, True
        return self.compute(photo), False

    def row(self, photo):
        """Expose the raw stored row for a photo (diagnostics and tests)."""
        return self._conn.execute(
            'SELECT * FROM faces WHERE photo_id = ?', (photo_id(photo),)).fetchone()

    def count(self):
        return self._conn.execute('SELECT COUNT(*) FROM faces').fetchone()[0]


def load_photos(data):
    """Yield photo dicts from the existing catalog and folder albums.

    Reads ``catalog.json`` and ``folders.json`` without rewriting either. Catalog
    items keep their audit ids; folder items keep only their paths. Paths already
    seen in the catalog are not repeated from folder albums.
    """
    data = Path(data)
    seen = set()
    catalog = data / 'catalog.json'
    if catalog.exists():
        for item in json.loads(catalog.read_text()).get('items', []):
            if item.get('kind') == 'photo' and item.get('path'):
                seen.add(canonical_path(item['path']))
                yield {'id': item.get('id'), 'path': item['path']}
    folders = data / 'folders.json'
    if folders.exists():
        for album in json.loads(folders.read_text()).values():
            for item in album.get('items', []):
                if item.get('path') and canonical_path(item['path']) not in seen:
                    seen.add(canonical_path(item['path']))
                    yield {'path': item['path']}
