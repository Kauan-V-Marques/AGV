#!/usr/bin/env python3
"""
Servidor web do AGV.

Escopo atual:
- stream RGB do Kinect v1
- stream do mapa de profundidade
- telemetria basica
- controle manual por site (W/A/S/D e toque)
- modo autonomo simples por profundidade
- envio serial para o Arduino
"""

import atexit
import glob
import logging
import math
import os
import subprocess
import sys
import threading
import time
from typing import Optional

import cv2
import numpy as np
from flask import Flask, Response, jsonify, request
from werkzeug.serving import make_server

try:
    import freenect  # type: ignore[import-not-found]
except Exception:
    freenect = None


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
MIN_FORWARD_PWM = int(os.environ.get("AGV_MIN_FORWARD_PWM", "170"))
MIN_TURN_PWM = int(os.environ.get("AGV_MIN_TURN_PWM", "140"))
KINECT_STABILIZATION_ENABLED = os.environ.get("AGV_KINECT_STABILIZATION", "1").strip() == "1"
KINECT_TILT_MIN = float(os.environ.get("AGV_KINECT_TILT_MIN", "-18"))
KINECT_TILT_MAX = float(os.environ.get("AGV_KINECT_TILT_MAX", "18"))
KINECT_TILT_INTERVAL = float(os.environ.get("AGV_KINECT_TILT_INTERVAL", "0.5"))
KINECT_TILT_DEADBAND = float(os.environ.get("AGV_KINECT_TILT_DEADBAND", "1.2"))
KINECT_TILT_NEUTRAL = float(os.environ.get("AGV_KINECT_TILT_NEUTRAL", "0"))
KINECT_TILT_GAIN = float(os.environ.get("AGV_KINECT_TILT_GAIN", "0.85"))
KINECT_TILT_SMOOTHING = float(os.environ.get("AGV_KINECT_TILT_SMOOTHING", "0.35"))
KINECT_TILT_MANUAL_HOLD = float(os.environ.get("AGV_KINECT_TILT_MANUAL_HOLD", "2.5"))
FACE_SCAN_INTERVAL = float(os.environ.get("AGV_FACE_SCAN_INTERVAL", "0.7"))
FACE_KAUAN_THRESHOLD = float(os.environ.get("AGV_FACE_KAUAN_THRESHOLD", "0.20"))
FACE_DB_DIR = os.path.join(PROJECT_ROOT, "logs")
FACE_KAUAN_PATH = os.path.join(FACE_DB_DIR, "kauan_face.npy")

app = Flask(__name__)

_runtime_lock = threading.Lock()
_runtime_started = False
_stop_event = threading.Event()
_threads = []

_state_lock = threading.Lock()
_state = {
    "rgb": None,
  "rgb_raw": None,
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
    "kinect_ok": False,
    "kinect_error": None,
    "last_frame_at": 0.0,
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
}
_face_kauan_descriptor = None


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


def _release_kinect_usb_claims() -> None:
    """Libera drivers do kernel que costumam prender o Kinect no Linux."""
    commands = [
        [
            "sudo", "-n", "modprobe", "-r",
            "gspca_kinect", "gspca_main", "uvcvideo", "snd_usb_audio",
            "videobuf2_v4l2", "videobuf2_vmalloc", "videobuf2_common", "videodev", "mc",
        ],
        ["sudo", "-n", "rmmod", "gspca_kinect"],
        ["sudo", "-n", "rmmod", "gspca_main"],
        ["sudo", "-n", "rmmod", "uvcvideo"],
        ["sudo", "-n", "rmmod", "snd_usb_audio"],
    ]
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
    global _face_kauan_descriptor

    if not os.path.exists(FACE_KAUAN_PATH):
        with _face_lock:
            _face_runtime["template_loaded"] = False
        return
    try:
        vec = np.load(FACE_KAUAN_PATH)
        if vec.ndim != 1:
            raise ValueError("template invalido")
        _face_kauan_descriptor = vec.astype(np.float32)
        with _face_lock:
            _face_runtime["template_loaded"] = True
        log.info("Face Kauan carregada de %s", FACE_KAUAN_PATH)
    except Exception as exc:
        _face_kauan_descriptor = None
        with _face_lock:
            _face_runtime["template_loaded"] = False
        log.warning("Falha ao carregar face do Kauan: %s", exc)


