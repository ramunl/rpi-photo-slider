import copy
import hashlib
from pathlib import Path
import tempfile
import unittest
from PIL import Image
from audit import inspect, date_value, name_date, resolve_date, VERSION
from catalog import compile_catalog

class LibraryTests(unittest.TestCase):
    def record(self,id='one',**kw):
        r={'id':id,'path':'/source/'+id+'.jpg','kind':'photo','health':'ok','size':3000,'camera':'Camera','date':'2010-05-01T12:00:00','date_source':'exif_original','date_candidates':{},'review_reasons':[]}
        r.update(kw);return r

    def test_pixel_duplicate_keeps_one_and_retains_provenance(self):
        a=self.record('a',sha256='a',pixel_sha256='pixels')
        b=self.record('b',sha256='b',pixel_sha256='pixels')
        rows=compile_catalog([a,b],{})
        self.assertEqual(sorted(r['status'] for r in rows),['duplicate','included'])
        self.assertEqual(next(r for r in rows if r['status']=='included')['duplicate_paths'],['/source/b.jpg'])

    def test_different_photos_are_not_deduplicated_by_perceptual_hash(self):
        rows=compile_catalog([self.record('a',dhash='0000'),self.record('b',dhash='0000')],{})
        self.assertTrue(all(r['status']=='included' for r in rows))

    def test_conflicting_years_require_review(self):
        rows=compile_catalog([self.record('a',sha256='same'),self.record('b',sha256='same',date='2011-01-01T00:00:00')],{})
        self.assertEqual(next(r for r in rows if not r['duplicate_of'])['status'],'review')

    def test_year_override_cannot_include_broken_photo(self):
        r=self.record(health='broken',date=None,review_reasons=['unknown_date','no_camera_metadata'])
        result=compile_catalog([r],{'one':{'year':2010,'classification':'photo'}})[0]
        self.assertEqual(result['status'],'broken')

    def test_identical_copy_can_supply_missing_date(self):
        a=self.record('a',sha256='same',date=None,review_reasons=['unknown_date'])
        b=self.record('b',sha256='same')
        rows=compile_catalog([a,b],{})
        self.assertTrue(all(r['date'] for r in rows))

    def test_manual_content_decision_does_not_clear_date_uncertainty(self):
        r=self.record(date=None,review_reasons=['no_camera_metadata','unknown_date'])
        result=compile_catalog([r],{'one':{'classification':'photo'}})[0]
        self.assertEqual(result['status'],'review')
        self.assertEqual(result['review_reasons'],['unknown_date'])

    def test_capture_date_beats_export_date_and_detects_conflict(self):
        r={'path':'/IMG_20101201_120000.jpg','date_candidates':{'exif_original':'2010-12-01T12:00:00'}}
        resolve_date(r,{'date':'2020-01-01T00:00:00','date_src':'takeout-sidecar'})
        self.assertEqual(r['date'][:4],'2010');self.assertTrue(r['date_conflict'])

    def test_modification_date_and_year_range_are_not_capture_dates(self):
        r={'path':'/photos 2008 - 2014/file.jpg','date_candidates':{'exif_modified':'2026-09-01T00:00:00'}}
        resolve_date(r,{'date':'2008-01-01T00:00:00','date_src':'foldername'})
        self.assertIsNone(r['date'])

    def test_calendar_dates(self):
        self.assertIsNone(date_value('2010:02:30 00:00:00'))
        self.assertIsNone(name_date('File 20101200.jpg'))
        self.assertEqual(name_date('IMG_20101231_235959.jpg')[:10],'2010-12-31')

    def test_full_decoding_detects_truncated_jpeg_and_preserves_original(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp);image=folder/'IMG_20100101.jpg';thumbs=folder/'thumbs';thumbs.mkdir()
            Image.new('RGB',(1000,1000),'blue').save(image)
            complete=image.read_bytes();image.write_bytes(complete[:len(complete)//2])
            before=image.read_bytes();s=image.stat()
            r=inspect((str(image),{},[VERSION,s.st_ino,s.st_size,s.st_mtime_ns]),thumbs)
            self.assertEqual(r['health'],'broken')
            self.assertEqual(image.read_bytes(),before)

if __name__=='__main__':unittest.main()
