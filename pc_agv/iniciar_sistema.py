#!/usr/bin/env python3
"""
Servidor web do agv.

Escopo atual:
- stream RGB do Kinect v1
- stream do mapa de profundidade
- telemetria basica
- controle manual por site (W/A/S/D e toque)
- modo autonomo simples por profundidade
- envio serial para o Arduino
"""

import atexit
import select
import glob
import logging
import math
import os
import pickle
import socket
import subprocess
import sys
import threading
import time
from typing import Optional

import cv2
import face_recognition
import numpy as np
from flask import Flask, Response, jsonify, request
from werkzeug.serving import make_server
from werkzeug.utils import secure_filename

try:
    import freenect  # type: ignore[import-not-found]
except Exception:
    freenect = None

_face_known_encodings = []
_face_known_names = []
_face_known_image_paths = []
_face_selected_name = ""


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stdout,
)
log = logging.getLogger("agv")
logging.getLogger("werkzeug").setLevel(logging.WARNING)

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_HOST = os.environ.get("AGV_HOST", "0.0.0.0").strip() or "0.0.0.0"
DEFAULT_PORT = int(os.environ.get("AGV_PORT", "5000"))
CONTROL_LOOP_SECONDS = float(os.environ.get("AGV_CONTROL_LOOP_SECONDS", "0.12"))
AUTO_LOOP_SECONDS = float(os.environ.get("AGV_AUTO_LOOP_SECONDS", "0.18"))
CAPTURE_LOOP_SECONDS = float(os.environ.get("AGV_CAPTURE_LOOP_SECONDS", "0.06"))
# Watchdog: para o agv se nenhum comando manual chegar neste intervalo (0 = desativado)
WATCHDOG_TIMEOUT_S = float(os.environ.get("AGV_WATCHDOG_TIMEOUT_S", "2.0"))
# FPS dos streams MJPEG — reduzido para funcionar bem com sinal fraco.
VIDEO_FPS = int(os.environ.get("AGV_VIDEO_FPS", "6"))
DEPTH_FPS = int(os.environ.get("AGV_DEPTH_FPS", "4"))
ZED_FPS = int(os.environ.get("AGV_ZED_FPS", "60"))
ZED_WIDTH = int(os.environ.get("AGV_ZED_WIDTH", "640"))
ZED_HEIGHT = int(os.environ.get("AGV_ZED_HEIGHT", "360"))
ZED_STREAM_MAX_WIDTH = int(os.environ.get("AGV_ZED_STREAM_MAX_WIDTH", "960"))
ZED_JPEG_QUALITY = int(os.environ.get("AGV_ZED_JPEG_QUALITY", "60"))
ZED_DEVICE = os.environ.get("AGV_ZED_DEVICE", "/dev/video0").strip() or "/dev/video0"
ZED_NAME_HINT = os.environ.get("AGV_ZED_NAME_HINT", "zed").strip().lower() or "zed"
ZED_STRICT_DEVICE = os.environ.get("AGV_ZED_STRICT_DEVICE", "1").strip() != "0"
KINECT_RELEASE_UVCVIDEO = os.environ.get("AGV_KINECT_RELEASE_UVCVIDEO", "0").strip() == "1"
MIN_FORWARD_PWM = int(os.environ.get("AGV_MIN_FORWARD_PWM", "170"))
MIN_TURN_PWM = int(os.environ.get("AGV_MIN_TURN_PWM", "140"))
KINECT_STABILIZATION_ENABLED = os.environ.get("AGV_KINECT_STABILIZATION", "1").strip() == "1"
KINECT_TILT_MIN = float(os.environ.get("AGV_KINECT_TILT_MIN", "-18"))
KINECT_TILT_MAX = float(os.environ.get("AGV_KINECT_TILT_MAX", "18"))
KINECT_TILT_INTERVAL = float(os.environ.get("AGV_KINECT_TILT_INTERVAL", "0.5"))
KINECT_TILT_DEADBAND = float(os.environ.get("AGV_KINECT_TILT_DEADBAND", "0.6"))
KINECT_TILT_NEUTRAL = float(os.environ.get("AGV_KINECT_TILT_NEUTRAL", "0"))
KINECT_TILT_GAIN = float(os.environ.get("AGV_KINECT_TILT_GAIN", "1.20"))
KINECT_TILT_SMOOTHING = float(os.environ.get("AGV_KINECT_TILT_SMOOTHING", "0.45"))
KINECT_TILT_MANUAL_HOLD = float(os.environ.get("AGV_KINECT_TILT_MANUAL_HOLD", "2.5"))
KINECT_TILT_PITCH_DEADBAND = float(os.environ.get("AGV_KINECT_TILT_PITCH_DEADBAND", "0.8"))
KINECT_TILT_MAX_STEP = float(os.environ.get("AGV_KINECT_TILT_MAX_STEP", "2.5"))
FACE_SCAN_INTERVAL = float(os.environ.get("AGV_FACE_SCAN_INTERVAL", "0.7"))
FACE_MATCH_THRESHOLD = float(os.environ.get("AGV_FACE_MATCH_THRESHOLD", "0.665"))
FACE_IMPOSTOR_MARGIN = float(os.environ.get("AGV_FACE_IMPOSTOR_MARGIN", "0.05"))
FACE_SELECTED_MAX_DISTANCE = float(os.environ.get("AGV_FACE_SELECTED_MAX_DISTANCE", "0.52"))
FACE_DETECT_MODEL = os.environ.get("AGV_FACE_DETECT_MODEL", "hog").strip().lower() or "hog"
FACE_SCAN_MAX_WIDTH = int(os.environ.get("AGV_FACE_SCAN_MAX_WIDTH", "640"))
FACE_DB_DIR = os.path.join(PROJECT_ROOT, "logs")
FACE_UPLOADS_DIR = os.path.join(FACE_DB_DIR, "faces")
ENCODINGS_FILE = os.path.join(FACE_DB_DIR, "encodings.pkl")

app = Flask(__name__)

_runtime_lock = threading.Lock()
_runtime_started = False
_stop_event = threading.Event()
_threads = []

_state_lock = threading.Lock()
_state = {
    "rgb": None,
  "rgb_raw": None,
        "zed": None,
    "zed_device": ZED_DEVICE,
    "depth": None,
    "depth_mm": False,
    "distance_m": None,
    "left_clearance_m": None,
    "center_clearance_m": None,
    "right_clearance_m": None,
  "tilt_deg": None,
  "tilt_target_deg": 0.0,
  "accel": [0.0, 0.0, 0.0],
    "fps_rgb": 0.0,
    "fps_depth": 0.0,
    "fps_zed": 0.0,
    "kinect_ok": False,
    "kinect_error": None,
    "last_frame_at": 0.0,
    "zed_ok": False,
    "zed_error": None,
    "zed_last_frame_at": 0.0,
}

_control_lock = threading.Lock()
_control_state = {
    "mode": "manual",
    "manual_speed": 0,
    "manual_steering": 0,
    "active_speed": 0,
    "active_steering": 0,
    "updated_at": time.time(),
    "last_source": "boot",
    "arduino_sent": False,
    "arduino_error": None,
}

_autopilot_lock = threading.Lock()
_autopilot_state = {
    "enabled": True,
    "running": False,
    "target_speed": 0,
    "target_steering": 0,
    "last_reason": "idle",
    "last_tick": 0.0,
}

_arduino_lock = threading.Lock()
_arduino_serial = None
_arduino_state = {
    "enabled": os.environ.get("AGV_ARDUINO_ENABLED", "1").strip() == "1",
    "connected": False,
    "port": None,
    "baud": int(os.environ.get("AGV_ARDUINO_BAUD", "9600")),
    "protocol": str(os.environ.get("AGV_ARDUINO_PROTOCOL", "csv")).strip().lower() or "csv",
    "last_command": None,
    "last_error": None,
    "last_tx_at": 0.0,
}

_arduino_log_state = {
    "payload": None,
    "mode": None,
    "source": None,
    "at": 0.0,
}

_arduino_rx_state = {
    "last_line": None,
    "at": 0.0,
}

_tilt_last_set_at = 0.0
_tilt_last_target = 0.0
_tilt_manual_until = 0.0
_tilt_manual_target = 0.0
_tilt_pitch_filtered = 0.0

_tilt_control_lock = threading.Lock()

_settings_lock = threading.Lock()
_settings = {
    "speed_limit_pct": int(os.environ.get("AGV_SPEED_LIMIT_PCT", "100")),
}

_face_lock = threading.Lock()
_face_cascade = cv2.CascadeClassifier(
  os.path.join(cv2.data.haarcascades, "haarcascade_frontalface_default.xml")
)
_face_runtime = {
  "known": False,
  "label": "",
  "distance": None,
  "last_seen_at": 0.0,
  "last_announce_at": 0.0,
  "bbox": None,
  "next_scan_at": 0.0,
  "template_loaded": False,
    "selected_name": "",
}

_zed_face_lock = threading.Lock()
_zed_face_event = threading.Event()
_zed_face_state = {
    "pending": False,
    "frame": None,
    "next_scan_at": 0.0,
    "known": False,
    "label": "",
    "distance": None,
    "bbox": None,
}


class AGVServerController:
    def __init__(self, host: str, port: int):
        self.host = host
        self.port = port
        self._server = make_server(host, port, app, threaded=True)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True, name="agv-http")

    def start(self) -> None:
        start_runtime()
        self._thread.start()
        log.info("Servidor HTTP em http://%s:%s", self.host, self.port)

    def stop(self) -> None:
        try:
            self._server.shutdown()
        finally:
            stop_runtime()


def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, int(value)))


def _now() -> float:
    return time.time()


def _detect_lan_ip() -> str:
    # Resolve o IP local mais util para outro dispositivo na mesma rede.
    candidates = []

    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            probe.connect(("8.8.8.8", 80))
            candidates.append(probe.getsockname()[0])
        finally:
            probe.close()
    except Exception:
        pass

    try:
        candidates.extend(socket.gethostbyname_ex(socket.gethostname())[2])
    except Exception:
        pass

    for value in candidates:
        if value and not value.startswith("127."):
            return value
    return "127.0.0.1"


def _release_kinect_usb_claims() -> None:
    """Libera drivers do kernel que costumam prender o Kinect no Linux."""
    base_modules = ["gspca_kinect", "gspca_main", "snd_usb_audio"]
    if KINECT_RELEASE_UVCVIDEO:
        base_modules.extend(["uvcvideo", "videobuf2_v4l2", "videobuf2_vmalloc", "videobuf2_common", "videodev", "mc"])

    commands = [
        ["sudo", "-n", "modprobe", "-r", *base_modules],
        ["sudo", "-n", "rmmod", "gspca_kinect"],
        ["sudo", "-n", "rmmod", "gspca_main"],
        ["sudo", "-n", "rmmod", "snd_usb_audio"],
    ]
    if KINECT_RELEASE_UVCVIDEO:
        commands.append(["sudo", "-n", "rmmod", "uvcvideo"])
    for cmd in commands:
        try:
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        except Exception:
            continue


def _face_preprocess(gray_face: np.ndarray) -> np.ndarray:
    normalized = cv2.equalizeHist(gray_face)
    resized = cv2.resize(normalized, (64, 64), interpolation=cv2.INTER_AREA)
    descriptor = resized.astype(np.float32) / 255.0
    descriptor = descriptor.flatten()
    norm = np.linalg.norm(descriptor)
    if norm > 0:
        descriptor = descriptor / norm
    return descriptor


def _face_extract_primary(gray_frame: np.ndarray):
    if _face_cascade.empty():
        return None, None
    faces = _face_cascade.detectMultiScale(
        gray_frame,
        scaleFactor=1.2,
        minNeighbors=5,
        minSize=(60, 60),
    )
    if len(faces) == 0:
        return None, None
    x, y, w, h = max(faces, key=lambda f: int(f[2]) * int(f[3]))
    crop = gray_frame[y : y + h, x : x + w]
    if crop.size == 0:
        return None, None
    return (int(x), int(y), int(w), int(h)), _face_preprocess(crop)


def _load_kauan_face_descriptor() -> None:
    global _face_known_encodings, _face_known_names
def _normalize_face_name(name: str) -> str:
    normalized = " ".join(str(name or "").strip().split())
    return normalized[:60]


def _face_name_slug(name: str) -> str:
    slug = secure_filename(name).strip("._").lower()
    return slug or "rosto"


def _load_face_database() -> tuple[list[np.ndarray], list[str], list[Optional[str]]]:
    if not os.path.exists(ENCODINGS_FILE):
        raise FileNotFoundError(ENCODINGS_FILE)

    with open(ENCODINGS_FILE, "rb") as f:
        payload = pickle.load(f)

    if not isinstance(payload, tuple):
        raise ValueError("Formato invalido do encodings.pkl")

    if len(payload) == 2:
        known_encodings, known_names = payload
        known_image_paths = [None] * len(known_names)
    elif len(payload) == 3:
        known_encodings, known_names, known_image_paths = payload
    else:
        raise ValueError("Formato invalido do encodings.pkl")

    if not isinstance(known_encodings, list) or not isinstance(known_names, list) or not isinstance(known_image_paths, list):
        raise ValueError("Formato invalido do encodings.pkl")
    if len(known_encodings) != len(known_names) or len(known_names) != len(known_image_paths):
        raise ValueError("Banco de rostos inconsistente")

    return (
        [np.array(enc, dtype=np.float32) for enc in known_encodings],
        [_normalize_face_name(name) for name in known_names],
        [path if path else None for path in known_image_paths],
    )


def _save_face_database(encodings: list[np.ndarray], names: list[str], image_paths: list[Optional[str]]) -> None:
    os.makedirs(FACE_DB_DIR, exist_ok=True)
    with open(ENCODINGS_FILE, "wb") as f:
        pickle.dump((encodings, names, image_paths), f)


def _load_known_faces() -> None:
    global _face_known_encodings, _face_known_names, _face_known_image_paths, _face_selected_name

    try:
        known_encodings, known_names, known_image_paths = _load_face_database()
        _face_known_encodings = known_encodings
        _face_known_names = known_names
        _face_known_image_paths = known_image_paths

        with _face_lock:
            if _face_selected_name and _face_selected_name not in _face_known_names:
                _face_selected_name = ""
            _face_runtime["template_loaded"] = bool(_face_known_names)
            _face_runtime["selected_name"] = _face_selected_name

        log.info("Encodings carregados com sucesso (%d faces)", len(_face_known_encodings))
    except FileNotFoundError:
        _face_known_encodings = []
        _face_known_names = []
        _face_known_image_paths = []
        with _face_lock:
            _face_runtime["template_loaded"] = False
            _face_runtime["selected_name"] = ""
        log.warning("Arquivo de encodings nao encontrado: %s", ENCODINGS_FILE)
    except Exception as exc:
        _face_known_encodings = []
        _face_known_names = []
        _face_known_image_paths = []
        with _face_lock:
            _face_runtime["template_loaded"] = False
            _face_runtime["selected_name"] = ""
        log.warning("Falha ao carregar encodings: %s", exc)


