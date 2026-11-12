#!/usr/bin/env python3
"""Optional local face detection with OpenCV's YuNet model.

This module never imports OpenCV or NumPy at import time. The heavy runtime is
loaded lazily inside :func:`detect`, and a clear :class:`MissingDependencyError`
is raised when it is missing or too old. Detection runs on a bounded-resolution,
EXIF-transposed copy of each photo; returned boxes are normalized to the full
orientation-corrected image so callers can draw or zoom against the original.

The model artifact is pinned by source URL, license, and SHA-256. Until the
SHA-256 is recorded (see ``python3 face_detector.py --pin ...``) detection is
disabled and raises :class:`ModelNotPinnedError`.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path

BASE = Path(__file__).resolve().parent
MODEL_DIR = BASE / 'models' / 'faces'
MODEL_FILENAME = 'face_detection_yunet_2023mar.onnx'

# Immutable artifact pin (YuNet 2023mar from the official OpenCV Zoo).
MODEL_URL = ('https://github.com/opencv/opencv_zoo/raw/main/models/'
             'face_detection_yunet/' + MODEL_FILENAME)
MODEL_LICENSE = 'Apache-2.0'
MODEL_LICENSE_URL = 'https://github.com/opencv/opencv_zoo/blob/main/LICENSE'
# Pinned with: python3 face_detector.py --pin models/faces/<model>.onnx
MODEL_SHA256 = ''

# cv2.FaceDetectorYN was introduced in OpenCV 4.5.4.
MIN_OPENCV_VERSION = (4, 5, 4)

# The detection contract. Changing any of these values (or MODEL_SHA256 or
# DETECTOR_VERSION) changes detector_signature() and invalidates cached results.
DETECTOR_VERSION = 1
DEFAULT_SCORE_THRESHOLD = 0.6
DEFAULT_NMS_THRESHOLD = 0.3
DEFAULT_TOP_K = 5000
DEFAULT_MAX_DIM = 1600           # bound the long side before detection
DEFAULT_MAX_PIXELS = 2_000_000   # bound total decoded pixels before detection


class FaceDetectionError(RuntimeError):
    """Base class for all face-detection failures."""


class MissingDependencyError(FaceDetectionError):
    """OpenCV or NumPy is missing, or OpenCV is too old for YuNet."""


class ModelIntegrityError(FaceDetectionError):
    """The model file is missing or its bytes do not match the recorded hash."""


class ModelNotPinnedError(ModelIntegrityError):
    """The model SHA-256 has not been recorded yet; detection is disabled."""


def model_path(model_dir=MODEL_DIR):
    return Path(model_dir) / MODEL_FILENAME


def sha256_file(path, chunk_size=1024 * 1024):
    digest = hashlib.sha256()
    with open(path, 'rb') as stream:
        for chunk in iter(lambda: stream.read(chunk_size), b''):
            digest.update(chunk)
    return digest.hexdigest()


def verify_model(model_dir=MODEL_DIR, expected_sha256=None):
    """Return the model path or raise if the artifact is unpinned, missing, or
    fails its checksum. ``expected_sha256`` overrides the recorded pin for tests."""
    expected = MODEL_SHA256 if expected_sha256 is None else expected_sha256
    if not expected:
        raise ModelNotPinnedError(
            'The YuNet model SHA-256 is not recorded. Download the model from '
            f'{MODEL_URL} and run "python3 face_detector.py --pin <model.onnx>".')
    path = model_path(model_dir)
    if not path.is_file():
        raise ModelIntegrityError(
            f'YuNet model is missing at {path}. Download it from {MODEL_URL}.')
    actual = sha256_file(path)
    if actual != expected:
        raise ModelIntegrityError(
            f'YuNet model checksum mismatch for {path}: expected {expected}, got {actual}.')
    return path


def _version_tuple(version):
    parts = []
    for part in str(version).split('.')[:3]:
        digits = ''.join(ch for ch in part if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:3])


def _opencv():
    try:
        import cv2
    except ImportError as exc:
        raise MissingDependencyError(
            'OpenCV is required for face detection. Install "opencv-python-headless" '
            'from requirements-faces.txt, or on Raspberry Pi OS run '
            '"sudo apt-get install python3-opencv".') from exc
    if _version_tuple(cv2.__version__) < MIN_OPENCV_VERSION:
        raise MissingDependencyError(
            f'OpenCV {cv2.__version__} is too old for YuNet; '
            f'{MIN_OPENCV_VERSION[0]}.{MIN_OPENCV_VERSION[1]}.{MIN_OPENCV_VERSION[2]} or newer is required.')
    return cv2


def bounded_mapping(width, height, max_dim=DEFAULT_MAX_DIM, max_pixels=DEFAULT_MAX_PIXELS):
    """Choose a detection resolution that preserves aspect ratio and bounds work.

    The resize is proportional (no letterboxing), so offsets are zero. Per-axis
    scale factors are derived from the actual rounded output size so that box
    coordinates map back to the original image exactly.
    """
    scale = min(1.0, max_dim / max(1, width, height))
    scale = min(scale, math.sqrt(max_pixels / max(1, width * height)))
    new_width = max(1, round(width * scale))
    new_height = max(1, round(height * scale))
    return {'width': new_width, 'height': new_height,
            'scale_x': new_width / width, 'scale_y': new_height / height,
            'offset_x': 0.0, 'offset_y': 0.0}


def resize_bounded(image, max_dim=DEFAULT_MAX_DIM, max_pixels=DEFAULT_MAX_PIXELS):
    """Return ``(resized_image, mapping)`` where ``mapping`` describes how to map
    detection-space coordinates back to ``image`` coordinates."""
    from PIL import Image
    mapping = bounded_mapping(image.width, image.height, max_dim, max_pixels)
    if mapping['width'] == image.width and mapping['height'] == image.height:
        return image, mapping
    resized = image.resize((mapping['width'], mapping['height']), Image.Resampling.BILINEAR)
    return resized, mapping


def map_box(x, y, width, height, mapping, image_width, image_height):
    """Map a detection-space box to normalized full-image coordinates."""
    left = (x - mapping['offset_x']) / mapping['scale_x']
    top = (y - mapping['offset_y']) / mapping['scale_y']
    box_width = width / mapping['scale_x']
    box_height = height / mapping['scale_y']
    norm_x = max(0.0, min(1.0, left / image_width))
    norm_y = max(0.0, min(1.0, top / image_height))
    return {'x': norm_x,
            'y': norm_y,
            'width': min(box_width / image_width, 1.0 - norm_x),
            'height': min(box_height / image_height, 1.0 - norm_y)}


def detector_signature(score_threshold=DEFAULT_SCORE_THRESHOLD,
                       nms_threshold=DEFAULT_NMS_THRESHOLD,
                       top_k=DEFAULT_TOP_K,
                       max_dim=DEFAULT_MAX_DIM,
                       max_pixels=DEFAULT_MAX_PIXELS):
    """Stable string identifying the model, preprocessing, and thresholds that
    produced a set of boxes. Any change invalidates cached results."""
    return ('yunet-2023mar:v%d:model=%s:score=%s:nms=%s:topk=%d:maxdim=%d:maxpx=%d'
            % (DETECTOR_VERSION, MODEL_SHA256 or 'unpinned',
               score_threshold, nms_threshold, top_k, max_dim, max_pixels))


def detect(path, *, model_dir=MODEL_DIR,
           score_threshold=DEFAULT_SCORE_THRESHOLD,
           nms_threshold=DEFAULT_NMS_THRESHOLD,
           top_k=DEFAULT_TOP_K,
           max_dim=DEFAULT_MAX_DIM,
           max_pixels=DEFAULT_MAX_PIXELS):
    """Detect faces in one photo.

    Returns ``{'width': int, 'height': int, 'faces': [box, ...]}`` where
    ``width``/``height`` are the orientation-corrected full-image dimensions and
    each box has normalized ``x``, ``y`` (top-left), ``width``, ``height``, and
    ``confidence`` fields, sorted by confidence descending. Unreadable images
    raise their underlying ``OSError`` so callers can treat them as retryable.
    """
    verify_model(model_dir)
    cv2 = _opencv()
    from PIL import Image, ImageOps
    with Image.open(path) as source:
        image = ImageOps.exif_transpose(source).convert('RGB')
    full_width, full_height = image.size
    resized, mapping = resize_bounded(image, max_dim, max_pixels)
    detector = cv2.FaceDetectorYN_create(
        str(model_path(model_dir)), '', (mapping['width'], mapping['height']),
        score_threshold, nms_threshold, top_k)
    try:
        import numpy as np
    except ImportError as exc:
        raise MissingDependencyError(
            'NumPy is required for face detection; install requirements-faces.txt.') from exc
    bgr = cv2.cvtColor(np.asarray(resized), cv2.COLOR_RGB2BGR)
    detected = detector.detect(bgr)
    # Older builds returned only the face matrix; 4.5.4+ returns (count, faces).
    faces = detected[1] if isinstance(detected, tuple) and len(detected) == 2 else detected
    boxes = []
    if faces is not None:
        for face in faces:
            x, y, w, h = (float(face[0]), float(face[1]), float(face[2]), float(face[3]))
            confidence = float(face[-1])
            if confidence < score_threshold:
                continue
            box = map_box(x, y, w, h, mapping, full_width, full_height)
            box['confidence'] = confidence
            boxes.append(box)
    boxes.sort(key=lambda box: box['confidence'], reverse=True)
    return {'width': full_width, 'height': full_height, 'faces': boxes}


def main(argv=None):
    parser = argparse.ArgumentParser(description='Local YuNet face detection (optional).')
    parser.add_argument('--model-dir', type=Path, default=MODEL_DIR)
    parser.add_argument('--pin', metavar='MODEL_FILE',
                        help='print the SHA-256 line to record in face_detector.MODEL_SHA256')
    parser.add_argument('--verify', action='store_true',
                        help='verify the pinned model file and exit')
    parser.add_argument('--score-threshold', type=float, default=DEFAULT_SCORE_THRESHOLD)
    parser.add_argument('--max-dim', type=int, default=DEFAULT_MAX_DIM)
    parser.add_argument('path', nargs='?', help='photo to detect faces in')
    args = parser.parse_args(argv)
    if args.pin:
        print(f'MODEL_SHA256 = "{sha256_file(args.pin)}"')
        return 0
    if args.verify:
        print(verify_model(args.model_dir))
        return 0
    if not args.path:
        parser.error('a photo path is required (or use --pin / --verify)')
    result = detect(args.path, model_dir=args.model_dir,
                    score_threshold=args.score_threshold, max_dim=args.max_dim)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
