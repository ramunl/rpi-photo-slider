import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import face_cache
from folders import playlist

BASE = Path(__file__).resolve().parent


def a_face():
    return {'x': 0.1, 'y': 0.2, 'width': 0.3, 'height': 0.4, 'confidence': 0.9}


class FaceCacheTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.data = self.root / 'data'
        self.photo_path = self.root / 'photo.jpg'
        self.photo_path.write_bytes(b'first bytes')
        self.photo = {'path': str(self.photo_path)}

    def tearDown(self):
        self.tmp.cleanup()

    def source_sha(self):
        return face_cache.sha256_file(self.photo_path)

    def test_miss_empty_result_and_hit_are_distinct(self):
        with face_cache.FaceCache(self.data) as cache:
            self.assertEqual(cache.get(self.photo), (False, None))
            cache.put(self.photo, {'width': 10, 'height': 20, 'faces': []}, self.source_sha())
            found, result = cache.get(self.photo)
            self.assertTrue(found)
            self.assertEqual(result['faces'], [])
            self.assertEqual(cache.row(self.photo)['empty'], 1)
            cache.put(self.photo, {'width': 10, 'height': 20, 'faces': [a_face()]},
                      self.source_sha())
            found, result = cache.get(self.photo)
            self.assertTrue(found)
            self.assertEqual(len(result['faces']), 1)
            self.assertAlmostEqual(result['faces'][0]['x'], 0.1)
            self.assertEqual(cache.row(self.photo)['empty'], 0)

    def test_catalog_id_used_and_legacy_path_items_namespaced(self):
        self.assertEqual(face_cache.photo_id({'id': 'abc123', 'path': self.photo_path}),
                         'abc123')
        legacy = face_cache.photo_id({'path': str(self.photo_path)})
        self.assertTrue(legacy.startswith('path:'))
        link = self.root / 'link.jpg'
        link.symlink_to(self.photo_path)
        self.assertEqual(face_cache.photo_id({'path': str(link)}), legacy)

    def test_replacement_with_preserved_timestamps_invalidates(self):
        cache = face_cache.FaceCache(self.data)
        cache.put(self.photo, {'width': 5, 'height': 5, 'faces': []}, self.source_sha())
        stat = self.photo_path.stat()
        self.photo_path.write_bytes(b'other bytes')  # same length, different content
        os.utime(self.photo_path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        self.assertEqual(self.photo_path.stat().st_mtime_ns, stat.st_mtime_ns)
        found, _ = cache.get(self.photo)
        self.assertFalse(found)
        cache.close()

    def test_path_and_threshold_changes_invalidate(self):
        cache = face_cache.FaceCache(self.data)
        cache.put(self.photo, {'width': 5, 'height': 5, 'faces': []}, self.source_sha())
        first = {'id': 'shared-id', 'path': str(self.root / 'a.jpg')}
        second = {'id': 'shared-id', 'path': str(self.root / 'b.jpg')}
        Path(first['path']).write_bytes(b'x')
        Path(second['path']).write_bytes(b'x')
        cache.put(first, {'width': 4, 'height': 4, 'faces': []},
                  face_cache.sha256_file(first['path']))
        self.assertFalse(cache.get(second)[0])  # same id, different canonical path
        cache.close()
        other_threshold = face_cache.FaceCache(self.data, score_threshold=0.5)
        self.assertFalse(other_threshold.get(self.photo)[0])  # signature mismatch
        other_threshold.close()

    def test_failed_read_is_retryable_and_preserves_existing_entry(self):
        cache = face_cache.FaceCache(self.data)
        sha = self.source_sha()
        cache.put(self.photo, {'width': 5, 'height': 5, 'faces': [a_face()]}, sha)
        self.photo_path.unlink()
        found, _ = cache.get(self.photo)
        self.assertFalse(found)                    # unreadable => miss, not a hit
        self.assertIsNotNone(cache.row(self.photo))  # stored row preserved
        with self.assertRaises(OSError):
            cache.compute(self.photo)              # failed read never cached
        self.photo_path.write_bytes(b'first bytes')
        found, result = cache.get(self.photo)      # original entry still valid
        self.assertTrue(found)
        self.assertEqual(len(result['faces']), 1)
        cache.close()

    def test_interrupted_write_keeps_previous_entry(self):
        cache = face_cache.FaceCache(self.data)
        cache.put(self.photo, {'width': 5, 'height': 5, 'faces': []}, self.source_sha())
        with mock.patch.object(cache, '_upsert', side_effect=RuntimeError('interrupted')):
            with self.assertRaises(RuntimeError):
                cache.put(self.photo, {'width': 6, 'height': 6, 'faces': [a_face()]},
                          self.source_sha())
        found, result = cache.get(self.photo)
        self.assertTrue(found)
        self.assertEqual(result['width'], 5)
        self.assertEqual(result['faces'], [])
        cache.close()

    def test_cache_reuse_across_processes(self):
        cache = face_cache.FaceCache(self.data)
        cache.put(self.photo, {'width': 8, 'height': 8, 'faces': [a_face()]},
                  self.source_sha())
        cache.close()
        script = (
            'import sys\n'
            f'sys.path.insert(0, {str(BASE)!r})\n'
            'from face_cache import FaceCache\n'
            f'cache = FaceCache({str(self.data)!r})\n'
            f'found, result = cache.get({self.photo!r})\n'
            'assert found, "second process reported a miss"\n'
            'assert result["width"] == 8 and len(result["faces"]) == 1, result\n'
            'cache.close()\n'
        )
        subprocess.run([sys.executable, '-c', script], check=True, env=dict(os.environ))

    def test_schema_version_recorded_and_newer_schema_rejected(self):
        cache = face_cache.FaceCache(self.data)
        row = cache._conn.execute(
            "SELECT value FROM schema_meta WHERE key = 'schema_version'").fetchone()
        self.assertEqual(int(row['value']), face_cache.SCHEMA_VERSION)
        cache.close()
        conn = sqlite3.connect(cache.path)
        conn.execute("UPDATE schema_meta SET value = '99' WHERE key = 'schema_version'")
        conn.commit()
        conn.close()
        with self.assertRaises(face_cache.CacheError):
            face_cache.FaceCache(self.data)

    def test_load_photos_prefers_catalog_id_and_deduplicates_by_path(self):
        shared = self.root / 'shared.jpg'
        shared.write_bytes(b'x')
        (self.data / 'catalog.json').write_text(json.dumps({'items': [
            {'kind': 'photo', 'id': 'cat-1', 'path': str(shared)}]}))
        (self.data / 'folders.json').write_text(json.dumps({'alb': {
            'name': 'Album', 'path': str(self.root), 'items': [{'path': str(shared)}]}}))
        photos = list(face_cache.load_photos(self.data))
        self.assertEqual(len(photos), 1)
        self.assertEqual(photos[0]['id'], 'cat-1')

    def test_unchanged_legacy_album_still_loads(self):
        album_dir = self.root / 'album'
        album_dir.mkdir()
        image = album_dir / 'one.jpg'
        image.write_bytes(b'photo bytes')
        (self.data / 'folders.json').write_text(json.dumps({'alb': {
            'name': 'Album', 'path': str(album_dir), 'items': [{'path': str(image)}]}}))
        before = (self.data / 'folders.json').read_bytes()
        photos = list(face_cache.load_photos(self.data))
        self.assertEqual(len(photos), 1)
        self.assertEqual(photos[0]['path'], str(image))
        self.assertEqual((self.data / 'folders.json').read_bytes(), before)
        name, items = playlist(self.data, 'alb')
        self.assertEqual(name, 'Album')
        self.assertEqual(len(items), 1)


if __name__ == '__main__':
    unittest.main()