def _set_selected_face(name: str) -> tuple[bool, str]:
    global _face_selected_name

    selected_name = _normalize_face_name(name)
    with _face_lock:
        if not selected_name:
            _face_selected_name = ""
            _face_runtime["known"] = False
            _face_runtime["label"] = ""
            _face_runtime["distance"] = None
            _face_runtime["bbox"] = None
            _face_runtime["next_scan_at"] = 0.0
            _face_runtime["selected_name"] = ""
            return True, "nenhum rosto selecionado"

        if selected_name not in _face_known_names:
            return False, "rosto nao encontrado"

        _face_selected_name = selected_name
        _face_runtime["known"] = False
        _face_runtime["label"] = ""
        _face_runtime["distance"] = None
        _face_runtime["bbox"] = None
        _face_runtime["next_scan_at"] = 0.0
        _face_runtime["selected_name"] = selected_name
        return True, "rosto selecionado"


def _selected_face_encoding() -> tuple[str, Optional[np.ndarray]]:
    with _face_lock:
        selected_name = str(_face_selected_name)
        known_names = list(_face_known_names)
        known_encodings = list(_face_known_encodings)

    if not selected_name:
        return "", None

    try:
        index = known_names.index(selected_name)
    except ValueError:
        return selected_name, None

    return selected_name, np.array(known_encodings[index], dtype=np.float32)


def _face_status_snapshot() -> dict:
    with _face_lock:
        return {
            "known": bool(_face_runtime["known"]),
            "label": str(_face_runtime["label"]),
            "distance": _face_runtime["distance"],
            "last_seen_at": float(_face_runtime["last_seen_at"]),
            "template_loaded": bool(_face_runtime["template_loaded"]),
            "selected_name": str(_face_runtime["selected_name"]),
            "names": list(_face_known_names),
        }


