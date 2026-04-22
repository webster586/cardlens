from __future__ import annotations

import tempfile
from dataclasses import dataclass

import cv2
import numpy as np


@dataclass(slots=True)
class CameraState:
    is_running: bool = False
    device_index: int = 0


class CameraService:
    def __init__(self) -> None:
        self.state = CameraState()
        self._capture: cv2.VideoCapture | None = None

    @staticmethod
    def enumerate_cameras(max_index: int = 4) -> list[tuple[int, str]]:
        available = []
        for i in range(max_index):
            cap = cv2.VideoCapture(i, cv2.CAP_DSHOW)
            if cap.isOpened():
                available.append((i, f"Kamera {i}"))
                cap.release()
        return available

    def open(self, device_index: int = 0) -> bool:
        self.close()
        self._capture = cv2.VideoCapture(device_index, cv2.CAP_DSHOW)
        if not self._capture.isOpened():
            self._capture = None
            return False
        # Request 30 fps to reduce lag (camera may or may not honour it)
        self._capture.set(cv2.CAP_PROP_FPS, 30)
        # Drop the internal buffer to 1 frame so we always get the latest frame
        self._capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        self.state.is_running = True
        self.state.device_index = device_index
        return True

    def close(self) -> None:
        if self._capture is not None:
            self._capture.release()
            self._capture = None
        self.state.is_running = False

    def grab_frame(self) -> np.ndarray | None:
        if self._capture is None or not self._capture.isOpened():
            return None
        ret, frame = self._capture.read()
        if not ret:
            return None
        return frame

    def capture_to_tempfile(self) -> str | None:
        frame = self.grab_frame()
        if frame is None:
            return None
        tmp = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False, prefix="poke_scan_")
        path = tmp.name
        tmp.close()
        cv2.imwrite(path, frame)
        return path

    # Legacy compatibility
    def start(self, device_index: int = 0) -> None:
        self.open(device_index)

    def stop(self) -> None:
        self.close()
