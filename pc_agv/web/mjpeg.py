"""Streams MJPEG (RGB, profundidade e ZED) com quadro de espera quando a camera falta."""

import time
from typing import Optional

import cv2
import numpy as np

from pc_agv.config import (
    ZED_JPEG_QUALITY,
    ZED_STREAM_MAX_WIDTH,
)
from pc_agv import estado
from pc_agv import util
from pc_agv.visao import profundidade


def _placeholder_frame(text: str) -> np.ndarray:
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    cv2.rectangle(frame, (20, 20), (620, 460), (30, 30, 30), 2)
    cv2.putText(frame, text, (40, 240), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 200, 255), 2)
    return frame


def _encode_jpeg(frame: np.ndarray, quality: int = 82) -> Optional[bytes]:
    ok, buffer = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        return None
    return buffer.tobytes()


def mjpeg_generator(kind: str, fps: int):
    interval = 1.0 / max(1, fps)
    while True:
        quality = 82
        if kind == "rgb":
            with estado.state_lock:
                frame = None if estado.state["rgb"] is None else estado.state["rgb"].copy()
                error = estado.state["kinect_error"]
            if frame is None:
                frame = _placeholder_frame("Aguardando Kinect RGB")
                if error:
                    cv2.putText(frame, error[:44], (40, 280), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (120, 180, 255), 2)
        elif kind == "depth":
            with estado.state_lock:
                depth = None if estado.state["depth"] is None else estado.state["depth"].copy()
                is_mm = bool(estado.state["depth_mm"])
                error = estado.state["kinect_error"]
            if depth is None:
                frame = _placeholder_frame("Aguardando Kinect Depth")
                if error:
                    cv2.putText(frame, error[:44], (40, 280), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (120, 180, 255), 2)
            else:
                frame = profundidade.depth_to_colormap(depth, is_mm)
        else:
            with estado.state_lock:
                frame = None if estado.state["zed"] is None else estado.state["zed"].copy()
                error = estado.state["zed_error"]
            if frame is None:
                frame = _placeholder_frame("Aguardando webcam ZED")
                if error:
                    cv2.putText(frame, error[:44], (40, 280), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (120, 180, 255), 2)
            else:
                h, w = frame.shape[:2]
                if ZED_STREAM_MAX_WIDTH > 0 and w > ZED_STREAM_MAX_WIDTH:
                    target_w = int(ZED_STREAM_MAX_WIDTH)
                    target_h = max(2, int((h * target_w) / max(1, w)))
                    frame = cv2.resize(frame, (target_w, target_h), interpolation=cv2.INTER_AREA)
            quality = util.clamp(ZED_JPEG_QUALITY, 35, 95)

        data = _encode_jpeg(frame, quality=quality)
        if data is not None:
            yield (
                b"--frame\r\n"
                b"Content-Type: image/jpeg\r\nContent-Length: "
                + str(len(data)).encode("ascii")
                + b"\r\n\r\n"
                + data
                + b"\r\n"
            )
        time.sleep(interval)
