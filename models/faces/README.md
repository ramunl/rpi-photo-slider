# YuNet face detection model

The face detector loads YuNet **2023mar** from the official
[OpenCV Zoo](https://github.com/opencv/opencv_zoo) repository.

- File: `face_detection_yunet_2023mar.onnx`
- Source: <https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx>
- Model card: <https://github.com/opencv/opencv_zoo/blob/main/models/face_detection_yunet/README.md>
- License: Apache License 2.0 — <https://github.com/opencv/opencv_zoo/blob/main/LICENSE>
- Minimum OpenCV: 4.5.4 (`cv2.FaceDetectorYN`)

The `.onnx` file itself is **not committed**; it is downloaded on the device and
verified against the pinned SHA-256 in `face_detector.MODEL_SHA256` on every
load. Download and pin it before enabling detection:

```sh
curl -L -o models/faces/face_detection_yunet_2023mar.onnx \
  https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx
python3 face_detector.py --pin models/faces/face_detection_yunet_2023mar.onnx
```

Copy the printed `MODEL_SHA256 = "..."` line into `face_detector.py`. Until that
line is filled in, detection raises `ModelNotPinnedError` rather than trusting an
unverified artifact.