def _save_kauan_face_descriptor(descriptor: np.ndarray) -> None:
    global _face_kauan_descriptor

    os.makedirs(FACE_DB_DIR, exist_ok=True)
    np.save(FACE_KAUAN_PATH, descriptor.astype(np.float32))
    _face_kauan_descriptor = descriptor.astype(np.float32)
    with _face_lock:
        _face_runtime["template_loaded"] = True


def _face_status_snapshot() -> dict:
    with _face_lock:
        return {
            "known": bool(_face_runtime["known"]),
            "label": str(_face_runtime["label"]),
            "distance": _face_runtime["distance"],
            "last_seen_at": float(_face_runtime["last_seen_at"]),
            "template_loaded": bool(_face_runtime["template_loaded"]),
        }


def _update_face_runtime(frame_bgr: np.ndarray) -> np.ndarray:
    now = _now()
    with _face_lock:
        if now < float(_face_runtime["next_scan_at"]):
            cached_bbox = _face_runtime["bbox"]
            known = bool(_face_runtime["known"])
            label = str(_face_runtime["label"])
        else:
            cached_bbox = None
            known = False
            label = ""

    if cached_bbox is None:
        gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
        bbox, descriptor = _face_extract_primary(gray)

        known = False
        label = ""
        distance = None

        if bbox is not None and descriptor is not None and _face_kauan_descriptor is not None:
            distance = float(np.linalg.norm(descriptor - _face_kauan_descriptor))
            if distance <= FACE_KAUAN_THRESHOLD:
                known = True
                label = "e o Kauan"

        with _face_lock:
            _face_runtime["known"] = known
            _face_runtime["label"] = label
            _face_runtime["distance"] = distance
            _face_runtime["last_seen_at"] = now if bbox is not None else _face_runtime["last_seen_at"]
            _face_runtime["bbox"] = bbox if known else None
            _face_runtime["next_scan_at"] = now + FACE_SCAN_INTERVAL
            _face_runtime["template_loaded"] = _face_kauan_descriptor is not None
            if known and now - float(_face_runtime["last_announce_at"]) >= 4.0:
                log.info("Face reconhecida: e o Kauan")
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


def _compute_tilt_target(ax: float, ay: float, az: float) -> float:
    gravity = math.sqrt((ax * ax) + (ay * ay) + (az * az))
    if gravity < 1e-6:
        return float(max(KINECT_TILT_MIN, min(KINECT_TILT_MAX, KINECT_TILT_NEUTRAL)))

    dominant_pitch = math.degrees(math.atan2(ax, max(1e-6, abs(az))))
    if abs(ay) > abs(ax):
        dominant_pitch = math.degrees(math.atan2(ay, max(1e-6, abs(az))))

    requested = KINECT_TILT_NEUTRAL - (dominant_pitch * KINECT_TILT_GAIN)
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
    return True, "comando enviado", target


