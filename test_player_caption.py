"""Photo captions distinguish detection results from skipped or failed work."""

import unittest

from player import _photo_caption


class CaptionTests(unittest.TestCase):
    def test_count_is_immediately_after_photo_index(self) -> None:
        state = {
            "year": "2010",
            "index": 2,
            "count": 10,
            "paused": True,
            "face_status": "detected",
            "face_count": 3,
        }
        self.assertEqual(
            _photo_caption(state, 1.4),
            "2010   3 / 10   Faces: 3   Paused   Zoom 140%",
        )

    def test_unknown_counts_are_not_reported_as_zero(self) -> None:
        for status in ("off", "manual", "unavailable"):
            with self.subTest(status=status):
                state = {
                    "year": "2010",
                    "index": 0,
                    "count": 1,
                    "paused": False,
                    "face_status": status,
                    "face_count": None,
                }
                self.assertEqual(
                    _photo_caption(state, 1), f"2010   1 / 1   Faces: {status}"
                )
