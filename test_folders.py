import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image
from folders import add_folder, albums, choose_folder, playlist, summaries


class FolderTests(unittest.TestCase):
    def test_nested_photos_persist_and_readding_refreshes_without_duplicates(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data = root / 'data'; data.mkdir()
            folder = root / 'Family photos'; folder.mkdir()
            nested = folder / 'Trip'; nested.mkdir()
            Image.new('RGB', (8, 8), 'red').save(folder / 'one.JPG')
            Image.new('RGB', (8, 8), 'blue').save(nested / 'two.png')
            (folder / 'bad.jpg').write_text('not a photo')
            (folder / 'notes.txt').write_text('ignore')
            before = (folder / 'one.JPG').read_bytes()
            first = add_folder(data, folder)
            self.assertEqual((first['count'], first['skipped']), (2, 1))
            self.assertEqual(playlist(data, first['id'])[0], 'Family photos')
            self.assertEqual(len(playlist(data, first['id'])[1]), 2)
            Image.new('RGB', (8, 8)).save(folder / 'new.jpg')
            second = add_folder(data, folder)
            self.assertEqual(first['id'], second['id'])
            self.assertEqual(len(albums(data)), 1)
            self.assertEqual(summaries(data)[0]['count'], 3)
            self.assertEqual((folder / 'one.JPG').read_bytes(), before)

    def test_empty_folder_does_not_change_saved_albums(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            with self.assertRaisesRegex(ValueError, 'No readable photos'):
                add_folder(data, data)
            self.assertFalse((data / 'folders.json').exists())

    def test_missing_album_and_disconnected_drive(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            with self.assertRaisesRegex(ValueError, 'not found'):
                playlist(data, 'missing')
            (data / 'folders.json').write_text(json.dumps({'saved': {
                'name': 'Photos', 'path': str(data / 'unmounted'), 'items': []}}))
            with self.assertRaisesRegex(ValueError, 'unavailable'):
                playlist(data, 'saved')

    @patch('folders.subprocess.run')
    def test_native_chooser_cancel_and_spaces(self, run):
        run.return_value.returncode = 1
        self.assertIsNone(choose_folder())
        run.return_value.returncode = 0
        run.return_value.stdout = '/mnt/Photos from summer\n'
        self.assertEqual(choose_folder(), '/mnt/Photos from summer')


if __name__ == '__main__':
    unittest.main()