def _update_face_runtime(frame_bgr: np.ndarray) -> np.ndarray:
    now = _now()
    selected_name, selected_encoding = _selected_face_encoding()

    with _face_lock:
        cached_bbox = None
        known = False
        label = ""
        if (
            selected_name
            and selected_encoding is not None
            and now < float(_face_runtime["next_scan_at"])
            and str(_face_runtime["selected_name"]) == selected_name
        ):
            cached_bbox = _face_runtime["bbox"]
            known = bool(_face_runtime["known"])
            label = str(_face_runtime["label"])

    if not selected_name or selected_encoding is None:
        with _face_lock:
            _face_runtime["known"] = False
            _face_runtime["label"] = ""
            _face_runtime["distance"] = None
            _face_runtime["bbox"] = None
            _face_runtime["selected_name"] = selected_name
            _face_runtime["template_loaded"] = bool(_face_known_names)
        return frame_bgr

    if cached_bbox is None:
        known = False
        label = ""
        distance = None
        bbox = None

        try:
            # ZED e camera estereo: usa metade esquerda (672x376) em vez de
            # reduzir 0.5x o frame completo (resultaria em 672x188, muito achatado).
            h, w = frame_bgr.shape[:2]
            scan_frame = frame_bgr[:, : w // 2] if w > 700 else frame_bgr

            scan_h, scan_w = scan_frame.shape[:2]
            if scan_w > max(64, FACE_SCAN_MAX_WIDTH):
                scale = float(FACE_SCAN_MAX_WIDTH) / float(scan_w)
                resized_w = int(scan_w * scale)
                resized_h = int(scan_h * scale)
                frame_for_face = cv2.resize(scan_frame, (resized_w, resized_h), interpolation=cv2.INTER_AREA)
                scale_x = float(scan_w) / float(resized_w)
                scale_y = float(scan_h) / float(resized_h)
            else:
                frame_for_face = scan_frame
                scale_x = 1.0
                scale_y = 1.0

            rgb_small = cv2.cvtColor(frame_for_face, cv2.COLOR_BGR2RGB)
            model = "cnn" if FACE_DETECT_MODEL == "cnn" else "hog"
            face_locations = face_recognition.face_locations(rgb_small, model=model)
            face_encodings = face_recognition.face_encodings(rgb_small, face_locations)

            best_distance = None
            best_bbox = None
            for (top, right, bottom, left), encoding in zip(face_locations, face_encodings):
                current_distance = float(np.linalg.norm(np.array(encoding, dtype=np.float32) - selected_encoding))
                if best_distance is None or current_distance < best_distance:
                    best_distance = current_distance
                    left_px = int(left * scale_x)
                    top_px = int(top * scale_y)
                    right_px = int(right * scale_x)
                    bottom_px = int(bottom * scale_y)
                    best_bbox = (
                        left_px,
                        top_px,
                        max(1, right_px - left_px),
                        max(1, bottom_px - top_px),
                    )

            if best_distance is not None:
                distance = best_distance
                if best_distance <= FACE_MATCH_THRESHOLD:
                    known = True
                    label = selected_name
                    bbox = best_bbox
        except Exception as exc:
            log.warning("Falha na deteccao facial da ZED: %s", exc)

        with _face_lock:
            _face_runtime["known"] = known
            _face_runtime["label"] = label
            _face_runtime["distance"] = distance
            _face_runtime["last_seen_at"] = now if bbox is not None else _face_runtime["last_seen_at"]
            _face_runtime["bbox"] = bbox if known else None
            _face_runtime["next_scan_at"] = now + FACE_SCAN_INTERVAL
            _face_runtime["template_loaded"] = bool(_face_known_names)
            _face_runtime["selected_name"] = selected_name
            if known and now - float(_face_runtime["last_announce_at"]) >= 4.0:
                log.info("Face reconhecida na ZED: %s", selected_name)
                _face_runtime["last_announce_at"] = now
            cached_bbox = _face_runtime["bbox"]
            known = bool(_face_runtime["known"])
            label = str(_face_runtime["label"])

    if known and cached_bbox is not None:
        x, y, w, h = cached_bbox
        cv2.rectangle(frame_bgr, (x, y), (x + w, y + h), (80, 230, 120), 2)
        cv2.putText(
            frame_bgr,
            label,
            (x, max(18, y - 8)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.65,
            (80, 230, 120),
            2,
        )
    return frame_bgr


def _detect_selected_face_on_frame(
    frame_bgr: np.ndarray,
    selected_name: str,
    known_names: list[str],
    known_encodings: list[np.ndarray],
) -> tuple[bool, str, Optional[float], Optional[tuple[int, int, int, int]]]:
    known = False
    label = ""
    distance = None
    bbox = None

    h, w = frame_bgr.shape[:2]
    scan_frame = frame_bgr[:, : w // 2] if w > 700 else frame_bgr

    scan_h, scan_w = scan_frame.shape[:2]
    if scan_w > max(64, FACE_SCAN_MAX_WIDTH):
        scale = float(FACE_SCAN_MAX_WIDTH) / float(scan_w)
        resized_w = int(scan_w * scale)
        resized_h = int(scan_h * scale)
        frame_for_face = cv2.resize(scan_frame, (resized_w, resized_h), interpolation=cv2.INTER_AREA)
        scale_x = float(scan_w) / float(resized_w)
        scale_y = float(scan_h) / float(resized_h)
    else:
        frame_for_face = scan_frame
        scale_x = 1.0
        scale_y = 1.0

    rgb_small = cv2.cvtColor(frame_for_face, cv2.COLOR_BGR2RGB)
    model = "cnn" if FACE_DETECT_MODEL == "cnn" else "hog"
    face_locations = face_recognition.face_locations(rgb_small, model=model)
    face_encodings = face_recognition.face_encodings(rgb_small, face_locations)

    try:
        selected_idx = known_names.index(selected_name)
    except ValueError:
        return False, "", None, None

    strict_threshold = min(FACE_MATCH_THRESHOLD, FACE_SELECTED_MAX_DISTANCE)

    best_selected_distance = None
    best_bbox = None

    for (top, right, bottom, left), encoding in zip(face_locations, face_encodings):
        probe = np.array(encoding, dtype=np.float32)
        all_distances = [float(np.linalg.norm(probe - np.array(ref, dtype=np.float32))) for ref in known_encodings]
        if not all_distances:
            continue

        selected_distance = all_distances[selected_idx]
        others = [dist for idx, dist in enumerate(all_distances) if idx != selected_idx]
        best_other_distance = min(others) if others else 999.0
        best_distance = min(all_distances)

        is_selected_best = selected_distance <= (best_distance + 1e-9)
        has_margin = (best_other_distance - selected_distance) >= FACE_IMPOSTOR_MARGIN
        is_valid = (
            selected_distance <= strict_threshold
            and is_selected_best
            and has_margin
        )

        if not is_valid:
            continue

        if best_selected_distance is None or selected_distance < best_selected_distance:
            best_selected_distance = selected_distance
            left_px = int(left * scale_x)
            top_px = int(top * scale_y)
            right_px = int(right * scale_x)
            bottom_px = int(bottom * scale_y)
            best_bbox = (
                left_px,
                top_px,
                max(1, right_px - left_px),
                max(1, bottom_px - top_px),
            )

    if best_selected_distance is not None and best_bbox is not None:
        distance = best_selected_distance
        known = True
        label = selected_name
        bbox = best_bbox

    return known, label, distance, bbox


def _zed_face_worker_loop() -> None:
    while not _stop_event.is_set():
        _zed_face_event.wait(timeout=0.15)
        _zed_face_event.clear()

        with _zed_face_lock:
            frame = _zed_face_state["frame"]
            pending = bool(_zed_face_state["pending"])
            _zed_face_state["frame"] = None

        if not pending or frame is None:
            continue

        now = _now()
        selected_name, selected_encoding = _selected_face_encoding()
        with _face_lock:
            known_names = list(_face_known_names)
            known_encodings = [np.array(enc, dtype=np.float32) for enc in _face_known_encodings]

        known = False
        label = ""
        distance = None
        bbox = None

        if selected_name and selected_encoding is not None and known_names and known_encodings:
            try:
                known, label, distance, bbox = _detect_selected_face_on_frame(
                    frame,
                    selected_name,
                    known_names,
                    known_encodings,
                )
            except Exception as exc:
                log.warning("Falha na deteccao facial da ZED: %s", exc)

        with _face_lock:
            _face_runtime["known"] = known
            _face_runtime["label"] = label
            _face_runtime["distance"] = distance
            _face_runtime["bbox"] = bbox if known else None
            _face_runtime["next_scan_at"] = now + FACE_SCAN_INTERVAL
            _face_runtime["template_loaded"] = bool(_face_known_names)
            _face_runtime["selected_name"] = selected_name
            if bbox is not None:
                _face_runtime["last_seen_at"] = now
            if known and now - float(_face_runtime["last_announce_at"]) >= 4.0:
                log.info("Face reconhecida na ZED: %s", selected_name)
                _face_runtime["last_announce_at"] = now

        with _zed_face_lock:
            _zed_face_state["pending"] = False
            _zed_face_state["known"] = known
            _zed_face_state["label"] = label
            _zed_face_state["distance"] = distance
            _zed_face_state["bbox"] = bbox if known else None
            _zed_face_state["next_scan_at"] = now + FACE_SCAN_INTERVAL


def _compute_tilt_target(ax: float, ay: float, az: float) -> float:
    # Kinect v1: eixo X do acelerometro acompanha bem o "pitch" do tilt.
    gravity = math.sqrt((ax * ax) + (ay * ay) + (az * az))
    if gravity < 1e-6:
        return float(max(KINECT_TILT_MIN, min(KINECT_TILT_MAX, KINECT_TILT_NEUTRAL)))

    pitch_deg = math.degrees(math.atan2(ax, max(1e-6, abs(az))))
    if abs(pitch_deg) < KINECT_TILT_PITCH_DEADBAND:
        pitch_deg = 0.0

    requested = KINECT_TILT_NEUTRAL - (pitch_deg * KINECT_TILT_GAIN)
    return float(max(KINECT_TILT_MIN, min(KINECT_TILT_MAX, requested)))


def _command_kinect_tilt(angle: float) -> tuple[bool, str, Optional[float]]:
    global _tilt_manual_until, _tilt_manual_target, _tilt_last_set_at, _tilt_last_target

    target = float(max(KINECT_TILT_MIN, min(KINECT_TILT_MAX, float(angle))))
    _tilt_manual_target = target
    _tilt_manual_until = _now() + KINECT_TILT_MANUAL_HOLD
    _tilt_last_target = target
    _tilt_last_set_at = 0.0

    with _state_lock:
        _state["tilt_target_deg"] = round(target, 2)

    # Quando disponivel, envia comando imediato sem depender do callback do video.
    # Isso melhora o feedback dos botoes Tilt +/- no painel.
    direct_sent = False
    if freenect is not None:
        try:
            if hasattr(freenect, "sync_set_tilt_degs"):
                freenect.sync_set_tilt_degs(int(round(target)))
                direct_sent = True
        except Exception:
            direct_sent = False

    message = "tilt aplicado" if direct_sent else "comando enviado"
    return True, message, target


def _set_kinect_stabilization(enabled: bool) -> bool:
    global KINECT_STABILIZATION_ENABLED
    with _tilt_control_lock:
        KINECT_STABILIZATION_ENABLED = bool(enabled)
    return KINECT_STABILIZATION_ENABLED


def _get_kinect_stabilization() -> bool:
    with _tilt_control_lock:
        return bool(KINECT_STABILIZATION_ENABLED)


def _do_tilt_body(dev) -> None:
    """Ajusta o tilt do Kinect para manter o frame mais nivelado."""
    global _tilt_last_set_at, _tilt_last_target, _tilt_manual_until, _tilt_manual_target, _tilt_pitch_filtered

    stabilization_enabled = _get_kinect_stabilization()
    # Mesmo com estabilizacao OFF, respeita comando manual recente de tilt.
    if (not stabilization_enabled) and (_now() >= _tilt_manual_until):
        return

    now = _now()
    if now - _tilt_last_set_at < KINECT_TILT_INTERVAL:
        return
    _tilt_last_set_at = now

    try:
        freenect.update_tilt_state(dev)
        tilt_state = freenect.get_tilt_state(dev)
        raw_tilt = float(freenect.get_tilt_degs(tilt_state))
        accel = freenect.get_mks_accel(tilt_state)
        ax, ay, az = float(accel[0]), float(accel[1]), float(accel[2])

        manual_tilt_active = now < _tilt_manual_until
        if manual_tilt_active:
            target_f = _tilt_manual_target
        elif stabilization_enabled:
            # Filtro leve no acelerometro para reduzir jitter de leitura.
            measured_pitch = math.degrees(math.atan2(ax, max(1e-6, abs(az))))
            _tilt_pitch_filtered = (_tilt_pitch_filtered * 0.70) + (measured_pitch * 0.30)
            target_f = _compute_tilt_target(_tilt_pitch_filtered, ay, az)
            alpha = max(0.0, min(1.0, KINECT_TILT_SMOOTHING))
            if alpha > 0.0:
                target_f = (_tilt_last_target * (1.0 - alpha)) + (target_f * alpha)
        else:
            target_f = raw_tilt

        target_f = float(max(KINECT_TILT_MIN, min(KINECT_TILT_MAX, target_f)))

        # Limita passo apenas na estabilizacao automatica.
        if not manual_tilt_active:
            step = float(max(0.2, KINECT_TILT_MAX_STEP))
            if target_f > raw_tilt + step:
                target_f = raw_tilt + step
            elif target_f < raw_tilt - step:
                target_f = raw_tilt - step

        command_deadband = 0.2 if manual_tilt_active else KINECT_TILT_DEADBAND
        if abs(raw_tilt - target_f) >= command_deadband:
            freenect.set_tilt_degs(dev, int(round(target_f)))
        _tilt_last_target = target_f

        with _state_lock:
            _state["tilt_deg"] = raw_tilt
            _state["tilt_target_deg"] = round(target_f, 2)
            _state["accel"] = [round(ax, 3), round(ay, 3), round(az, 3)]
            if abs(ax) < 0.001 and abs(ay) < 0.001 and abs(az) < 0.001:
                _state["kinect_error"] = "tilt sem resposta do acelerometro"
            elif str(_state.get("kinect_error") or "").startswith("tilt sem resposta"):
                _state["kinect_error"] = None
    except Exception as exc:
        with _state_lock:
            _state["kinect_error"] = f"falha estabilizacao: {exc}"


def _candidate_arduino_ports():
    preferred = os.environ.get("AGV_ARDUINO_PORT", "").strip()
    ports = []
    if preferred:
        ports.append(preferred)

    try:
        from serial.tools import list_ports  # type: ignore[import-not-found]
    except Exception:
        list_ports = None

    if list_ports is not None:
        try:
            discovered = []
            for info in list_ports.comports():
                device = str(getattr(info, "device", "") or "").strip()
                if not device:
                    continue
                discovered.append(device)
            ports.extend(sorted(discovered))
        except Exception:
            pass

    ports.extend(sorted(glob.glob("/dev/serial/by-id/*")))
    ports.extend(sorted(glob.glob("/dev/ttyACM*")))
    ports.extend(sorted(glob.glob("/dev/ttyUSB*")))
    ports.extend(sorted(glob.glob("/dev/ttyAMA*")))
    ports.extend(sorted(glob.glob("/dev/rfcomm*")))

    dedup = []
    seen = set()
    for port in ports:
        if not port:
            continue
        key = os.path.realpath(port)
        if key in seen:
            continue
        seen.add(key)
        dedup.append(port)
    return dedup


def _close_arduino_locked() -> None:
    global _arduino_serial

    if _arduino_serial is None:
        return
    try:
        _arduino_serial.close()
    except Exception:
        pass
    _arduino_serial = None


def _drain_arduino_input_locked(ser, max_lines: int = 12) -> list[str]:
    lines = []
    prev_line = _arduino_rx_state["last_line"]
    prev_at = float(_arduino_rx_state["at"])

    try:
        waiting = int(getattr(ser, "in_waiting", 0) or 0)
    except Exception:
        waiting = 0

    while waiting > 0 and len(lines) < max_lines:
        try:
            raw = ser.readline()
        except Exception as exc:
            _arduino_state["last_error"] = f"falha ao ler serial: {exc}"
            break

        if not raw:
            break

        line = raw.decode("utf-8", errors="ignore").strip()
        if line:
            lines.append(line)
            _arduino_rx_state["last_line"] = line
            _arduino_rx_state["at"] = _now()

            if "ERROR" in line or "TIMEOUT" in line:
                _arduino_state["last_error"] = line
            elif (
                line.startswith("ARDUINO:OK")
                or line.startswith("ARDUINO:READY")
                or line.startswith("ARDUINO:PONG")
                or line.startswith("STATUS:")
            ):
                _arduino_state["last_error"] = None

        try:
            waiting = int(getattr(ser, "in_waiting", 0) or 0)
        except Exception:
            break

    if lines:
        last_line = lines[-1]
        should_log = (
            prev_line != last_line
            or (_now() - prev_at) >= 2.0
            or "ERROR" in last_line
            or "TIMEOUT" in last_line
        )
        if should_log:
            log.info("Arduino RX: %s", last_line)

    return lines


def _probe_arduino_locked(ser) -> bool:
    lines = _drain_arduino_input_locked(ser)
    if any(("READY" in line or "PONG" in line or "OK" in line or line.startswith("STATUS:")) for line in lines):
        return True

    try:
        ser.write(b"PING\n")
        ser.flush()
        time.sleep(0.25)
    except Exception:
        return False

    lines = _drain_arduino_input_locked(ser)
    return any(("READY" in line or "PONG" in line or "OK" in line or line.startswith("STATUS:")) for line in lines)


def _connect_arduino() -> bool:
    global _arduino_serial

    if not _arduino_state["enabled"]:
        with _arduino_lock:
            _arduino_state["last_error"] = "serial desativada por ambiente"
        return False

    try:
        import serial  # type: ignore[import-not-found]
    except Exception as exc:
        with _arduino_lock:
            _arduino_state["connected"] = False
            _arduino_state["last_error"] = f"pyserial ausente: {exc}"
        return False

    ports = _candidate_arduino_ports()
    if not ports:
        with _arduino_lock:
            _arduino_state["connected"] = False
            _arduino_state["last_error"] = "nenhuma porta serial encontrada"
        return False

    for port in ports:
        try:
            ser = serial.Serial(
                port=port,
                baudrate=int(_arduino_state["baud"]),
                timeout=0.1,
                write_timeout=0.1,
                dsrdtr=False,
                rtscts=False,
            )
            try:
                ser.setDTR(False)
                ser.setRTS(False)
            except Exception:
                pass

            # O Arduino Uno costuma reiniciar ao abrir a serial USB.
            # Espera curta demais faz os primeiros comandos se perderem.
            time.sleep(1.8)
            try:
                ser.reset_output_buffer()
            except Exception:
                pass

            with _arduino_lock:
                _arduino_serial = ser
                _arduino_state["connected"] = True
                _arduino_state["port"] = os.path.realpath(port)
                _arduino_state["last_error"] = None
                identified = _probe_arduino_locked(ser)
                if not identified:
                    _arduino_state["last_error"] = "porta abriu, mas sem resposta do firmware"
            log.info("Arduino conectado em %s @ %s", os.path.realpath(port), _arduino_state["baud"])
            return True
        except Exception as exc:
            detail = str(exc)
            if isinstance(exc, PermissionError) or "Permission denied" in detail or "Errno 13" in detail:
                detail = f"sem permissao para abrir {port}; entre novamente na sessao ou adicione o usuario ao grupo dialout"
            with _arduino_lock:
                _arduino_state["connected"] = False
                _arduino_state["last_error"] = f"falha ao abrir {port}: {detail}"

    return False


def _speed_steering_to_payload(speed: int, steering: int) -> tuple[int, int]:
    speed_cmd = _clamp(speed, -100, 100)
    steering_cmd = _clamp(steering, -100, 100)

    abs_speed_pct = abs(speed_cmd)
    accel = abs_speed_pct * 255 // 100

    if abs_speed_pct > 0:
        accel = max(_clamp(MIN_FORWARD_PWM, 0, 255), accel)

    if speed_cmd < 0:
        accel = -accel

    if accel == 0 and abs(steering_cmd) >= 20:
        accel = _clamp(MIN_TURN_PWM, 0, 255)

    steering_gain = 0.70 if accel != 0 else 1.00
    dir_val = int(steering_cmd * 254 * steering_gain / 100)
    if abs(dir_val) < 8:
        dir_val = 0

    return accel, dir_val


def _send_to_arduino(speed: int, steering: int, mode: str, source: str) -> bool:
    global _arduino_serial

    if not _arduino_state["enabled"]:
        with _arduino_lock:
            _arduino_state["last_error"] = "serial desativada por ambiente"
        return False

    with _arduino_lock:
        ser = _arduino_serial

    if ser is None and not _connect_arduino():
        return False

    with _arduino_lock:
        ser = _arduino_serial
    if ser is None:
        return False

    accel, dir_val = _speed_steering_to_payload(speed, steering)

    # Speed limit: escala linear em 0-255 para que o usuario sinta diferenca real.
    # A 50% o PWM maximo e 127 (visivelmente mais lento que 255).
    with _settings_lock:
        limit_pct = _settings["speed_limit_pct"]
    if limit_pct < 100 and accel != 0:
        max_pwm = int(255 * limit_pct / 100)
        accel = max(-max_pwm, min(max_pwm, accel))

    payload = f"{accel},{dir_val}"

    try:
        with _arduino_lock:
            _drain_arduino_input_locked(ser)
        ser.write((payload + "\n").encode("ascii", errors="ignore"))
        try:
            ser.flush()
        except Exception:
            pass
        with _arduino_lock:
            _arduino_state["connected"] = True
            _arduino_state["last_command"] = payload
            _arduino_state["last_error"] = None
            _arduino_state["last_tx_at"] = _now()
            _drain_arduino_input_locked(ser)
        log_changed = (
            _arduino_log_state["payload"] != payload
            or _arduino_log_state["mode"] != mode
            or _arduino_log_state["source"] != source
            or (_now() - float(_arduino_log_state["at"])) >= 2.0
        )
        if log_changed:
            log.info("Arduino TX: %s (mode=%s source=%s)", payload, mode, source)
            _arduino_log_state["payload"] = payload
            _arduino_log_state["mode"] = mode
            _arduino_log_state["source"] = source
            _arduino_log_state["at"] = _now()
        return True
    except Exception as exc:
        with _arduino_lock:
            _arduino_state["connected"] = False
            _arduino_state["last_error"] = str(exc)
            _close_arduino_locked()
        return False


def _depth_segment_distance(depth_seg: np.ndarray, is_mm: bool) -> Optional[float]:
    if is_mm:
        valid = depth_seg[(depth_seg > 200) & (depth_seg < 8000)]
        if valid.size:
            return float(np.min(valid)) / 1000.0
        return None

    valid = depth_seg[(depth_seg > 100) & (depth_seg < 2040)]
    if not valid.size:
        return None
    raw = float(np.max(valid))
    denom = raw * -0.0030711016 + 3.3309495161
    if denom <= 0.0:
        return None
    depth_m = 1.0 / denom
    if 0.15 <= depth_m <= 10.0:
        return depth_m
    return None


def _compute_depth_metrics(depth: np.ndarray) -> tuple[bool, Optional[float], Optional[float], Optional[float], Optional[float]]:
    if depth is None or depth.size == 0:
        return False, None, None, None, None

    height, width = depth.shape[:2]
    is_mm = bool(np.nanmax(depth) > 3000)

    row_a = int(height * 0.35)
    row_b = int(height * 0.65)
    left_a = int(width * 0.10)
    left_b = int(width * 0.30)
    center_a = int(width * 0.40)
    center_b = int(width * 0.60)
    right_a = int(width * 0.70)
    right_b = int(width * 0.90)

    left = _depth_segment_distance(depth[row_a:row_b, left_a:left_b], is_mm)
    center = _depth_segment_distance(depth[row_a:row_b, center_a:center_b], is_mm)
    right = _depth_segment_distance(depth[row_a:row_b, right_a:right_b], is_mm)

    distance = center
    candidates = [value for value in (left, center, right) if value is not None]
    if distance is None and candidates:
        distance = min(candidates)

    return is_mm, distance, left, center, right


def _depth_to_colormap(depth: np.ndarray, is_mm: bool) -> np.ndarray:
    if is_mm:
        clipped = np.clip(depth.astype(np.float32), 200, 4000)
        norm = ((clipped - 200.0) / 3800.0 * 255.0).astype(np.uint8)
    else:
        clipped = np.clip(depth.astype(np.float32), 0, 2047)
        norm = (clipped / 2047.0 * 255.0).astype(np.uint8)
    return cv2.applyColorMap(255 - norm, cv2.COLORMAP_JET)


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


def _kinect_device_available() -> tuple[bool, Optional[str]]:
    if freenect is None:
        return False, "python3-freenect nao disponivel no sistema"

    ctx = None
    try:
        ctx = freenect.init()
        if ctx is None:
            return False, "falha ao iniciar libfreenect"
        count = int(freenect.num_devices(ctx))
        if count <= 0:
            return False, "Kinect nao detectado no USB"
        return True, None
    except Exception as exc:
        return False, f"Kinect indisponivel: {exc}"
    finally:
        if ctx is not None:
            try:
                freenect.shutdown(ctx)
            except Exception:
                pass


def _zed_source_value(source_raw: str):
    value = str(source_raw or "").strip()
    if value.isdigit():
        return int(value)
    return value or 0


def _zed_nodes_from_sysfs() -> list[str]:
    """Detecta nodes ZED pelo sysfs. Tenta varios nomes: zed, stereolabs, usb video, ou qualquer device USB."""
    nodes = []
    for name_path in sorted(glob.glob("/sys/class/video4linux/video*/name")):
        try:
            card_name = open(name_path, "r", encoding="utf-8", errors="ignore").read().strip().lower()
        except Exception:
            continue
        
        # Tenta match por ZED_NAME_HINT ou por pattern STEREOLABS ou USB generico
        is_match = (
            (ZED_NAME_HINT and ZED_NAME_HINT in card_name) or
            "stereolabs" in card_name or
            "stereo" in card_name or
            ("usb" in card_name and "video" in card_name)
        )
        if not is_match:
            continue

        video_node = os.path.basename(os.path.dirname(name_path))
        node_path = f"/dev/{video_node}"
        if os.path.exists(node_path):
            nodes.append(node_path)
            log.info("ZED device detectado via sysfs: %s (%s)", node_path, card_name)
    return nodes


def _zed_candidate_sources() -> list:
    candidates = []
    preferred_raw = str(ZED_DEVICE or "").strip()
    preferred = _zed_source_value(preferred_raw)
    if preferred_raw:
        candidates.append(preferred)

    for node in _zed_nodes_from_sysfs():
        if node not in candidates:
            candidates.append(node)

    if not ZED_STRICT_DEVICE:
        for path in sorted(glob.glob("/dev/video*")):
            if path not in candidates:
                candidates.append(path)

    dedup = []
    seen = set()
    for source in candidates:
        key = str(source)
        if key in seen:
            continue
        seen.add(key)
        dedup.append(source)
    return dedup


def _open_zed_capture(source) -> tuple[Optional[subprocess.Popen], Optional[str]]:
    """Abre stream de video ZED via FFmpeg (OpenCV nao consegue abrir device v4l2)."""
    if not isinstance(source, str) or not source.startswith("/dev/video"):
        return None, "source invalido"
    
    try:
        if not os.path.exists(source):
            return None, f"device {source} nao existe"
        
        base_prefix = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel", "error",
            "-fflags", "nobuffer",
            "-flags", "low_delay",
            "-probesize", "32",
            "-analyzeduration", "0",
            "-f", "v4l2",
            "-thread_queue_size", "32",
            "-video_size", f"{ZED_WIDTH}x{ZED_HEIGHT}",
            "-framerate", str(ZED_FPS),
        ]

        variants = [
            {
                "name": "mjpeg-copy",
                "cmd": base_prefix
                + [
                    "-input_format", "mjpeg",
                    "-i", source,
                    "-an", "-sn", "-dn",
                    "-fflags", "flush_packets",
                    "-f", "mjpeg",
                    "-c:v", "copy",
                    "-",
                ],
            },
            {
                "name": "yuyv-encode",
                "cmd": base_prefix
                + [
                    "-input_format", "yuyv422",
                    "-i", source,
                    "-an", "-sn", "-dn",
                    "-fflags", "flush_packets",
                    "-vsync", "drop",
                    "-f", "image2pipe",
                    "-c:v", "mjpeg",
                    "-q:v", "3",
                    "-",
                ],
            },
        ]

        last_err = ""
        for variant in variants:
            proc = subprocess.Popen(
                variant["cmd"],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                bufsize=0,
            )

            time.sleep(0.25)
            if proc.poll() is None:
                log.info(
                    "FFmpeg stream aberto: %s (%dx%d @ %d fps, %s)",
                    source,
                    ZED_WIDTH,
                    ZED_HEIGHT,
                    ZED_FPS,
                    variant["name"],
                )
                return proc, None

            try:
                proc.communicate(timeout=0.4)
            except Exception:
                pass
            last_err = f"variant {variant['name']} falhou"

        return None, f"ffmpeg falhou: {last_err or 'unknown'}"
    except Exception as exc:
        return None, str(exc)


def _open_zed_cv_capture(source) -> tuple[Optional[cv2.VideoCapture], Optional[str]]:
    if not isinstance(source, str) or not source.startswith("/dev/video"):
        return None, "source invalido"
    if not os.path.exists(source):
        return None, f"device {source} nao existe"

    try:
        cap = cv2.VideoCapture(source, cv2.CAP_V4L2)
        if not cap.isOpened():
            cap.release()
            return None, "falha ao abrir com OpenCV/V4L2"

        fourcc = cv2.VideoWriter_fourcc(*"YUYV")
        cap.set(cv2.CAP_PROP_FOURCC, fourcc)
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, float(ZED_WIDTH))
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, float(ZED_HEIGHT))
        cap.set(cv2.CAP_PROP_FPS, float(ZED_FPS))
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

        actual_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        actual_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        actual_fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
        log.info(
            "OpenCV stream aberto: %s (%dx%d @ %.1f fps)",
            source,
            actual_w,
            actual_h,
            actual_fps,
        )
        return cap, None
    except Exception as exc:
        return None, str(exc)


