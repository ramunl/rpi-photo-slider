import unittest
from PIL import Image
from player import fit_photo, pan_after_drag


class ZoomTests(unittest.TestCase):
    def test_fit_and_zoom_out_preserve_proportions(self):
        image = Image.new('RGB', (400, 200))
        self.assertEqual(fit_photo(image, 200, 200).size, (200, 100))
        self.assertEqual(fit_photo(image, 200, 200, .5).size, (100, 50))

    def test_zoom_crops_center_instead_of_distorting_photo(self):
        image = Image.new('RGB', (400, 200), 'red')
        image.paste('blue', (100, 0, 300, 200))
        zoomed = fit_photo(image, 200, 200, 2)
        self.assertEqual(zoomed.size, (200, 200))
        self.assertEqual(zoomed.getpixel((100, 100)), (0, 0, 255))
        self.assertEqual(zoomed.getpixel((20, 100)), (0, 0, 255))

    def test_large_zoom_never_allocates_bigger_than_viewport(self):
        image = Image.new('RGB', (1000, 3000))
        zoomed = fit_photo(image, 1920, 1080, 4)
        self.assertLessEqual(zoomed.width, 1920)
        self.assertLessEqual(zoomed.height, 1080)

    def test_drag_reveals_edges_and_clamps_without_blank_space(self):
        image = Image.new('RGB', (400, 200), 'red')
        image.paste('blue', (300, 0, 400, 200))
        pan = pan_after_drag(image.size, (200, 200), 2, (0, 0), (-10000, 10000))
        self.assertEqual(pan, (1, 0))
        right = fit_photo(image, 200, 200, 2, pan)
        self.assertEqual(right.getpixel((180, 100)), (0, 0, 255))
        left = fit_photo(image, 200, 200, 2, (-1, 0))
        self.assertEqual(left.getpixel((180, 100)), (255, 0, 0))

    def test_pan_reverses_direction_and_ignores_uncropped_axes(self):
        self.assertEqual(pan_after_drag((400, 200), (200, 200), 1, (0, 0), (50, 50)), (0, 0))
        pan = pan_after_drag((400, 200), (200, 200), 4, (0, 0), (100, -50))
        self.assertLess(pan[0], 0)
        self.assertGreater(pan[1], 0)
        restored = pan_after_drag((400, 200), (200, 200), 4, pan, (-100, 50))
        self.assertEqual(restored, (0, 0))
