import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageOps

import face_detector

BASE = Path(__file__).resolve().parent
FIXTURES = BASE / 'tests' / 'fixtures' / 'faces'


def real_model_ready():
    """True only when the model is present, pinned, checksum-valid, and cv2 is usable."""
    if not face_detector.MODEL_SHA256 or not face_detector.model_path().is_file():
        return False
    try:
        face_detector._opencv()
        face_detector.verify_model()
    except face_detector.FaceDetectionError:
        return False
    return True


REAL_MODEL = real_model_ready()
HAS_FACE_FIXTURES = (FIXTURES / 'single.jpg').is_file() and (FIXTURES / 'group.jpg').is_file()


class DetectorUnitTests(unittest.TestCase):
    def test_model_checksum_mismatch_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / face_detector.MODEL_FILENAME
            path.write_bytes(b'not a real model')
            with self.assertRaises(face_detector.ModelIntegrityError):
                face_detector.verify_model(Path(tmp), expected_sha256='0' * 64)

    def test_missing_model_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(face_detector.ModelIntegrityError):
                face_detector.verify_model(Path(tmp), expected_sha256='0' * 64)

    def test_unpinned_model_raises_clear_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(face_detector.ModelNotPinnedError):
                face_detector.verify_model(Path(tmp), expected_sha256='')

    def test_bounded_mapping_preserves_aspect_and_maps_back(self):
        mapping = face_detector.bounded_mapping(4000, 2000, max_dim=1000)
        self.assertEqual((mapping['width'], mapping['height']), (1000, 500))
        self.assertEqual((mapping['offset_x'], mapping['offset_y']), (0.0, 0.0))
        box = face_detector.map_box(1000, 500, 0, 0, mapping, 4000, 2000)
        self.assertAlmostEqual(box['x'], 1.0, places=6)
        self.assertAlmostEqual(box['y'], 1.0, places=6)

    def test_bounded_mapping_obeys_pixel_cap(self):
        mapping = face_detector.bounded_mapping(3000, 3000, max_dim=3000, max_pixels=1_000_000)
        self.assertLessEqual(mapping['width'] * mapping['height'], 1_000_000)

    def test_resize_maps_offsets_accurately_without_cropping(self):
        image = Image.new('RGB', (400, 200), 'white')
        resized, mapping = face_detector.resize_bounded(image, max_dim=100)
        self.assertEqual(resized.size, (100, 50))
        self.assertEqual((mapping['offset_x'], mapping['offset_y']), (0.0, 0.0))
        box = face_detector.map_box(50, 25, 20, 10, mapping, 400, 200)
        self.assertAlmostEqual(box['x'], 0.5)
        self.assertAlmostEqual(box['y'], 0.5)
        self.assertAlmostEqual(box['width'], 0.2)
        self.assertAlmostEqual(box['height'], 0.2)

    def test_detector_signature_changes_with_thresholds_and_preprocessing(self):
        baseline = face_detector.detector_signature()
        self.assertNotEqual(baseline, face_detector.detector_signature(score_threshold=0.5))
        self.assertNotEqual(baseline, face_detector.detector_signature(max_dim=640))
        self.assertNotEqual(baseline, face_detector.detector_signature(nms_threshold=0.4))