def _read_frame_from_ffmpeg(
    proc: subprocess.Popen,
    carry: bytes,
    timeout_s: float = 0.35,
) -> tuple[Optional[np.ndarray], bytes]:
    """Le o frame mais recente do pipe FFmpeg, descartando backlog para reduzir latencia."""
    if not proc or proc.poll() is not None:
        return None, b""

    stream = proc.stdout
    if stream is None:
        return None, b""
    
    frame_data = carry
    deadline = _now() + timeout_s
    
    try:
        while _now() < deadline:
            wait = max(0.0, deadline - _now())
            readable, _, _ = select.select([stream], [], [], wait)
            if not readable:
                break

            chunk = stream.read(65536)
            if not chunk:
                return None, frame_data
            
            frame_data += chunk

            # Mantem apenas uma janela recente para evitar crescimento sem limite.
            if len(frame_data) > 4_000_000:
                frame_data = frame_data[-4_000_000:]
            
            if b"\xff\xd9" in frame_data:
                end_idx = frame_data.rfind(b"\xff\xd9") + 2
                start_idx = frame_data.rfind(b"\xff\xd8", 0, end_idx)
                
                if start_idx >= 0:
                    jpeg_bytes = frame_data[start_idx:end_idx]
                    remaining = frame_data[end_idx:]
                    frame = cv2.imdecode(np.frombuffer(jpeg_bytes, np.uint8), cv2.IMREAD_COLOR)
                    if frame is not None:
                        return frame, remaining
                    frame_data = remaining
    except Exception:
        pass
    
    return None, frame_data


def _zed_capture_loop() -> None:
    fps_counter = 0
    fps_mark = _now()
    failed_until: dict[str, float] = {}

    while not _stop_event.is_set():
        proc = None
        cap = None
        pipe_carry = b""
        try:
            selected_source = None
            last_error = None

            for candidate in _zed_candidate_sources():
                candidate_key = str(candidate)
                if float(failed_until.get(candidate_key, 0.0)) > _now():
                    continue

                cap, open_error = _open_zed_cv_capture(candidate)
                if cap is not None:
                    selected_source = candidate
                    break

                proc, open_error = _open_zed_capture(candidate)
                if proc is not None:
                    selected_source = candidate
                    break

                last_error = open_error
                failed_until[candidate_key] = _now() + 20.0

            if cap is None and proc is None:
                with _state_lock:
                    _state["zed_ok"] = False
                    _state["zed_error"] = f"nao abriu webcam ZED ({last_error or 'sem detalhe'})"
                _stop_event.wait(2.0)
                continue

            with _state_lock:
                _state["zed_ok"] = True
                _state["zed_error"] = None
                _state["zed_device"] = str(selected_source)

            while not _stop_event.is_set():
                if cap is not None:
                    ok, frame = cap.read()
                    if not ok or frame is None:
                        frame = None
                else:
                    frame, pipe_carry = _read_frame_from_ffmpeg(proc, pipe_carry)

                if frame is None:
                    with _state_lock:
                        _state["zed_ok"] = False
                        _state["zed_error"] = f"falha de leitura da webcam ZED em {selected_source}"
                    failed_until[str(selected_source)] = _now() + 20.0
                    break

                now = _now()
                with _zed_face_lock:
                    should_queue = (not bool(_zed_face_state["pending"])) and (now >= float(_zed_face_state["next_scan_at"]))
                    if should_queue:
                        _zed_face_state["pending"] = True
                        _zed_face_state["frame"] = frame.copy()
                        _zed_face_event.set()
                    known = bool(_zed_face_state["known"])
                    label = str(_zed_face_state["label"])
                    bbox = _zed_face_state["bbox"]

                annotated_frame = frame
                if known and bbox is not None:
                    x, y, bw, bh = bbox
                    cv2.rectangle(annotated_frame, (x, y), (x + bw, y + bh), (80, 230, 120), 2)
                    cv2.putText(
                        annotated_frame,
                        label,
                        (x, max(18, y - 8)),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.65,
                        (80, 230, 120),
                        2,
                    )

                with _state_lock:
                    if isinstance(frame, np.ndarray) and frame.size > 0:
                        _state["zed"] = annotated_frame
                        _state["zed_ok"] = True
                        _state["zed_error"] = None
                        _state["zed_last_frame_at"] = _now()

                fps_counter += 1
                now = _now()
                elapsed = now - fps_mark
                if elapsed >= 1.0:
                    with _state_lock:
                        _state["fps_zed"] = round(fps_counter / elapsed, 1)
                    fps_counter = 0
                    fps_mark = now

                _stop_event.wait(max(0.001, 1.0 / max(1, ZED_FPS)))
        except Exception as exc:
            with _state_lock:
                _state["zed_ok"] = False
                _state["zed_error"] = f"erro na webcam ZED: {exc}"
            _stop_event.wait(2.0)
        finally:
            if cap is not None:
                try:
                    cap.release()
                except Exception:
                    pass
            if proc is not None:
                try:
                    proc.terminate()
                    proc.wait(timeout=1)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass


def _get_status_snapshot() -> dict:
    with _state_lock:
        state = {
            "distance_m": _state["distance_m"],
            "left_clearance_m": _state["left_clearance_m"],
            "center_clearance_m": _state["center_clearance_m"],
            "right_clearance_m": _state["right_clearance_m"],
            "tilt_deg": _state["tilt_deg"],
            "tilt_target_deg": _state["tilt_target_deg"],
            "accel": list(_state["accel"]),
            "fps_rgb": _state["fps_rgb"],
            "fps_depth": _state["fps_depth"],
            "kinect_ok": _state["kinect_ok"],
            "kinect_error": _state["kinect_error"],
            "last_frame_at": _state["last_frame_at"],
        }
    with _control_lock:
        control = {
            "mode": _control_state["mode"],
            "manual_speed": _control_state["manual_speed"],
            "manual_steering": _control_state["manual_steering"],
            "active_speed": _control_state["active_speed"],
            "active_steering": _control_state["active_steering"],
            "updated_at": _control_state["updated_at"],
            "last_source": _control_state["last_source"],
            "arduino_sent": _control_state["arduino_sent"],
            "arduino_error": _control_state["arduino_error"],
        }
    with _autopilot_lock:
        autopilot = dict(_autopilot_state)
    with _arduino_lock:
        arduino = dict(_arduino_state)

    last_frame_at = state["last_frame_at"]
    state["frame_age_s"] = None if last_frame_at <= 0 else round(_now() - last_frame_at, 2)
    zed_last_frame_at = float(_state["zed_last_frame_at"])
    zed_frame_age = None if zed_last_frame_at <= 0 else round(_now() - zed_last_frame_at, 2)

    return {
        "ok": True,
        "state": state,
        "agv": control,
        "autopilot": autopilot,
        "arduino": arduino,
        "kinect": {
            "stabilization_enabled": _get_kinect_stabilization(),
        },
        "zed": {
            "online": bool(_state["zed_ok"]),
            "error": _state["zed_error"],
            "fps": float(_state["fps_zed"]),
            "frame_age_s": zed_frame_age,
            "device": _state.get("zed_device") or ZED_DEVICE,
        },
        "face": _face_status_snapshot(),
        "settings": dict(_settings),
        "runtime": {
            "host": DEFAULT_HOST,
            "port": DEFAULT_PORT,
            "source": os.path.basename(__file__),
            "project_root": PROJECT_ROOT,
        },
    }


def _capture_loop() -> None:
    if freenect is None:
        while not _stop_event.is_set():
            with _state_lock:
                _state["kinect_ok"] = False
                _state["kinect_error"] = "python3-freenect nao disponivel no sistema"
            _stop_event.wait(1.0)
        return

    # Fila mínima para passar dados brutos das callbacks (rápidas) para o
    # thread de processamento pesado (face detection, depth metrics).
    _raw_state: dict = {"rgb": None, "depth": None, "rgb_fresh": False, "depth_fresh": False}
    _raw_lock = threading.Lock()
    _process_event = threading.Event()

    # FPS contadores
    rgb_counter = [0]
    depth_counter = [0]
    fps_mark = [_now()]

    def _process_loop():
        """Thread separado: face detection + depth metrics (pesado, libera GIL)."""
        while not _stop_event.is_set():
            _process_event.wait(timeout=0.1)
            _process_event.clear()

            with _raw_lock:
                rgb = _raw_state["rgb"] if _raw_state["rgb_fresh"] else None
                dep = _raw_state["depth"] if _raw_state["depth_fresh"] else None
                _raw_state["rgb_fresh"] = False
                _raw_state["depth_fresh"] = False

            if rgb is not None:
                with _state_lock:
                    _state["rgb_raw"] = rgb
                    _state["rgb"] = rgb
                    _state["kinect_ok"] = True
                    _state["kinect_error"] = None
                    _state["last_frame_at"] = _now()
                # FPS
                rgb_counter[0] += 1
                now = _now()
                elapsed = now - fps_mark[0]
                if elapsed >= 1.0:
                    with _state_lock:
                        _state["fps_rgb"] = round(rgb_counter[0] / elapsed, 1)
                        _state["fps_depth"] = round(depth_counter[0] / elapsed, 1)
                    rgb_counter[0] = 0
                    depth_counter[0] = 0
                    fps_mark[0] = now

            if dep is not None:
                depth_array = np.asarray(dep)
                is_mm, distance, left, center, right = _compute_depth_metrics(depth_array)
                with _state_lock:
                    _state["depth"] = depth_array
                    _state["depth_mm"] = is_mm
                    _state["distance_m"] = distance
                    _state["left_clearance_m"] = left
                    _state["center_clearance_m"] = center
                    _state["right_clearance_m"] = right
                depth_counter[0] += 1

    proc_thread = threading.Thread(target=_process_loop, daemon=True, name="agv-kinect-proc")
    proc_thread.start()

    while not _stop_event.is_set():
        _release_kinect_usb_claims()
        available, error = _kinect_device_available()
        if not available:
            with _state_lock:
                _state["kinect_ok"] = False
                _state["kinect_error"] = error
            _stop_event.wait(4.0)
            continue

        # Callbacks MÍNIMAS: apenas copia dados brutos e sinaliza evento
        def _video_cb(dev, data, timestamp):
            _do_tilt_body(dev)
            with _raw_lock:
                _raw_state["rgb"] = cv2.cvtColor(data, cv2.COLOR_RGB2BGR)
                _raw_state["rgb_fresh"] = True
            _process_event.set()

        def _depth_cb(dev, data, timestamp):
            with _raw_lock:
                _raw_state["depth"] = data
                _raw_state["depth_fresh"] = True
            _process_event.set()

        def _body_cb(dev, ctx):
            if _stop_event.is_set():
                raise freenect.Kill
            _do_tilt_body(dev)

        try:
            freenect.runloop(depth=_depth_cb, video=_video_cb, body=_body_cb)
        except Exception as exc:
            with _state_lock:
                _state["kinect_ok"] = False
                _state["kinect_error"] = str(exc) or "falha no Kinect"
            try:
                if hasattr(freenect, "sync_stop"):
                    freenect.sync_stop()
            except Exception:
                pass
            if not _stop_event.is_set():
                _stop_event.wait(5.0)