def _do_tilt_body(dev) -> None:
    """Ajusta o tilt do Kinect para manter o frame mais nivelado."""
    global _tilt_last_set_at, _tilt_last_target, _tilt_manual_until, _tilt_manual_target

    if not KINECT_STABILIZATION_ENABLED:
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

        if now < _tilt_manual_until:
            target_f = _tilt_manual_target
        else:
            target_f = _compute_tilt_target(ax, ay, az)
            alpha = max(0.0, min(1.0, KINECT_TILT_SMOOTHING))
            if alpha > 0.0:
                target_f = (_tilt_last_target * (1.0 - alpha)) + (target_f * alpha)

        target_f = float(max(KINECT_TILT_MIN, min(KINECT_TILT_MAX, target_f)))

        if abs(raw_tilt - target_f) >= KINECT_TILT_DEADBAND:
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

    forward_pct = max(0, speed_cmd)
    accel = forward_pct * 255 // 100

    if forward_pct > 0:
        accel = max(_clamp(MIN_FORWARD_PWM, 0, 255), accel)

    if accel == 0 and abs(steering_cmd) >= 20:
        accel = _clamp(MIN_TURN_PWM, 0, 255)

    steering_gain = 0.70 if accel > 0 else 1.00
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
    if limit_pct < 100 and accel > 0:
        max_pwm = int(255 * limit_pct / 100)
        accel = min(accel, max_pwm)

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

    return {
        "ok": True,
        "state": state,
        "agv": control,
        "autopilot": autopilot,
        "arduino": arduino,
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
                frame_bgr = _update_face_runtime(rgb.copy())
                with _state_lock:
                    _state["rgb_raw"] = rgb
                    _state["rgb"] = frame_bgr
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

        if mode == "auto":
            with _autopilot_lock:
                speed = int(_autopilot_state["target_speed"])
                steering = int(_autopilot_state["target_steering"])
            command_source = "autopilot"
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
        _load_kauan_face_descriptor()

        specs = [
            ("agv-kinect", _capture_loop),
            ("agv-autopilot", _autopilot_loop),
            ("agv-control", _control_loop),
        ]
        for name, target in specs:
            thread = threading.Thread(target=target, daemon=True, name=name)
            thread.start()
            _threads.append(thread)

        log.info("Runtime AGV iniciado")


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
    log.info("Runtime AGV finalizado")


atexit.register(stop_runtime)


def _mjpeg_generator(kind: str, fps: int):
    interval = 1.0 / max(1, fps)
    while True:
        if kind == "rgb":
            with _state_lock:
                frame = None if _state["rgb"] is None else _state["rgb"].copy()
                error = _state["kinect_error"]
            if frame is None:
                frame = _placeholder_frame("Aguardando Kinect RGB")
                if error:
                    cv2.putText(frame, error[:44], (40, 280), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (120, 180, 255), 2)
        else:
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

        data = _encode_jpeg(frame)
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
    return Response(_mjpeg_generator("rgb", 18), mimetype="multipart/x-mixed-replace; boundary=frame")


@app.route("/depth_map")
def route_depth_map():
    return Response(_mjpeg_generator("depth", 10), mimetype="multipart/x-mixed-replace; boundary=frame")


@app.route("/api/video")
def route_api_video():
    return route_video()


@app.route("/api/depth_map")
def route_api_depth_map():
    return route_depth_map()


@app.route("/status")
@app.route("/api/status")
def route_status():
    return jsonify(_get_status_snapshot())


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


@app.route("/api/face/status")
def route_face_status():
    return jsonify({"ok": True, "face": _face_status_snapshot()})


@app.route("/api/face/register_kauan", methods=["POST"])
def route_face_register_kauan():
    with _state_lock:
        frame = None if _state["rgb_raw"] is None else _state["rgb_raw"].copy()

    if frame is None:
        return jsonify({"ok": False, "error": "no_frame"}), 400

    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    bbox, descriptor = _face_extract_primary(gray)
    if bbox is None or descriptor is None:
        return jsonify({"ok": False, "error": "no_face_detected"}), 400

    _save_kauan_face_descriptor(descriptor)
    with _face_lock:
        _face_runtime["known"] = True
        _face_runtime["label"] = "e o Kauan"
        _face_runtime["distance"] = 0.0
        _face_runtime["last_seen_at"] = _now()
        _face_runtime["bbox"] = bbox
        _face_runtime["next_scan_at"] = 0.0

    return jsonify({"ok": True, "face": _face_status_snapshot()})


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

    return jsonify(_get_status_snapshot())


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

    return jsonify(_get_status_snapshot())


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
  <title>AGV Basico</title>
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
    body.dark button { background: #21262d; }
    body.dark .mode-button[data-mode="manual"] { background: #1a3a2c; }
    body.dark .mode-button[data-mode="auto"] { background: #4a2010; }
    body.dark .stop-button { background: var(--danger); }

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
      <div class="hero-top">
        <div>
          <h1>AGV<br>Basico</h1>
          <p>Controle pelo navegador com video ao vivo do Kinect, mapa de profundidade e modo autonomo simples por distancia.</p>
        </div>
        <div class="chips">
          <div class="chip" id="chip-mode">modo manual</div>
          <div class="chip" id="chip-kinect">kinect offline</div>
          <div class="chip" id="chip-serial">serial desconectada</div>
          <div class="chip" id="chip-face">face sem cadastro</div>
        </div>
      </div>
    </section>

    <section class="grid">
      <div class="video-stack">
        <article class="panel">
          <h2>RGB Kinect</h2>
          <img class="stream" src="/video" alt="Video RGB do Kinect">
          <div class="caption">Imagem ao vivo usada para acompanhar o AGV.</div>
        </article>

        <article class="panel">
          <h2>Depth Kinect</h2>
          <img class="stream" src="/depth_map" alt="Mapa de profundidade do Kinect">
          <div class="caption">Mapa de distancia usado no modo autonomo.</div>
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
            <button id="btn-tilt-up">Tilt +</button>
            <button id="btn-tilt-center">Centralizar</button>
            <button id="btn-tilt-down">Tilt -</button>
            <button id="btn-face-register">Registrar Kauan</button>
            <button class="stop-button" id="btn-stop">Parada total</button>
          </div>
        </section>

        <section class="panel">
          <h2>Controle Manual</h2>
          <div class="pad">
            <button class="blank" aria-hidden="true"></button>
            <button data-key="w">W</button>
            <button class="blank" aria-hidden="true"></button>
            <button data-key="a">A</button>
            <button data-key="s">S</button>
            <button data-key="d">D</button>
          </div>
          <p class="footer-note">No celular, segure os botoes. No PC, use W A S D, setas ou E como esquerda.</p>
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
      lastSpeed: 0,
      lastSteering: 0,
    };

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

    function deriveManualCommand() {
      let speed = 0;
      let steering = 0;

      if (controlState.pressed.w && !controlState.pressed.s) speed = 100;
      if (controlState.pressed.s && !controlState.pressed.w) speed = -100;
      if (controlState.pressed.a && !controlState.pressed.d) steering = -70;
      if (controlState.pressed.d && !controlState.pressed.a) steering = 70;

      return { speed, steering };
    }

    async function postJson(url, payload) {
      const response = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload || {}),
      });
      return response.json();
    }

    async function sendManualCommand(source) {
      if (controlState.mode !== "manual") return;
      const next = deriveManualCommand();
      if (next.speed === controlState.lastSpeed && next.steering === controlState.lastSteering) return;

      controlState.lastSpeed = next.speed;
      controlState.lastSteering = next.steering;
      try {
        await postJson("/api/control", {
          mode: "manual",
          speed: next.speed,
          steering: next.steering,
          source: source || "site-manual",
        });
      } catch (error) {
        console.error(error);
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

    async function registerKauanFace() {
      try {
        const response = await postJson("/api/face/register_kauan", {});
        if (!response.ok) {
          alert("Nao foi possivel cadastrar o rosto agora. Olhe para a camera e tente de novo.");
          return;
        }
        alert("Rosto do Kauan cadastrado com sucesso.");
      } catch (error) {
        console.error(error);
        alert("Falha ao cadastrar rosto.");
      }
    }

    function updateTelemetry(snapshot) {
      const state = snapshot.state || {};
      const agv = snapshot.agv || {};
      const autopilot = snapshot.autopilot || {};
      const arduino = snapshot.arduino || {};
      const face = snapshot.face || {};

      document.getElementById("chip-mode").textContent = "modo " + (agv.mode || "manual");
      document.getElementById("chip-kinect").textContent = state.kinect_ok ? "kinect online" : "kinect offline";
      document.getElementById("chip-serial").textContent = arduino.connected ? "serial conectada" : "serial desconectada";
      if (!face.template_loaded) {
        document.getElementById("chip-face").textContent = "face sem cadastro";
      } else if (face.known && face.label) {
        document.getElementById("chip-face").textContent = face.label;
      } else {
        document.getElementById("chip-face").textContent = "face ativa";
      }

      document.getElementById("distance").textContent = fmtMeters(state.distance_m);
      document.getElementById("left-clearance").textContent = fmtMeters(state.left_clearance_m);
      document.getElementById("center-clearance").textContent = fmtMeters(state.center_clearance_m);
      document.getElementById("right-clearance").textContent = fmtMeters(state.right_clearance_m);
      document.getElementById("active-command").textContent = `${agv.active_speed ?? 0} / ${agv.active_steering ?? 0}`;
      document.getElementById("auto-reason").textContent = autopilot.last_reason || "--";
      document.getElementById("kinect-status").textContent = state.kinect_ok
        ? `ok | atraso ${state.frame_age_s ?? "--"}s | tilt ${state.tilt_deg ?? "--"} | alvo ${state.tilt_target_deg ?? "--"}`
        : (state.kinect_error || "offline");
      document.getElementById("serial-status").textContent = arduino.connected ? `${arduino.port || "usb"} | ${arduino.last_command || "--"}` : (arduino.last_error || "desconectada");
      document.getElementById("face-status").textContent = face.known ? (face.label || "e o Kauan") : "--";
      document.getElementById("fps-status").textContent = `${state.fps_rgb || 0} rgb | ${state.fps_depth || 0} depth`;

      if ((agv.mode || "manual") !== controlState.mode) {
        controlState.mode = agv.mode || "manual";
        document.querySelectorAll(".mode-button").forEach((button) => {
          button.classList.toggle("is-selected", button.dataset.mode === controlState.mode);
        });
      }
    }

    async function refreshStatus() {
      try {
        const response = await fetch("/api/status", { cache: "no-store" });
        const snapshot = await response.json();
        updateTelemetry(snapshot);
      } catch (error) {
        console.error(error);
      }
    }

    document.getElementById("btn-manual").addEventListener("click", () => setMode("manual"));
    document.getElementById("btn-auto").addEventListener("click", () => setMode("auto"));
    document.getElementById("btn-stop").addEventListener("click", emergencyStop);
    document.getElementById("btn-reconnect").addEventListener("click", reconnectKinect);
    document.getElementById("btn-tilt-up").addEventListener("click", () => sendTilt(12));
    document.getElementById("btn-tilt-center").addEventListener("click", () => sendTilt(0));
    document.getElementById("btn-tilt-down").addEventListener("click", () => sendTilt(-12));
    document.getElementById("btn-face-register").addEventListener("click", registerKauanFace);

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
      if (event.repeat) return;
      const key = mapKey(event.key);
      if (!key) return;
      event.preventDefault();
      setPressed(key, true, "site-keyboard");
    });

    window.addEventListener("keyup", (event) => {
      const key = mapKey(event.key);
      if (!key) return;
      event.preventDefault();
      setPressed(key, false, "site-keyboard");
    });

    window.addEventListener("blur", () => {
      controlState.pressed = { w: false, a: false, s: false, d: false };
      sendManualCommand("site-blur");
      document.querySelectorAll("[data-key]").forEach((button) => button.classList.remove("is-active"));
    });

    refreshStatus();
    setInterval(refreshStatus, 500);

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