class RealModelAcceptanceTests(unittest.TestCase):
    def assert_normalized(self, box):
        for key in ('x', 'y', 'width', 'height'):
            self.assertGreaterEqual(box[key], 0.0)
            self.assertLessEqual(box[key], 1.0)
        self.assertLessEqual(box['x'] + box['width'], 1.0 + 1e-9)
        self.assertLessEqual(box['y'] + box['height'], 1.0 + 1e-9)
        self.assertGreater(box['width'], 0.0)
        self.assertGreater(box['height'], 0.0)
        self.assertGreater(box['confidence'], 0.0)

    @unittest.skipUnless(REAL_MODEL and HAS_FACE_FIXTURES, 'model/fixtures not present')
    def test_single_face_fixture_detects_one_face(self):
        manifest = json.loads((FIXTURES / 'manifest.json').read_text())
        single = next(f for f in manifest['fixtures'] if f['file'] == 'single.jpg')
        result = face_detector.detect(str(FIXTURES / 'single.jpg'))
        self.assertGreaterEqual(len(result['faces']), single['expected']['min_faces'])
        for box in result['faces']:
            self.assert_normalized(box)

    @unittest.skipUnless(REAL_MODEL and HAS_FACE_FIXTURES, 'model/fixtures not present')
    def test_multiple_face_fixture_detects_multiple_faces(self):
        manifest = json.loads((FIXTURES / 'manifest.json').read_text())
        group = next(f for f in manifest['fixtures'] if f['file'] == 'group.jpg')
        result = face_detector.detect(str(FIXTURES / 'group.jpg'))
        self.assertGreaterEqual(len(result['faces']), group['expected']['min_faces'])
        confidences = [box['confidence'] for box in result['faces']]
        self.assertEqual(confidences, sorted(confidences, reverse=True))
        self.assertTrue(all(c >= face_detector.DEFAULT_SCORE_THRESHOLD for c in confidences))
        for box in result['faces']:
            self.assert_normalized(box)

    @unittest.skipUnless(REAL_MODEL, 'model not present')
    def test_no_face_image_returns_empty_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'blank.jpg'
            Image.new('RGB', (640, 480), 'gray').save(path)
            result = face_detector.detect(str(path))
            self.assertEqual(result['faces'], [])

    @unittest.skipUnless(REAL_MODEL and HAS_FACE_FIXTURES, 'model/fixtures not present')
    def test_exif_rotation_is_transposed_before_detection(self):
        with Image.open(FIXTURES / 'single.jpg') as im:
            upright = ImageOps.exif_transpose(im).convert('RGB')
        with tempfile.TemporaryDirectory() as tmp:
            rotated = Path(tmp) / 'rotated.jpg'
            exif = Image.Exif()
            exif[0x0112] = 6  # Orientation 6: display rotated 90 degrees clockwise
            upright.save(rotated, exif=exif)
            result = face_detector.detect(str(rotated))
        # The detector reports the orientation-corrected (transposed) dimensions.
        self.assertEqual((result['width'], result['height']),
                         (upright.height, upright.width))
        self.assertGreaterEqual(len(result['faces']), 1)
        for box in result['faces']:
            self.assert_normalized(box)

    @unittest.skipUnless(REAL_MODEL and HAS_FACE_FIXTURES, 'model/fixtures not present')
    def test_bounded_resize_boxes_overlap_full_resolution_detection(self):
        full = face_detector.detect(str(FIXTURES / 'single.jpg'),
                                    max_dim=10000, max_pixels=100_000_000)
        bounded = face_detector.detect(str(FIXTURES / 'single.jpg'), max_dim=640)
        self.assertGreaterEqual(len(full['faces']), 1)
        self.assertGreaterEqual(len(bounded['faces']), 1)
        full_box = max(full['faces'], key=lambda b: b['width'] * b['height'])
        bounded_box = max(bounded['faces'], key=lambda b: b['width'] * b['height'])

        def center(box):
            return box['x'] + box['width'] / 2, box['y'] + box['height'] / 2

        def intersection(a, b):
            x0 = max(a['x'], b['x']); y0 = max(a['y'], b['y'])
            x1 = min(a['x'] + a['width'], b['x'] + b['width'])
            y1 = min(a['y'] + a['height'], b['y'] + b['height'])
            return max(0.0, x1 - x0) * max(0.0, y1 - y0)

        def area(box):
            return box['width'] * box['height']

        union = area(full_box) + area(bounded_box) - intersection(full_box, bounded_box)
        self.assertGreater(union, 0)
        self.assertGreaterEqual(intersection(full_box, bounded_box) / union, 0.5)
        cx, cy = center(full_box)
        bcx, bcy = center(bounded_box)
        self.assertLess(abs(cx - bcx), 0.06)
        self.assertLess(abs(cy - bcy), 0.06)

    @unittest.skipUnless(REAL_MODEL, 'model not present')
    def test_model_checksum_failure_blocks_detection(self):
        model = face_detector.model_path()
        with tempfile.TemporaryDirectory() as tmp:
            tampered_dir = Path(tmp)
            tampered = tampered_dir / face_detector.MODEL_FILENAME
            tampered.write_bytes(model.read_bytes() + b'tampered')
            with self.assertRaises(face_detector.ModelIntegrityError):
                face_detector.detect(str(model), model_dir=tampered_dir)


if __name__ == '__main__':
    unittest.main()