def _compute_autopilot_command() -> tuple[int, int, str]:
    with _state_lock:
        kinect_ok = bool(_state["kinect_ok"])
        left = _state["left_clearance_m"]
        center = _state["center_clearance_m"]
        right = _state["right_clearance_m"]
        distance = _state["distance_m"]

    if not kinect_ok:
        return 0, 0, "sem-kinect"
    if center is None and distance is None:
        return 0, 0, "sem-depth"

    center_distance = center if center is not None else distance
    if center_distance is None:
        return 0, 0, "sem-distancia"

    left_value = left if left is not None else center_distance
    right_value = right if right is not None else center_distance

    if center_distance < 0.35:
        turn = 70 if right_value >= left_value else -70
        return 0, turn, "obstaculo-muito-perto"
    if center_distance < 0.60:
        turn = 65 if right_value >= left_value else -65
        return 18, turn, "desvio-curto"
    if center_distance < 0.95:
        turn = 45 if right_value >= left_value else -45
        return 32, turn, "desvio-suave"

    steering = 0
    if left is not None and right is not None:
        diff = right - left
        steering = _clamp(int(diff * 70.0), -35, 35)
        if abs(steering) < 8:
            steering = 0
    return 48, steering, "livre"


def _autopilot_loop() -> None:
    while not _stop_event.is_set():
        with _control_lock:
            auto_mode = _control_state["mode"] == "auto"

        if auto_mode:
            speed, steering, reason = _compute_autopilot_command()
            with _autopilot_lock:
                _autopilot_state["running"] = True
                _autopilot_state["target_speed"] = speed
                _autopilot_state["target_steering"] = steering
                _autopilot_state["last_reason"] = reason
                _autopilot_state["last_tick"] = _now()
        else:
            with _autopilot_lock:
                _autopilot_state["running"] = False
                _autopilot_state["target_speed"] = 0
                _autopilot_state["target_steering"] = 0
                _autopilot_state["last_reason"] = "manual"
                _autopilot_state["last_tick"] = _now()

        _stop_event.wait(AUTO_LOOP_SECONDS)


def _control_loop() -> None:
    while not _stop_event.is_set():
        with _control_lock:
            mode = _control_state["mode"]
            manual_speed = _control_state["manual_speed"]
            manual_steering = _control_state["manual_steering"]
            source = _control_state["last_source"]
            updated_at = _control_state["updated_at"]

        if mode == "auto":
            with _autopilot_lock:
                speed = int(_autopilot_state["target_speed"])
                steering = int(_autopilot_state["target_steering"])
            command_source = "autopilot"
        else:
            # Watchdog: se nenhum novo comando chegou dentro do timeout, para o AGV.
            if WATCHDOG_TIMEOUT_S > 0 and (_now() - updated_at) > WATCHDOG_TIMEOUT_S:
                speed = 0
                steering = 0
                command_source = "watchdog"
            else:
                speed = manual_speed
                steering = manual_steering
                command_source = source

        sent = _send_to_arduino(speed, steering, mode, command_source)
        error = None
        if not sent:
            with _arduino_lock:
                error = _arduino_state.get("last_error")

        with _control_lock:
            _control_state["active_speed"] = speed
            _control_state["active_steering"] = steering
            _control_state["arduino_sent"] = bool(sent)
            _control_state["arduino_error"] = error

        _stop_event.wait(CONTROL_LOOP_SECONDS)


def start_runtime() -> None:
    global _runtime_started

    with _runtime_lock:
        if _runtime_started:
            return
        _runtime_started = True
        _stop_event.clear()
        _load_known_faces()

        specs = [
            ("agv-kinect", _capture_loop),
            ("agv-zed-face", _zed_face_worker_loop),
            ("agv-zed", _zed_capture_loop),
            ("agv-autopilot", _autopilot_loop),
            ("agv-control", _control_loop),
        ]
        for name, target in specs:
            thread = threading.Thread(target=target, daemon=True, name=name)
            thread.start()
            _threads.append(thread)

        log.info("Runtime agv iniciado")


def stop_runtime() -> None:
    global _runtime_started

    with _runtime_lock:
        if not _runtime_started:
            return
        _runtime_started = False
        _stop_event.set()

    with _arduino_lock:
        _close_arduino_locked()
        _arduino_state["connected"] = False

    for thread in list(_threads):
        thread.join(timeout=1.0)
    _threads.clear()
    log.info("Runtime agv finalizado")


atexit.register(stop_runtime)


def _mjpeg_generator(kind: str, fps: int):
    interval = 1.0 / max(1, fps)
    while True:
        quality = 82
        if kind == "rgb":
            with _state_lock:
                frame = None if _state["rgb"] is None else _state["rgb"].copy()
                error = _state["kinect_error"]
            if frame is None:
                frame = _placeholder_frame("Aguardando Kinect RGB")
                if error:
                    cv2.putText(frame, error[:44], (40, 280), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (120, 180, 255), 2)
        elif kind == "depth":
            with _state_lock:
                depth = None if _state["depth"] is None else _state["depth"].copy()
                is_mm = bool(_state["depth_mm"])
                error = _state["kinect_error"]
            if depth is None:
                frame = _placeholder_frame("Aguardando Kinect Depth")
                if error:
                    cv2.putText(frame, error[:44], (40, 280), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (120, 180, 255), 2)
            else:
                frame = _depth_to_colormap(depth, is_mm)
        else:
            with _state_lock:
                frame = None if _state["zed"] is None else _state["zed"].copy()
                error = _state["zed_error"]
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
            quality = _clamp(ZED_JPEG_QUALITY, 35, 95)

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


@app.route("/")
def route_index():
    return HTML_PAGE, 200, {
        "Content-Type": "text/html; charset=utf-8",
        "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
    }


@app.route("/video")
def route_video():
    return Response(_mjpeg_generator("rgb", VIDEO_FPS), mimetype="multipart/x-mixed-replace; boundary=frame")


@app.route("/depth_map")
def route_depth_map():
    return Response(_mjpeg_generator("depth", DEPTH_FPS), mimetype="multipart/x-mixed-replace; boundary=frame")


@app.route("/zed")
def route_zed():
    return Response(_mjpeg_generator("zed", ZED_FPS), mimetype="multipart/x-mixed-replace; boundary=frame")


@app.route("/api/video")
def route_api_video():
    return route_video()


@app.route("/api/depth_map")
def route_api_depth_map():
    return route_depth_map()


@app.route("/api/zed")
def route_api_zed():
    return route_zed()


@app.route("/status")
@app.route("/api/status")
def route_status():
    resp = jsonify(_get_status_snapshot())
    # Connection: close garante conexao TCP dedicada, sem compartilhar com os streams.
    resp.headers["Connection"] = "close"
    return resp


@app.route("/api/access_link")
def route_access_link():
    lan_ip = _detect_lan_ip()
    scheme = "https" if request.is_secure else "http"
    port = request.environ.get("SERVER_PORT", str(DEFAULT_PORT))
    return jsonify({
        "ok": True,
        "url": f"{scheme}://{lan_ip}:{port}/",
        "ip": lan_ip,
        "port": port,
    })


@app.route("/api/kinect/reconnect", methods=["POST"])
def route_kinect_reconnect():
    if freenect is not None:
        try:
            freenect.sync_stop()
        except Exception:
            pass
    _release_kinect_usb_claims()
    return jsonify({"ok": True, "requested": True})


@app.route("/api/kinect/tilt", methods=["POST"])
def route_kinect_tilt():
    payload = request.get_json(silent=True) or {}
    angle = float(payload.get("angle", 0.0))
    ok, message, applied = _command_kinect_tilt(angle)
    return jsonify({"ok": ok, "message": message, "tilt_target_deg": applied})


@app.route("/api/kinect/stabilization", methods=["GET", "POST"])
def route_kinect_stabilization():
    if request.method == "POST":
        payload = request.get_json(silent=True) or {}
        enabled = bool(payload.get("enabled", True))
        applied = _set_kinect_stabilization(enabled)
        return jsonify({"ok": True, "enabled": applied})

    return jsonify({"ok": True, "enabled": _get_kinect_stabilization()})


@app.route("/api/face/status")
def route_face_status():
    return jsonify({"ok": True, "face": _face_status_snapshot()})


@app.route("/api/face/register_face", methods=["POST"])
def route_face_register_face():
    face_name = _normalize_face_name(request.form.get("name", ""))
    image_file = request.files.get("image")

    if not face_name:
        return jsonify({"ok": False, "error": "name_required"}), 400
    if image_file is None or not image_file.filename:
        return jsonify({"ok": False, "error": "image_required"}), 400

    image_bytes = image_file.read()
    if not image_bytes:
        return jsonify({"ok": False, "error": "empty_image"}), 400

    frame = cv2.imdecode(np.frombuffer(image_bytes, np.uint8), cv2.IMREAD_COLOR)
    if frame is None:
        return jsonify({"ok": False, "error": "invalid_image"}), 400

    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    encodings = face_recognition.face_encodings(rgb)
    if len(encodings) == 0:
        return jsonify({"ok": False, "error": "no_face_detected"}), 400

    descriptor = np.array(encodings[0], dtype=np.float32)
    extension = os.path.splitext(image_file.filename or "")[1].lower()
    if extension not in (".jpg", ".jpeg", ".png", ".bmp", ".webp"):
        extension = ".jpg"

    os.makedirs(FACE_UPLOADS_DIR, exist_ok=True)
    stored_path = os.path.join(FACE_UPLOADS_DIR, f"{_face_name_slug(face_name)}{extension}")
    if not cv2.imwrite(stored_path, frame):
        return jsonify({"ok": False, "error": "failed_to_save_image"}), 500

    try:
        known_encodings, known_names, known_image_paths = _load_face_database()
    except Exception:
        known_encodings, known_names, known_image_paths = [], [], []

    target_index = None
    face_name_lower = face_name.lower()
    for index, existing_name in enumerate(known_names):
        if str(existing_name).lower() == face_name_lower:
            target_index = index
            break

    project_image_path = os.path.relpath(stored_path, PROJECT_ROOT)
    if target_index is None:
        known_encodings.append(descriptor)
        known_names.append(face_name)
        known_image_paths.append(project_image_path)
    else:
        known_encodings[target_index] = descriptor
        known_names[target_index] = face_name
        known_image_paths[target_index] = project_image_path

    _save_face_database(known_encodings, known_names, known_image_paths)
    _load_known_faces()
    _set_selected_face(face_name)

    return jsonify({
        "ok": True,
        "name": face_name,
        "image_path": project_image_path,
        "face": _face_status_snapshot(),
    })


@app.route("/api/face/select", methods=["POST"])
def route_face_select():
    payload = request.get_json(silent=True) or {}
    ok, message = _set_selected_face(str(payload.get("name", "")))
    status_code = 200 if ok else 404
    return jsonify({"ok": ok, "message": message, "face": _face_status_snapshot()}), status_code

#def route_face_register_face():
#    with _state_lock:
#        frame = None if _state["rgb_raw"] is None else _state["rgb_raw"].copy()
#
#    if frame is None:
#        return jsonify({"ok": False, "error": "no_frame"}), 400
#
#    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
#    bbox, descriptor = _face_extract_primary(gray)
#    if bbox is None or descriptor is None:
#        return jsonify({"ok": False, "error": "no_face_detected"}), 400
#
#    _save_kauan_face_descriptor(descriptor)
#    with _face_lock:
#        _face_runtime["known"] = True
#        _face_runtime["label"] = "e o Kauan"
#        _face_runtime["distance"] = 0.0
#        _face_runtime["last_seen_at"] = _now()
#        _face_runtime["bbox"] = bbox
#        _face_runtime["next_scan_at"] = 0.0
#
#    return jsonify({"ok": True, "face": _face_status_snapshot()})


@app.route("/api/control", methods=["POST"])
def route_control():
    payload = request.get_json(silent=True) or {}
    mode = str(payload.get("mode", "manual")).strip().lower()
    if mode not in ("manual", "auto"):
        mode = "manual"

    try:
        speed = int(payload.get("speed", 0))
    except Exception:
        speed = 0
    try:
        steering = int(payload.get("steering", 0))
    except Exception:
        steering = 0

    source = str(payload.get("source", "web")).strip() or "web"
    speed = _clamp(speed, -100, 100)
    steering = _clamp(steering, -100, 100)

    with _control_lock:
        _control_state["mode"] = mode
        _control_state["manual_speed"] = speed
        _control_state["manual_steering"] = steering
        _control_state["updated_at"] = _now()
        _control_state["last_source"] = source

    # Resposta minima - Connection: close para nao bloquear pool de conexoes do browser.
    resp = jsonify({"ok": True})
    resp.headers["Connection"] = "close"
    return resp


@app.route("/api/settings", methods=["GET", "POST"])
def route_settings():
    if request.method == "POST":
        payload = request.get_json(silent=True) or {}
        if "speed_limit_pct" in payload:
            try:
                pct = max(0, min(100, int(payload["speed_limit_pct"])))
            except Exception:
                return jsonify({"ok": False, "error": "invalid speed_limit_pct"}), 400
            with _settings_lock:
                _settings["speed_limit_pct"] = pct
    with _settings_lock:
        return jsonify({"ok": True, "settings": dict(_settings)})


@app.route("/api/stop", methods=["POST"])
def route_stop():
    _send_to_arduino(0, 0, "manual", "stop")
    with _control_lock:
        _control_state["mode"] = "manual"
        _control_state["manual_speed"] = 0
        _control_state["manual_steering"] = 0
        _control_state["active_speed"] = 0
        _control_state["active_steering"] = 0
        _control_state["updated_at"] = _now()
        _control_state["last_source"] = "stop"
        _control_state["arduino_sent"] = True
        _control_state["arduino_error"] = None

    resp = jsonify({"ok": True})
    resp.headers["Connection"] = "close"
    return resp


