"""Pure framing geometry shared by background preparation and controls."""


def face_frame(image_size, viewport, faces, zoom_percent=40):
    """Fit the union of orientation-corrected normalized boxes inside the crop."""
    if not faces:
        return 1.0, (0.0, 0.0)
    low = [min(box[axis] for box in faces) for axis in ("x", "y")]
    high = [
        max(box[axis] + box[size] for box in faces)
        for axis, size in (("x", "width"), ("y", "height"))
    ]
    fit = min(v / s for v, s in zip(viewport, image_size))
    zoom = 1 + zoom_percent / 100
    for lo, hi, source, screen in zip(low, high, image_size, viewport):
        if hi > lo:
            zoom = min(zoom, screen / (source * fit * (hi - lo)))
    zoom = max(1.0, zoom)
    pan = []
    for lo, hi, source, screen in zip(low, high, image_size, viewport):
        crop = min(1.0, screen / (source * fit * zoom))
        left = max(0.0, min(1 - crop, (lo + hi - crop) / 2))
        pan.append(2 * left / (1 - crop) - 1 if crop < 1 else 0.0)
    return zoom, tuple(pan)


def fit_photo(image, width, height, zoom=1.0, pan=(0, 0)):
    """Resize only the visible crop, even at high zoom on large photos."""
    from PIL import Image

    scale = min(width / image.width, height / image.height) * zoom
    crop_w, crop_h = min(image.width, width / scale), min(image.height, height / scale)
    left = (image.width - crop_w) / 2 * (1 + max(-1, min(1, pan[0])))
    top = (image.height - crop_h) / 2 * (1 + max(-1, min(1, pan[1])))
    return image.resize(
        (
            max(1, min(width, round(crop_w * scale))),
            max(1, min(height, round(crop_h * scale))),
        ),
        Image.Resampling.LANCZOS,
        box=(left, top, left + crop_w, top + crop_h),
    )