def create_server(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> AGVServerController:
    return AGVServerController(host, port)


def serve(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> None:
    controller = create_server(host=host, port=port)
    controller.start()
    try:
        while True:
            time.sleep(1.0)
    except KeyboardInterrupt:
        pass
    finally:
        controller.stop()


def main() -> int:
    serve()
    return 0


HTML_PAGE = r'''<!doctype html>
<html lang="pt-BR">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>agv</title>
  <style>
    :root {
      --bg: #f3efe5;
      --panel: rgba(255, 252, 247, 0.9);
      --panel-strong: #fffdf8;
      --ink: #17202a;
      --muted: #5f6b76;
      --accent: #d95d39;
      --ok: #2f855a;
      --danger: #c53030;
      --line: rgba(23, 32, 42, 0.12);
      --shadow: 0 18px 44px rgba(53, 39, 24, 0.12);
      --radius: 22px;
      --mono: "DejaVu Sans Mono", "Liberation Mono", monospace;
      --sans: "Trebuchet MS", "Verdana", sans-serif;
    }

    * { box-sizing: border-box; }

    body {
      margin: 0;
      font-family: var(--sans);
      color: var(--ink);
      background:
        radial-gradient(circle at top left, rgba(217, 93, 57, 0.20), transparent 34%),
        radial-gradient(circle at bottom right, rgba(47, 133, 90, 0.14), transparent 28%),
        linear-gradient(135deg, #f7f2e8 0%, #ece6d9 100%);
      min-height: 100vh;
    }

    .shell {
      width: min(1400px, calc(100vw - 24px));
      margin: 20px auto;
      display: grid;
      gap: 18px;
    }

    .hero,
    .panel {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: var(--radius);
      box-shadow: var(--shadow);
      backdrop-filter: blur(10px);
    }

    .hero {
      padding: 22px;
      display: grid;
      gap: 16px;
    }

    .hero-top {
      display: flex;
      justify-content: space-between;
      align-items: flex-start;
      gap: 16px;
      flex-wrap: wrap;
    }

    h1 {
      margin: 0;
      font-size: clamp(28px, 4vw, 44px);
      line-height: 0.95;
      letter-spacing: 0.04em;
      text-transform: uppercase;
    }

    .hero p {
      margin: 6px 0 0;
      color: var(--muted);
      max-width: 700px;
    }

    .chips {
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
    }

        .access-row {
            display: flex;
            flex-wrap: wrap;
            align-items: center;
            gap: 10px;
            padding: 10px 12px;
            border-radius: 14px;
            background: rgba(23, 32, 42, 0.05);
            border: 1px solid rgba(23, 32, 42, 0.1);
        }

        .access-link {
            flex: 1 1 260px;
            font-family: var(--mono);
            font-size: 13px;
            color: var(--muted);
            word-break: break-all;
        }

        .copy-link-button {
            padding: 10px 14px;
            border-radius: 12px;
            background: #1f6f5c;
            font-size: 13px;
            letter-spacing: 0.04em;
            text-transform: uppercase;
        }

    .chip {
      padding: 10px 14px;
      border-radius: 999px;
      background: rgba(23, 32, 42, 0.06);
      border: 1px solid rgba(23, 32, 42, 0.08);
      font-size: 13px;
      letter-spacing: 0.04em;
      text-transform: uppercase;
    }

    .grid {
      display: grid;
      gap: 18px;
      grid-template-columns: minmax(0, 1.7fr) minmax(320px, 0.9fr);
    }

    .video-stack {
      display: grid;
      gap: 18px;
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }

    .panel {
      padding: 18px;
    }

    .panel h2 {
      margin: 0 0 14px;
      font-size: 16px;
      letter-spacing: 0.08em;
      text-transform: uppercase;
    }

    .stream {
      display: block;
      width: 100%;
      aspect-ratio: 4 / 3;
      object-fit: cover;
      border-radius: 18px;
      background: #0f1418;
      border: 1px solid rgba(255, 255, 255, 0.08);
    }

    .caption {
      margin-top: 10px;
      color: var(--muted);
      font-size: 14px;
    }

    .side {
      display: grid;
      gap: 18px;
    }

    .mode-row,
    .action-row {
      display: grid;
      grid-template-columns: repeat(2, 1fr);
      gap: 10px;
    }

    button {
      appearance: none;
      border: 0;
      border-radius: 16px;
      padding: 14px 16px;
      font: inherit;
      cursor: pointer;
      background: #182028;
      color: #fff;
      transition: transform 120ms ease, filter 120ms ease, background 120ms ease;
    }

    button:hover { filter: brightness(1.05); }
    button:active,
    button.is-active { transform: translateY(1px) scale(0.99); }

    .mode-button[data-mode="manual"] { background: #224e3c; }
    .mode-button[data-mode="auto"] { background: #8a3f16; }
    .mode-button.is-selected { outline: 3px solid rgba(255, 255, 255, 0.45); }

    .stop-button {
      background: var(--danger);
      grid-column: span 2;
      font-weight: 700;
      letter-spacing: 0.05em;
      text-transform: uppercase;
    }

    .pad {
      display: grid;
      gap: 10px;
      grid-template-columns: repeat(3, 1fr);
      align-items: stretch;
    }

    .pad button {
      min-height: 82px;
      font-size: 24px;
      font-weight: 700;
      background: var(--panel-strong);
      color: var(--ink);
      border: 1px solid rgba(23, 32, 42, 0.12);
    }

    .pad .blank {
      visibility: hidden;
      pointer-events: none;
    }

        .manual-layout {
            display: grid;
            gap: 12px;
        }

        .joystick-wrap {
            display: grid;
            justify-items: center;
            gap: 10px;
            padding: 8px 0 4px;
        }

        .joystick-base {
            width: min(72vw, 280px);
            aspect-ratio: 1 / 1;
            border-radius: 50%;
            position: relative;
            touch-action: none;
            background:
                radial-gradient(circle at 30% 30%, rgba(255, 255, 255, 0.6), rgba(255, 255, 255, 0.08)),
                linear-gradient(150deg, rgba(23, 32, 42, 0.14), rgba(23, 32, 42, 0.06));
            border: 1px solid rgba(23, 32, 42, 0.14);
            box-shadow: inset 0 0 0 10px rgba(255, 255, 255, 0.12);
        }

        .joystick-ring {
            position: absolute;
            inset: 12%;
            border-radius: 50%;
            border: 2px dashed rgba(23, 32, 42, 0.22);
            pointer-events: none;
        }

        .joystick-knob {
            position: absolute;
            width: 34%;
            aspect-ratio: 1 / 1;
            left: 33%;
            top: 33%;
            border-radius: 50%;
            background: linear-gradient(160deg, #f5f3ef, #d8d0c2);
            border: 1px solid rgba(23, 32, 42, 0.16);
            box-shadow: 0 6px 14px rgba(23, 32, 42, 0.24);
            pointer-events: none;
            transition: transform 50ms linear;
        }

        .joystick-readout {
            font-family: var(--mono);
            color: var(--muted);
            font-size: 13px;
            letter-spacing: 0.03em;
            text-transform: uppercase;
        }

    .telemetry {
      display: grid;
      gap: 10px;
    }

    .metric {
      display: flex;
      justify-content: space-between;
      gap: 12px;
      padding: 10px 12px;
      border-radius: 14px;
      background: rgba(23, 32, 42, 0.04);
      font-size: 14px;
    }

    .metric strong {
      font-family: var(--mono);
      font-weight: 700;
    }

    .footer-note {
      color: var(--muted);
      font-size: 13px;
      line-height: 1.5;
    }

        .face-tools {
            display: grid;
            gap: 12px;
        }

        .face-field {
            display: grid;
            gap: 6px;
            font-size: 13px;
            color: var(--muted);
        }

        .face-field span {
            letter-spacing: 0.04em;
            text-transform: uppercase;
        }

        .face-field input,
        .face-field select {
            width: 100%;
            border: 1px solid var(--line);
            border-radius: 12px;
            padding: 11px 12px;
            font: inherit;
            color: var(--ink);
            background: rgba(255, 255, 255, 0.72);
        }

        .face-field input[type=file] {
            padding: 9px 12px;
        }

        .face-register-button {
            background: #7d4d16;
            text-transform: uppercase;
            letter-spacing: 0.04em;
            font-weight: 700;
        }

        .face-helper {
            margin: 0;
            color: var(--muted);
            font-size: 13px;
            line-height: 1.4;
        }

    @media (max-width: 1100px) {
      .grid { grid-template-columns: 1fr; }
    }

    @media (max-width: 760px) {
      .video-stack { grid-template-columns: 1fr; }
      .shell { width: min(100vw - 16px, 1400px); margin: 8px auto 20px; }
      .hero, .panel { border-radius: 18px; }
      .pad button { min-height: 72px; }
    }

    /* Modo escuro */
    body.dark {
      --bg: #0d1117;
      --panel: rgba(22, 28, 36, 0.95);
      --panel-strong: #161b22;
      --ink: #e6edf3;
      --muted: #8b949e;
      --line: rgba(255, 255, 255, 0.1);
      --shadow: 0 18px 44px rgba(0, 0, 0, 0.5);
    }
    body.dark {
      background:
        radial-gradient(circle at top left, rgba(217, 93, 57, 0.10), transparent 34%),
        radial-gradient(circle at bottom right, rgba(47, 133, 90, 0.07), transparent 28%),
        linear-gradient(135deg, #0d1117 0%, #161b22 100%);
    }
    body.dark .pad button {
      background: #21262d;
      color: var(--ink);
      border-color: rgba(255, 255, 255, 0.12);
    }
        body.dark .copy-link-button { background: #216e5f; }
        body.dark .access-row {
            background: rgba(255, 255, 255, 0.03);
            border-color: rgba(255, 255, 255, 0.12);
        }
        body.dark .joystick-base {
            background:
                radial-gradient(circle at 30% 30%, rgba(255, 255, 255, 0.15), rgba(255, 255, 255, 0.03)),
                linear-gradient(150deg, rgba(0, 0, 0, 0.35), rgba(255, 255, 255, 0.02));
            border-color: rgba(255, 255, 255, 0.14);
            box-shadow: inset 0 0 0 10px rgba(255, 255, 255, 0.05);
        }
        body.dark .joystick-ring { border-color: rgba(255, 255, 255, 0.22); }
        body.dark .joystick-knob {
            background: linear-gradient(160deg, #2d333b, #22272e);
            border-color: rgba(255, 255, 255, 0.16);
        }
    body.dark button { background: #21262d; }
    body.dark .mode-button[data-mode="manual"] { background: #1a3a2c; }
    body.dark .mode-button[data-mode="auto"] { background: #4a2010; }
    body.dark .stop-button { background: var(--danger); }
        body.dark .face-field input,
        body.dark .face-field select {
            background: rgba(255, 255, 255, 0.04);
            color: var(--ink);
        }
        body.dark .face-register-button { background: #8b5a1d; }

    /* Toggle switch */
    .toggle-switch {
      position: relative;
      display: inline-block;
      width: 48px;
      height: 26px;
      flex-shrink: 0;
      cursor: pointer;
    }
    .toggle-switch input { opacity: 0; width: 0; height: 0; position: absolute; }
    .toggle-track {
      position: absolute;
      inset: 0;
      border-radius: 999px;
      background: rgba(23, 32, 42, 0.15);
      border: 1px solid var(--line);
      transition: background 200ms;
    }
    .toggle-thumb {
      position: absolute;
      width: 20px; height: 20px;
      border-radius: 50%;
      background: var(--muted);
      top: 2px; left: 2px;
      transition: transform 200ms, background 200ms;
      pointer-events: none;
    }
    .toggle-switch input:checked ~ .toggle-track { background: var(--ok); border-color: var(--ok); }
    .toggle-switch input:checked ~ .toggle-thumb { transform: translateX(22px); background: #fff; }

    /* Slider de velocidade */
    input[type=range].speed-slider {
      -webkit-appearance: none;
      appearance: none;
      width: 100%;
      height: 6px;
      border-radius: 999px;
      background: rgba(23, 32, 42, 0.15);
      outline: none;
      cursor: pointer;
      border: 1px solid var(--line);
    }
    input[type=range].speed-slider::-webkit-slider-thumb {
      -webkit-appearance: none;
      appearance: none;
      width: 22px; height: 22px;
      border-radius: 50%;
      background: var(--accent);
      cursor: pointer;
      border: 2px solid #fff;
      box-shadow: 0 2px 6px rgba(0,0,0,0.2);
    }
    input[type=range].speed-slider::-moz-range-thumb {
      width: 22px; height: 22px;
      border-radius: 50%;
      background: var(--accent);
      border: 2px solid #fff;
      cursor: pointer;
    }
  </style>
</head>
<body>
  <main class="shell">
    <section class="hero">
            <div id="net-banner" hidden style="background:#7a1a1a;color:#fff;border-radius:12px;padding:10px 16px;font-size:14px;font-weight:600;letter-spacing:0.03em;text-align:center;"></div>
      <div class="hero-top">
        <div>
          <h1>agv</h1>
          <p>Controle pelo navegador com video ao vivo do Kinect, mapa de profundidade e modo autonomo simples por distancia.</p>
        </div>
        <div class="chips">
          <div class="chip" id="chip-mode">modo manual</div>
          <div class="chip" id="chip-kinect">kinect offline</div>
                    <div class="chip" id="chip-zed">zed offline</div>
          <div class="chip" id="chip-serial">serial desconectada</div>
          <div class="chip" id="chip-face">face sem cadastro</div>
        </div>
      </div>
            <div class="access-row">
                <span class="access-link" id="access-link">Link para outro dispositivo: carregando...</span>
                <button class="copy-link-button" id="btn-copy-link" type="button">Copiar link</button>
            </div>
    </section>

    <section class="grid">
      <div class="video-stack">
        <article class="panel">
          <h2>RGB Kinect</h2>
          <img class="stream" src="/video" alt="Video RGB do Kinect">
          <div class="caption">Imagem ao vivo usada para acompanhar o agv.</div>
        </article>

        <article class="panel">
          <h2>Depth Kinect</h2>
          <img class="stream" src="/depth_map" alt="Mapa de profundidade do Kinect">
          <div class="caption">Mapa de distancia usado no modo autonomo.</div>
        </article>

                <article class="panel">
                    <h2>Webcam ZED</h2>
                    <img class="stream" src="/zed" alt="Video da webcam ZED">
                    <div class="caption">Camera extra para monitorar a traseira do agv.</div>
                </article>
      </div>

      <div class="side">
        <section class="panel">
          <h2>Modo</h2>
          <div class="mode-row">
            <button class="mode-button is-selected" data-mode="manual" id="btn-manual">Manual</button>
            <button class="mode-button" data-mode="auto" id="btn-auto">Autonomo</button>
          </div>
          <div class="action-row" style="margin-top:10px;">
            <button id="btn-reconnect">Reconectar Kinect</button>
                        <button id="btn-stabilization">Estabilizacao ON</button>
            <button id="btn-tilt-up">Tilt +</button>
            <button id="btn-tilt-center">Centralizar</button>
            <button id="btn-tilt-down">Tilt -</button>
            <button class="stop-button" id="btn-stop">Parada total</button>
          </div>
        </section>

                <section class="panel">
                    <h2>Rostos</h2>
                    <div class="face-tools">
                        <label class="face-field">
                            <span>Pessoa para procurar na ZED</span>
                            <select id="face-select">
                                <option value="">Nenhuma pessoa selecionada</option>
                            </select>
                        </label>
                        <label class="face-field">
                            <span>Nome do rosto</span>
                            <input id="face-name-input" type="text" maxlength="60" placeholder="Ex.: Kauan">
                        </label>
                        <label class="face-field">
                            <span>Imagem para cadastro</span>
                            <input id="face-file-input" type="file" accept="image/*">
                        </label>
                        <button class="face-register-button" id="btn-face-register" type="button">Registrar rosto</button>
                        <p class="face-helper" id="face-form-status">Escolha uma pessoa no seletor. O quadrado so aparece na ZED quando o rosto escolhido for encontrado.</p>
                    </div>
                </section>

        <section class="panel">
          <h2>Controle Manual</h2>
                    <div class="manual-layout">
                        <div class="pad" id="keyboard-pad">
                            <button class="blank" aria-hidden="true"></button>
                            <button data-key="w">W</button>
                            <button class="blank" aria-hidden="true"></button>
                            <button data-key="a">A</button>
                            <button data-key="s">S</button>
                            <button data-key="d">D</button>
                        </div>
                        <div class="joystick-wrap" id="mobile-joystick" hidden>
                            <div class="joystick-base" id="joystick-base" aria-label="Controle por stick virtual">
                                <div class="joystick-ring"></div>
                                <div class="joystick-knob" id="joystick-knob"></div>
                            </div>
                            <div class="joystick-readout" id="joystick-readout">Stick: parado</div>
                        </div>
          </div>
                    <p class="footer-note" id="control-hint">No celular aparece stick virtual. No PC, use W A S D, setas ou E como esquerda.</p>
        </section>

        <section class="panel">
          <h2>Telemetria</h2>
          <div class="telemetry">
            <div class="metric"><span>Distancia frontal</span><strong id="distance">--</strong></div>
            <div class="metric"><span>Esquerda</span><strong id="left-clearance">--</strong></div>
            <div class="metric"><span>Centro</span><strong id="center-clearance">--</strong></div>
            <div class="metric"><span>Direita</span><strong id="right-clearance">--</strong></div>
            <div class="metric"><span>Comando ativo</span><strong id="active-command">--</strong></div>
            <div class="metric"><span>Autonomo</span><strong id="auto-reason">--</strong></div>
            <div class="metric"><span>Kinect</span><strong id="kinect-status">--</strong></div>
            <div class="metric"><span>Webcam ZED</span><strong id="zed-status">--</strong></div>
            <div class="metric"><span>Arduino</span><strong id="serial-status">--</strong></div>
            <div class="metric"><span>Face</span><strong id="face-status">--</strong></div>
            <div class="metric"><span>FPS</span><strong id="fps-status">--</strong></div>
          </div>
        </section>

        <section class="panel">
          <h2>Configuracoes</h2>
          <div class="telemetry">
            <div class="metric">
              <span>Modo escuro</span>
              <label class="toggle-switch" title="Alternar modo escuro">
                <input type="checkbox" id="dark-mode-toggle">
                <span class="toggle-track"></span>
                <span class="toggle-thumb"></span>
              </label>
            </div>
            <div class="metric" style="flex-direction:column;align-items:stretch;gap:10px;">
              <div style="display:flex;justify-content:space-between;align-items:center;">
                <span>Velocidade maxima</span>
                <strong id="speed-limit-display">100%</strong>
              </div>
              <input type="range" class="speed-slider" id="speed-limit-slider" min="0" max="100" value="100" step="5">
            </div>
          </div>
        </section>
      </div>
    </section>
  </main>

  <script>
    const controlState = {
      mode: "manual",
      pressed: { w: false, a: false, s: false, d: false },
            mobile: { x: 0, y: 0, active: false },
      lastSpeed: 0,
      lastSteering: 0,
    };

        const uiState = {
            isMobileControl: false,
            shareLink: "",
            selectedFaceName: "",
            knownFaces: [],
                        faceBeepArmed: true,
                        faceLastLabel: "",
                        audioReady: false,
                        audioContext: null,
        };

        function ensureAudioReady() {
            if (uiState.audioReady) return;
            const AudioCtx = window.AudioContext || window.webkitAudioContext;
            if (!AudioCtx) return;
            try {
                uiState.audioContext = new AudioCtx();
                uiState.audioReady = true;
            } catch (error) {
                console.warn("AudioContext indisponivel", error);
            }
        }

        function playFaceBeep() {
            if (!uiState.audioReady || !uiState.audioContext) return;
            try {
                const ctx = uiState.audioContext;
                if (ctx.state === "suspended") {
                    ctx.resume().catch(() => {});
                }
                const now = ctx.currentTime;
                const master = ctx.createGain();
                master.gain.setValueAtTime(0.0001, now);
                master.gain.exponentialRampToValueAtTime(0.12, now + 0.015);
                master.gain.exponentialRampToValueAtTime(0.0001, now + 0.22);
                master.connect(ctx.destination);

                const toneA = ctx.createOscillator();
                toneA.type = "sine";
                toneA.frequency.setValueAtTime(900, now);
                toneA.frequency.exponentialRampToValueAtTime(1250, now + 0.10);
                toneA.connect(master);
                toneA.start(now);
                toneA.stop(now + 0.11);

                const toneB = ctx.createOscillator();
                toneB.type = "triangle";
                toneB.frequency.setValueAtTime(1250, now + 0.11);
                toneB.frequency.exponentialRampToValueAtTime(1600, now + 0.22);
                toneB.connect(master);
                toneB.start(now + 0.11);
                toneB.stop(now + 0.23);
            } catch (error) {
                console.warn("Falha ao tocar beep de reconhecimento", error);
            }
        }

    function fmtMeters(value) {
      return typeof value === "number" ? value.toFixed(2) + " m" : "--";
    }

    function mapKey(raw) {
      const key = (raw || "").toLowerCase();
      if (key === "w" || key === "arrowup") return "w";
      if (key === "a" || key === "e" || key === "arrowleft") return "a";
      if (key === "s" || key === "arrowdown") return "s";
      if (key === "d" || key === "arrowright") return "d";
      return null;
    }

        function isTypingTarget(event) {
            const element = event && event.target;
            if (!element) return false;
            const tag = (element.tagName || "").toLowerCase();
            return !!element.isContentEditable || tag === "input" || tag === "textarea" || tag === "select";
        }

    function deriveManualCommand() {
            if (uiState.isMobileControl) {
                const deadZone = 0.08;
                const x = controlState.mobile.x;
                const y = controlState.mobile.y;
                const speed = Math.abs(y) < deadZone ? 0 : Math.round(-y * 100);
                const steering = Math.abs(x) < deadZone ? 0 : Math.round(x * 100);
                return { speed, steering };
            }

      let speed = 0;
      let steering = 0;

      if (controlState.pressed.w && !controlState.pressed.s) speed = 100;
      if (controlState.pressed.s && !controlState.pressed.w) speed = -100;
      if (controlState.pressed.a && !controlState.pressed.d) steering = -70;
      if (controlState.pressed.d && !controlState.pressed.a) steering = 70;

      return { speed, steering };
    }

    async function postJson(url, payload, signal) {
      const response = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload || {}),
        signal: signal || null,
      });
      return response.json();
    }

        function updateFaceSelector(face) {
            const select = document.getElementById("face-select");
            if (!select) return;

            const names = Array.isArray(face.names) ? face.names : [];
            const selectedName = typeof face.selected_name === "string" ? face.selected_name : uiState.selectedFaceName;
            uiState.knownFaces = names.slice();
            uiState.selectedFaceName = selectedName || "";

            const currentOptions = [""];
            names.forEach((name) => currentOptions.push(name));
            const previousValue = select.value;
            const shouldRebuild = select.options.length !== currentOptions.length || currentOptions.some((value, index) => (select.options[index] || {}).value !== value);

            if (shouldRebuild) {
                select.innerHTML = "";
                const emptyOption = document.createElement("option");
                emptyOption.value = "";
                emptyOption.textContent = "Nenhuma pessoa selecionada";
                select.appendChild(emptyOption);
                names.forEach((name) => {
                    const option = document.createElement("option");
                    option.value = name;
                    option.textContent = name;
                    select.appendChild(option);
                });
            }

            const nextValue = names.includes(uiState.selectedFaceName) ? uiState.selectedFaceName : "";
            select.value = nextValue;
            if (!nextValue && previousValue && !names.includes(previousValue)) {
                uiState.selectedFaceName = "";
            }
        }

        function setFaceFormStatus(message) {
            const status = document.getElementById("face-form-status");
            if (status) status.textContent = message;
        }

        async function selectFace(name) {
            try {
                const response = await postJson("/api/face/select", { name: name || "" });
                if (!response.ok) {
                    alert("Nao foi possivel selecionar esse rosto.");
                    return;
                }
                updateFaceSelector(response.face || {});
                setFaceFormStatus(response.face && response.face.selected_name
                    ? `Procurando ${response.face.selected_name} na webcam ZED.`
                    : "Nenhuma pessoa selecionada para a webcam ZED.");
            } catch (error) {
                console.error(error);
                alert("Falha ao selecionar o rosto.");
            }
        }

    // Cancela qualquer fetch de controle em voo antes de mandar novo.
    let _controlAbort = null;

    async function sendManualCommand(source) {
      if (controlState.mode !== "manual") return;
      const next = deriveManualCommand();
      if (next.speed === controlState.lastSpeed && next.steering === controlState.lastSteering) return;

      controlState.lastSpeed = next.speed;
      controlState.lastSteering = next.steering;

      // Cancela request anterior que ainda não terminou
      if (_controlAbort) { try { _controlAbort.abort(); } catch(e) {} }
      _controlAbort = new AbortController();
      const signal = _controlAbort.signal;

      // Timeout de 1.5 s para não acumular requests em fila
      const timeoutId = setTimeout(() => { try { _controlAbort.abort(); } catch(e) {} }, 1500);
      try {
        await postJson("/api/control", {
          mode: "manual",
          speed: next.speed,
          steering: next.steering,
          source: source || "site-manual",
        }, signal);
      } catch (error) {
        if (error.name !== "AbortError") console.error(error);
      } finally {
        clearTimeout(timeoutId);
      }
    }

    async function setMode(mode) {
      controlState.mode = mode;
      if (mode === "auto") {
        controlState.pressed = { w: false, a: false, s: false, d: false };
        controlState.lastSpeed = 0;
        controlState.lastSteering = 0;
      }

      document.querySelectorAll(".mode-button").forEach((button) => {
        button.classList.toggle("is-selected", button.dataset.mode === mode);
      });

      try {
        await postJson("/api/control", {
          mode,
          speed: 0,
          steering: 0,
          source: "site-mode",
        });
      } catch (error) {
        console.error(error);
      }
    }

    function setPressed(key, value, source) {
            if (uiState.isMobileControl) return;
      if (!key) return;
      controlState.pressed[key] = value;
      sendManualCommand(source);
      document.querySelectorAll("[data-key]").forEach((button) => {
        button.classList.toggle("is-active", controlState.pressed[button.dataset.key]);
      });
    }

    async function emergencyStop() {
      controlState.mode = "manual";
      controlState.pressed = { w: false, a: false, s: false, d: false };
            controlState.mobile = { x: 0, y: 0, active: false };
      controlState.lastSpeed = 0;
      controlState.lastSteering = 0;
      document.querySelectorAll("[data-key]").forEach((button) => button.classList.remove("is-active"));
      document.querySelectorAll(".mode-button").forEach((button) => {
        button.classList.toggle("is-selected", button.dataset.mode === "manual");
      });
      try {
        await postJson("/api/stop", {});
      } catch (error) {
        console.error(error);
      }
    }

        function detectMobileControl() {
            const coarsePointer = window.matchMedia && window.matchMedia("(pointer: coarse)").matches;
            const mobileUA = /android|iphone|ipad|ipod|mobile/i.test(navigator.userAgent || "");
            return coarsePointer || mobileUA;
        }

        function setupControlSurface() {
            uiState.isMobileControl = detectMobileControl();
            const keyboardPad = document.getElementById("keyboard-pad");
            const mobileJoystick = document.getElementById("mobile-joystick");
            const hint = document.getElementById("control-hint");
            keyboardPad.hidden = uiState.isMobileControl;
            mobileJoystick.hidden = !uiState.isMobileControl;
            hint.textContent = uiState.isMobileControl
                ? "Stick virtual ativo. Arraste o centro para mover o agv e solte para parar."
                : "No PC, use W A S D, setas ou E como esquerda.";
            if (uiState.isMobileControl) {
                controlState.pressed = { w: false, a: false, s: false, d: false };
                document.querySelectorAll("[data-key]").forEach((button) => button.classList.remove("is-active"));
            } else {
                controlState.mobile = { x: 0, y: 0, active: false };
            }
            sendManualCommand("site-surface");
        }

        function renderShareLink() {
            const label = document.getElementById("access-link");
            label.textContent = "Link para outro dispositivo: " + (uiState.shareLink || "indisponivel");
        }

        async function resolveShareLink() {
            const host = (location.hostname || "").toLowerCase();
            const loopbackHosts = new Set(["localhost", "127.0.0.1", "::1"]);
            if (!loopbackHosts.has(host)) {
                uiState.shareLink = location.origin + "/";
                renderShareLink();
                return;
            }

            try {
                const response = await fetch("/api/access_link", { cache: "no-store" });
                const payload = await response.json();
                uiState.shareLink = payload.url || (location.origin + "/");
            } catch (error) {
                console.error(error);
                uiState.shareLink = location.origin + "/";
            }
            renderShareLink();
        }

        async function copyShareLink() {
            if (!uiState.shareLink) {
                await resolveShareLink();
            }
            const button = document.getElementById("btn-copy-link");
            const originalText = button.textContent;
            try {
                if (navigator.clipboard && navigator.clipboard.writeText) {
                    await navigator.clipboard.writeText(uiState.shareLink);
                } else {
                    const tempInput = document.createElement("textarea");
                    tempInput.value = uiState.shareLink;
                    tempInput.style.position = "fixed";
                    tempInput.style.opacity = "0";
                    document.body.appendChild(tempInput);
                    tempInput.select();
                    document.execCommand("copy");
                    document.body.removeChild(tempInput);
                }
                button.textContent = "Copiado";
            } catch (error) {
                console.error(error);
                button.textContent = "Falhou";
            }
            setTimeout(() => {
                button.textContent = originalText;
            }, 1200);
        }

    async function reconnectKinect() {
      try {
        await postJson("/api/kinect/reconnect", {});
      } catch (error) {
        console.error(error);
      }
    }

    async function sendTilt(angle) {
      try {
        const response = await postJson("/api/kinect/tilt", { angle });
        if (!response.ok) {
          alert("Nao foi possivel mover o tilt do Kinect agora.");
        }
      } catch (error) {
        console.error(error);
        alert("Falha ao enviar comando de tilt.");
      }
    }

        function applyStabilizationButton(enabled) {
            const btn = document.getElementById("btn-stabilization");
            if (!btn) return;
            btn.textContent = enabled ? "Estabilizacao ON" : "Estabilizacao OFF";
            btn.style.background = enabled ? "#1f6f5c" : "#7a1a1a";
        }

        async function setStabilization(enabled) {
            try {
                const response = await postJson("/api/kinect/stabilization", { enabled: !!enabled });
                applyStabilizationButton(!!response.enabled);
            } catch (error) {
                console.error(error);
                alert("Falha ao ajustar estabilizacao do Kinect.");
            }
        }

        async function toggleStabilization() {
            const btn = document.getElementById("btn-stabilization");
            const enabledNow = btn && btn.textContent.includes("ON");
            await setStabilization(!enabledNow);
        }

        async function registerFace() {
            const button = document.getElementById("btn-face-register");
            const nameInput = document.getElementById("face-name-input");
            const fileInput = document.getElementById("face-file-input");
            const faceName = (nameInput.value || "").trim();
            const imageFile = fileInput.files && fileInput.files[0];

            if (!faceName) {
                alert("Digite o nome da pessoa antes de registrar.");
                nameInput.focus();
                return;
            }
            if (!imageFile) {
                alert("Escolha uma imagem para cadastrar o rosto.");
                fileInput.focus();
                return;
            }

            const form = new FormData();
            form.append("name", faceName);
            form.append("image", imageFile);

            button.disabled = true;
            setFaceFormStatus("Enviando imagem e cadastrando rosto...");
            try {
                const response = await fetch("/api/face/register_face", {
                    method: "POST",
                    body: form,
                });
                const payload = await response.json();
                if (!response.ok || !payload.ok) {
                    const errorCode = payload && payload.error ? payload.error : "erro desconhecido";
                    alert("Nao foi possivel cadastrar o rosto: " + errorCode);
                    setFaceFormStatus("Falha ao cadastrar rosto.");
                    return;
                }

                nameInput.value = "";
                fileInput.value = "";
                updateFaceSelector(payload.face || {});
                setFaceFormStatus(`Rosto de ${payload.name} salvo em ${payload.image_path}.`);
            } catch (error) {
                console.error(error);
                setFaceFormStatus("Falha ao cadastrar rosto.");
                alert("Falha ao cadastrar rosto.");
            } finally {
                button.disabled = false;
            }
        }

    function updateTelemetry(snapshot) {
      const state = snapshot.state || {};
      const agv = snapshot.agv || {};
      const autopilot = snapshot.autopilot || {};
      const arduino = snapshot.arduino || {};
            const kinect = snapshot.kinect || {};
                        const zed = snapshot.zed || {};
      const face = snapshot.face || {};

      document.getElementById("chip-mode").textContent = "modo " + (agv.mode || "manual");
      document.getElementById("chip-kinect").textContent = state.kinect_ok ? "kinect online" : "kinect offline";
            document.getElementById("chip-zed").textContent = zed.online ? "zed online" : "zed offline";
      document.getElementById("chip-serial").textContent = arduino.connected ? "serial conectada" : "serial desconectada";
      if (!face.template_loaded) {
        document.getElementById("chip-face").textContent = "face sem cadastro";
      } else if (face.known && face.label) {
        document.getElementById("chip-face").textContent = face.label;
            } else if (face.selected_name) {
                document.getElementById("chip-face").textContent = `procurando ${face.selected_name}`;
      } else {
                document.getElementById("chip-face").textContent = "face aguardando selecao";
      }

            updateFaceSelector(face);

      document.getElementById("distance").textContent = fmtMeters(state.distance_m);
      document.getElementById("left-clearance").textContent = fmtMeters(state.left_clearance_m);
      document.getElementById("center-clearance").textContent = fmtMeters(state.center_clearance_m);
      document.getElementById("right-clearance").textContent = fmtMeters(state.right_clearance_m);
      document.getElementById("active-command").textContent = `${agv.active_speed ?? 0} / ${agv.active_steering ?? 0}`;
      document.getElementById("auto-reason").textContent = autopilot.last_reason || "--";
      document.getElementById("kinect-status").textContent = state.kinect_ok
        ? `ok | atraso ${state.frame_age_s ?? "--"}s | tilt ${state.tilt_deg ?? "--"} | alvo ${state.tilt_target_deg ?? "--"}`
        : (state.kinect_error || "offline");
            document.getElementById("zed-status").textContent = zed.online
                ? `ok | atraso ${zed.frame_age_s ?? "--"}s | ${zed.fps ?? 0} fps`
                : (zed.error || "offline");
      document.getElementById("serial-status").textContent = arduino.connected ? `${arduino.port || "usb"} | ${arduino.last_command || "--"}` : (arduino.last_error || "desconectada");
            document.getElementById("face-status").textContent = face.known
                ? `${face.label || "rosto"} | dist ${typeof face.distance === "number" ? face.distance.toFixed(3) : "--"}`
                : (face.selected_name ? `procurando ${face.selected_name}` : "selecione uma pessoa");
      document.getElementById("fps-status").textContent = `${state.fps_rgb || 0} rgb | ${state.fps_depth || 0} depth`;
    applyStabilizationButton(!!kinect.stabilization_enabled);

            if (!face.template_loaded) {
                setFaceFormStatus("Cadastre uma imagem com nome para procurar alguem na ZED.");
            } else if (face.known && face.label) {
                setFaceFormStatus(`Rosto encontrado na ZED: ${face.label}.`);
            } else if (face.selected_name) {
                setFaceFormStatus(`Procurando ${face.selected_name} na webcam ZED.`);
            }

            const faceKnown = !!(face && face.known && face.label);
            const faceLabel = faceKnown ? String(face.label) : "";
            if (faceKnown && (uiState.faceBeepArmed || uiState.faceLastLabel !== faceLabel)) {
                playFaceBeep();
                uiState.faceBeepArmed = false;
            }
            if (!faceKnown) {
                uiState.faceBeepArmed = true;
            }
            uiState.faceLastLabel = faceLabel;

      if ((agv.mode || "manual") !== controlState.mode) {
        controlState.mode = agv.mode || "manual";
        document.querySelectorAll(".mode-button").forEach((button) => {
          button.classList.toggle("is-selected", button.dataset.mode === controlState.mode);
        });
      }
    }

    async function refreshStatus() {
            const t0 = Date.now();
            const ctrl = new AbortController();
            const tid = setTimeout(() => ctrl.abort(), 2000); // timeout 2s
            try {
                const response = await fetch("/api/status", { cache: "no-store", signal: ctrl.signal });
                const snapshot = await response.json();
                clearTimeout(tid);
                const rtt = Date.now() - t0;
                updateTelemetry(snapshot);
                _statusFail = 0;
                // Polling adaptativo: quanto mais rapido o RTT, mais frequente o poll.
                _statusInterval = rtt < 300 ? 500 : rtt < 800 ? 900 : 1800;
                _updateNetBanner(rtt);
            } catch (error) {
                clearTimeout(tid);
                _statusFail++;
                _statusInterval = Math.min(3000, 500 + _statusFail * 500);
                _updateNetBanner(null);
                console.warn("status timeout/erro #" + _statusFail);
            } finally {
                clearTimeout(_statusTimer);
                _statusTimer = setTimeout(refreshStatus, _statusInterval);
            }
        }

        let _statusInterval = 500;
        let _statusFail = 0;
        let _statusTimer = null;

        function _updateNetBanner(rtt) {
            let banner = document.getElementById("net-banner");
            if (!banner) return;
            if (rtt === null || rtt > 800 || _statusFail > 0) {
                const msg = rtt === null
                    ? (_statusFail >= 3 ? "⚠ Sem resposta do agv — verifique o Wi-Fi" : "⚠ Conexão lenta...")
                    : `⚠ Sinal fraco (${rtt}ms)`;
                banner.textContent = msg;
                banner.hidden = false;
            } else {
                banner.hidden = true;
            }
        }

    document.getElementById("btn-manual").addEventListener("click", () => setMode("manual"));
    document.getElementById("btn-auto").addEventListener("click", () => setMode("auto"));
    document.getElementById("btn-stop").addEventListener("click", emergencyStop);
    document.getElementById("btn-reconnect").addEventListener("click", reconnectKinect);
    document.getElementById("btn-stabilization").addEventListener("click", toggleStabilization);
    document.getElementById("btn-tilt-up").addEventListener("click", () => sendTilt(12));
    document.getElementById("btn-tilt-center").addEventListener("click", () => sendTilt(0));
    document.getElementById("btn-tilt-down").addEventListener("click", () => sendTilt(-12));
        document.getElementById("btn-face-register").addEventListener("click", registerFace);
        document.getElementById("face-select").addEventListener("change", (event) => {
            selectFace(event.target.value);
        });
    document.getElementById("btn-copy-link").addEventListener("click", copyShareLink);

    window.addEventListener("pointerdown", ensureAudioReady, { passive: true });
    window.addEventListener("keydown", ensureAudioReady);

    document.querySelectorAll("[data-key]").forEach((button) => {
      const key = button.dataset.key;
      const press = (event) => {
        event.preventDefault();
        setPressed(key, true, "site-touch");
      };
      const release = (event) => {
        event.preventDefault();
        setPressed(key, false, "site-touch");
      };
      button.addEventListener("pointerdown", press);
      button.addEventListener("pointerup", release);
      button.addEventListener("pointercancel", release);
      button.addEventListener("pointerleave", release);
    });

    window.addEventListener("keydown", (event) => {
            if (isTypingTarget(event)) return;
      if (event.repeat) return;
      const key = mapKey(event.key);
      if (!key) return;
      event.preventDefault();
      setPressed(key, true, "site-keyboard");
    });

    window.addEventListener("keyup", (event) => {
            if (isTypingTarget(event)) return;
      const key = mapKey(event.key);
      if (!key) return;
      event.preventDefault();
      setPressed(key, false, "site-keyboard");
    });

    window.addEventListener("blur", () => {
      controlState.pressed = { w: false, a: false, s: false, d: false };
            controlState.mobile = { x: 0, y: 0, active: false };
      sendManualCommand("site-blur");
      document.querySelectorAll("[data-key]").forEach((button) => button.classList.remove("is-active"));
    });

        const joystickBase = document.getElementById("joystick-base");
        const joystickKnob = document.getElementById("joystick-knob");
        const joystickReadout = document.getElementById("joystick-readout");
        let activePointerId = null;
        let _stickThrottleAt = 0;   // throttle: evita flood de requests no joystick

        function updateStickVisual(nx, ny) {
            const max = joystickBase.clientWidth * 0.22;
            joystickKnob.style.transform = `translate(${(nx * max).toFixed(1)}px, ${(ny * max).toFixed(1)}px)`;
            const speed = Math.round(-ny * 100);
            const steering = Math.round(nx * 100);
            if (Math.abs(speed) < 5 && Math.abs(steering) < 5) {
                joystickReadout.textContent = "Stick: parado";
            } else {
                joystickReadout.textContent = `Stick: vel ${speed} dir ${steering}`;
            }
        }

        function updateStickFromPointer(clientX, clientY) {
            const rect = joystickBase.getBoundingClientRect();
            const cx = rect.left + rect.width / 2;
            const cy = rect.top + rect.height / 2;
            const maxRadius = rect.width * 0.32;
            let dx = clientX - cx;
            let dy = clientY - cy;
            const distance = Math.hypot(dx, dy);
            if (distance > maxRadius) {
                const scale = maxRadius / distance;
                dx *= scale;
                dy *= scale;
            }
            const nx = dx / maxRadius;
            const ny = dy / maxRadius;
            controlState.mobile = { x: nx, y: ny, active: true };
            updateStickVisual(nx, ny);
            // Throttle: envia no máximo 1 request a cada 80 ms
            const now = Date.now();
            if (now - _stickThrottleAt >= 80) {
                _stickThrottleAt = now;
                sendManualCommand("site-stick");
            }
        }

        function resetStick() {
            controlState.mobile = { x: 0, y: 0, active: false };
            updateStickVisual(0, 0);
            sendManualCommand("site-stick-release");
        }

        joystickBase.addEventListener("pointerdown", (event) => {
            if (!uiState.isMobileControl) return;
            event.preventDefault();
            if (controlState.mode !== "manual") {
                setMode("manual");
            }
            activePointerId = event.pointerId;
            joystickBase.setPointerCapture(event.pointerId);
            updateStickFromPointer(event.clientX, event.clientY);
        });

        joystickBase.addEventListener("pointermove", (event) => {
            if (!uiState.isMobileControl) return;
            if (activePointerId !== event.pointerId) return;
            event.preventDefault();
            updateStickFromPointer(event.clientX, event.clientY);
        });

        const onStickRelease = (event) => {
            if (activePointerId !== event.pointerId) return;
            activePointerId = null;
            resetStick();
        };
        joystickBase.addEventListener("pointerup", onStickRelease);
        joystickBase.addEventListener("pointercancel", onStickRelease);
        joystickBase.addEventListener("pointerleave", onStickRelease);

        setupControlSurface();
        resolveShareLink();
        window.addEventListener("resize", setupControlSurface);

    refreshStatus();
    _statusTimer = setTimeout(refreshStatus, 500);

    // ── Configuracoes ──────────────────────────────────────────────────

    // Modo escuro
    const darkToggle = document.getElementById("dark-mode-toggle");
    function applyDark(enabled) {
      document.body.classList.toggle("dark", enabled);
      darkToggle.checked = enabled;
      localStorage.setItem("agv_dark", enabled ? "1" : "0");
    }
    applyDark(localStorage.getItem("agv_dark") === "1");
    darkToggle.addEventListener("change", () => applyDark(darkToggle.checked));

    // Velocidade maxima
    const speedSlider = document.getElementById("speed-limit-slider");
    const speedDisplay = document.getElementById("speed-limit-display");
    let speedSendTimer = null;

    async function sendSpeedLimit(pct) {
      try {
        await postJson("/api/settings", { speed_limit_pct: pct });
      } catch (e) {
        console.error(e);
      }
    }

    function applySpeedLimit(pct) {
      speedDisplay.textContent = pct + "%";
      speedSlider.value = pct;
      localStorage.setItem("agv_speed_limit", pct);
    }

    // Carregar valor salvo
    const savedSpeed = parseInt(localStorage.getItem("agv_speed_limit") ?? "100", 10);
    applySpeedLimit(isNaN(savedSpeed) ? 100 : savedSpeed);
    sendSpeedLimit(speedSlider.value);

    speedSlider.addEventListener("input", () => {
      const pct = parseInt(speedSlider.value, 10);
      applySpeedLimit(pct);
      clearTimeout(speedSendTimer);
      speedSendTimer = setTimeout(() => sendSpeedLimit(pct), 300);
    });
  </script>
</body>
</html>
'''


if __name__ == "__main__":
    raise SystemExit(main())
