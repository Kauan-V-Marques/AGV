#!/usr/bin/env python3
"""
AGV — Sistema Kinect v1 Completo
Vídeo RGB + Profundidade/Distância + Acelerômetro + Motor (estabilização)
TUDO rodando ao mesmo tempo, sem parar.

Endpoints:
  http://<ip>:5000/           → Painel visual
  http://<ip>:5000/video      → Stream MJPEG RGB
  http://<ip>:5000/depth_map  → Stream MJPEG mapa de profundidade
  http://<ip>:5000/status     → JSON com todos os dados
"""

import threading
import time
import math
import logging
import os
import sys
import json
import glob
from datetime import datetime
import numpy as np
import cv2
from flask import Flask, Response, jsonify, request
try:
    import face_recognition  # type: ignore[import-not-found]
    _FACE_RECOGNITION_AVAILABLE = True
except Exception:
    face_recognition = None
    _FACE_RECOGNITION_AVAILABLE = False

from ai_brain import AGVBrain

# ─────────────────────────────────────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stdout,
)
log = logging.getLogger("agv")

# ─────────────────────────────────────────────────────────────────────────────
# ESTADO GLOBAL (protegido por lock)
# ─────────────────────────────────────────────────────────────────────────────
_lock = threading.Lock()
_state = {
    "rgb":        None,   # numpy BGR uint8 480×640×3
    "depth":      None,   # numpy uint16 480×640
    "depth_mm":   False,  # True se depth já está em mm
    "distance_m": None,   # float: objeto mais próximo na ROI central (metros)
    "left_clearance_m": None,
    "center_clearance_m": None,
    "right_clearance_m": None,
    "tilt_deg":   None,   # float|None: ângulo atual lido do motor (graus)
    "tilt_cmd":   0.0,    # float: último alvo comandado ao motor (graus)
    "motor_ok":   False,
    "accel":      [0.0, 0.0, 0.0],  # [x, y, z] em m/s²
    "kinect_ok":  False,
    "fps_rgb":    0.0,
    "fps_depth":  0.0,
    "error":      None,
}

_agv_control = {
    "mode": "manual",
    "speed": 0,
    "steering": 0,
    "updated_at": time.time(),
    "last_source": "boot",
}

_arduino_lock = threading.Lock()
_arduino_serial = None
_arduino_state = {
    "connected": False,
    "port": None,
    "baud": int(os.environ.get("AGV_ARDUINO_BAUD", "115200")),
    "protocol": str(os.environ.get("AGV_ARDUINO_PROTOCOL", "wasd")).strip().lower() or "wasd",
    "last_error": None,
    "last_command": None,
    "enabled": os.environ.get("AGV_ARDUINO_ENABLED", "1").strip() == "1",
}

_brain = None
_brain_running = True

_running = True          # False dispara Kill no runloop

# Inicialização one-shot no body_cb
_init_done = False

# Contadores de FPS
_rgb_n   = 0
_depth_n = 0
_fps_t   = time.time()

# Estabilização do motor
_last_tilt_sent = 0.0
_last_tilt_time = 0.0
_motor_tested   = False
TILT_DEADBAND  = 1.5    # não mover motor se diferença < 1.5°
TILT_INTERVAL  = 0.8    # segundos mínimos entre comandos ao motor
TILT_MIN       = -20.0
TILT_MAX       = +20.0

FACE_DB_PATH = os.path.join("logs", "faces_db.json")
FACE_MATCH_THRESHOLD = 0.6
FACE_SCAN_INTERVAL = 0.35

FRONTEND_REVISION = "2026.03.22-r2"
_RUNTIME_SOURCE = sys.executable if getattr(sys, "frozen", False) else __file__
try:
    _RUNTIME_BUILD_TS = float(os.path.getmtime(_RUNTIME_SOURCE))
except Exception:
    _RUNTIME_BUILD_TS = time.time()


def _runtime_meta_snapshot():
    return {
        "frontend_revision": FRONTEND_REVISION,
        "runtime_mode": "frozen" if getattr(sys, "frozen", False) else "source",
        "runtime_source": os.path.basename(_RUNTIME_SOURCE),
        "build_local_time": datetime.fromtimestamp(_RUNTIME_BUILD_TS).strftime("%Y-%m-%d %H:%M:%S"),
    }

_face_lock = threading.Lock()
_face_runtime = {
    "next_scan_at": 0.0,
    "last": {
        "label": "--",
        "known": False,
        "distance": None,
        "bbox": None,
        "updated_at": 0.0,
        "trigger_id": 0,
    },
}
_face_db = {
    "enabled": False,
    "selected": "",
    "people": {},
}


def _safe_ascii_name(raw: str) -> str:
    keep = []
    for ch in str(raw or "").strip():
        if ch.isalnum() or ch in ("-", "_", " "):
            keep.append(ch)
    name = "".join(keep).strip()
    return name[:40]


def _save_face_db():
    os.makedirs(os.path.dirname(FACE_DB_PATH), exist_ok=True)
    tmp_path = FACE_DB_PATH + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(_face_db, f, ensure_ascii=True)
    os.replace(tmp_path, FACE_DB_PATH)


def _load_face_db():
    if not os.path.exists(FACE_DB_PATH):
        return
    try:
        with open(FACE_DB_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as exc:
        log.warning("face db invalido: %s", exc)
        return

    people = data.get("people") if isinstance(data, dict) else None
    if not isinstance(people, dict):
        people = {}

    clean_people = {}
    for name, samples in people.items():
        safe_name = _safe_ascii_name(name)
        if not safe_name or not isinstance(samples, list):
            continue
        clean_samples = []
        for sample in samples:
            if not isinstance(sample, list):
                continue
            vec = np.asarray(sample, dtype=np.float32)
            if vec.ndim != 1 or vec.size != 96:
                continue
            clean_samples.append(vec.tolist())
        if clean_samples:
            clean_people[safe_name] = clean_samples[:30]

    with _face_lock:
        _face_db["enabled"] = bool(data.get("enabled", False))
        _face_db["selected"] = _safe_ascii_name(data.get("selected", ""))
        _face_db["people"] = clean_people


def _extract_face_signature(bgr_frame):
    """Extract face encoding using deep learning (ResNet)."""
    if bgr_frame is None:
        return None, None
    if not _FACE_RECOGNITION_AVAILABLE:
        return None, None
    
    rgb_frame = cv2.cvtColor(bgr_frame, cv2.COLOR_BGR2RGB)
    
    # Detectar faces usando face_recognition
    face_locations = face_recognition.face_locations(rgb_frame, model="hog")
    if not face_locations:
        return None, None
    
    # Usar a maior face detectada
    (top, right, bottom, left) = max(face_locations, key=lambda loc: (loc[2] - loc[0]) * (loc[3] - loc[1]))
    
    # Extrair encoding (embedding) da face
    encodings = face_recognition.face_encodings(rgb_frame, [face_locations[0]])
    if not encodings:
        return None, None
    
    encoding = encodings[0]
    bbox = (left, top, right - left, bottom - top)
    
    return encoding.astype(np.float32), bbox



def _face_match(signature):
    """Match face signature against known faces using euclidean distance."""
    with _face_lock:
        selected = _face_db.get("selected", "")
        people = dict(_face_db.get("people", {}))

    candidates = {}
    if selected:
        samples = people.get(selected)
        if samples:
            candidates[selected] = samples
    if not candidates:
        candidates = people

    if not candidates:
        return "Desconhecido", None, False

    best_name = "Desconhecido"
    best_dist = float('inf')
    
    for name, samples in candidates.items():
        for sample in samples:
            vec = np.asarray(sample, dtype=np.float32)
            if vec.shape != signature.shape:
                continue
            # Use euclidean distance (face_recognition standard)
            dist = float(np.sqrt(np.sum((signature - vec) ** 2)))
            if dist < best_dist:
                best_dist = dist
                best_name = name

    # FACE_MATCH_THRESHOLD = 0.6 is standard for face_recognition
    known = best_dist <= FACE_MATCH_THRESHOLD
    if not known:
        return "Desconhecido", best_dist, False
    return best_name, best_dist, True



def _update_face_runtime(frame_bgr):
    now = time.time()
    with _face_lock:
        enabled = bool(_face_db.get("enabled", False))
        last = dict(_face_runtime["last"])
        next_scan_at = float(_face_runtime.get("next_scan_at", 0.0))
    if not enabled:
        return last
    if now < next_scan_at:
        return last

    signature, bbox = _extract_face_signature(frame_bgr)
    if signature is None:
        result = {
            "label": "Sem rosto",
            "known": False,
            "distance": None,
            "bbox": None,
            "updated_at": now,
            "trigger_id": last.get("trigger_id", 0),
        }
    else:
        label, distance, known = _face_match(signature)
        trigger_id = last.get("trigger_id", 0)
        last_label = last.get("label")
        last_known = bool(last.get("known"))
        if label != "Sem rosto" and (label != last_label or bool(known) != last_known):
            trigger_id += 1
        result = {
            "label": label,
            "known": bool(known),
            "distance": distance,
            "bbox": bbox,
            "updated_at": now,
            "trigger_id": trigger_id,
        }

    with _face_lock:
        _face_runtime["last"] = result
        _face_runtime["next_scan_at"] = now + FACE_SCAN_INTERVAL
    return result


def _draw_face_overlay(frame_bgr):
    info = _update_face_runtime(frame_bgr)
    bbox = info.get("bbox")
    if bbox:
        x, y, w, h = bbox
        color = (80, 240, 140) if info.get("known") else (75, 120, 255)
        cv2.rectangle(frame_bgr, (x, y), (x + w, y + h), color, 2)
        label = info.get("label", "--")
        cv2.putText(
            frame_bgr,
            label,
            (x, max(22, y - 8)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.65,
            color,
            2,
        )
    return frame_bgr


def _face_status_snapshot():
    with _face_lock:
        people = _face_db.get("people", {})
        last = dict(_face_runtime["last"])
        return {
            "enabled": bool(_face_db.get("enabled", False)),
            "selected": str(_face_db.get("selected", "")),
            "people": [{"name": name, "samples": len(samples)} for name, samples in sorted(people.items())],
            "last": {
                "label": str(last.get("label", "--")),
                "known": bool(last.get("known", False)),
                "distance": last.get("distance"),
                "updated_at": float(last.get("updated_at", 0.0)),
                "trigger_id": int(last.get("trigger_id", 0)),
            },
        }


def _depth_segment_distance(depth_seg, is_mm):
    if is_mm:
        valid = depth_seg[(depth_seg > 200) & (depth_seg < 8000)]
        if valid.size:
            return float(np.min(valid)) / 1000.0
        return None

    valid = depth_seg[(depth_seg > 100) & (depth_seg < 2040)]
    if not valid.size:
        return None
    r = float(np.max(valid))
    denom = (r * -0.0030711016 + 3.3309495161)
    if denom <= 0.0:
        return None
    depth_m = 1.0 / denom
    if 0.15 <= depth_m <= 10.0:
        return depth_m
    return None


def _candidate_arduino_ports():
    preferred = os.environ.get("AGV_ARDUINO_PORT", "").strip()
    ports = []
    if preferred:
        ports.append(preferred)
    ports.extend(sorted(glob.glob("/dev/ttyACM*")))
    ports.extend(sorted(glob.glob("/dev/ttyUSB*")))

    dedup = []
    seen = set()
    for p in ports:
        if p and p not in seen:
            seen.add(p)
            dedup.append(p)
    return dedup


def _connect_arduino():
    global _arduino_serial

    if not _arduino_state["enabled"]:
        log.info("Arduino serial desativado (AGV_ARDUINO_ENABLED=0)")
        return False

    try:
        import serial  # type: ignore
    except Exception as exc:
        _arduino_state["last_error"] = f"pyserial ausente: {exc}"
        log.warning("Arduino: pyserial nao disponivel (%s)", exc)
        return False

    ports = _candidate_arduino_ports()
    if not ports:
        _arduino_state["last_error"] = "nenhuma porta serial encontrada"
        log.warning("Arduino: nenhuma porta /dev/ttyACM* ou /dev/ttyUSB* encontrada")
        return False

    for port in ports:
        try:
            ser = serial.Serial(
                port=port,
                baudrate=int(_arduino_state["baud"]),
                timeout=0.1,
                write_timeout=0,
            )
            # Avoid blocking HTTP control requests for too long while reconnecting.
            time.sleep(0.1)
            try:
                ser.reset_input_buffer()
                ser.reset_output_buffer()
            except Exception:
                pass

            with _arduino_lock:
                _arduino_serial = ser
                _arduino_state["connected"] = True
                _arduino_state["port"] = port
                _arduino_state["last_error"] = None

            log.info(
                "Arduino serial conectado em %s @ %d (%s)",
                port,
                int(_arduino_state["baud"]),
                _arduino_state["protocol"],
            )
            return True
        except Exception as exc:
            _arduino_state["last_error"] = f"falha ao abrir {port}: {exc}"
            log.warning("Arduino: falha ao abrir %s (%s)", port, exc)

    return False


def _speed_steering_to_wasd(speed, steering):
    s = int(speed)
    t = int(steering)
    if abs(s) < 8 and abs(t) < 12:
        return "X"
    if t <= -25:
        return "A"
    if t >= 25:
        return "D"
    if s > 0:
        return "W"
    if s < 0:
        return "S"
    return "X"


def _send_to_arduino(speed, steering, mode, source):
    global _arduino_serial

    try:
        speed_i = int(speed)
    except Exception:
        speed_i = 0
    try:
        steering_i = int(steering)
    except Exception:
        steering_i = 0

    if not _arduino_state["enabled"]:
        return False

    with _arduino_lock:
        ser = _arduino_serial

    if ser is None:
        if not _connect_arduino():
            return False
        with _arduino_lock:
            ser = _arduino_serial
        if ser is None:
            return False

    proto = _arduino_state["protocol"]
    payload = None

    try:
        if proto == "csv":
            payload = f"M,{speed_i},{steering_i}\\n"
            ser.write(payload.encode("ascii", errors="ignore"))
        elif proto == "dual":
            wasd = _speed_steering_to_wasd(speed_i, steering_i)
            payload = f"M,{speed_i},{steering_i}|{wasd}|{mode}|{source}"
            ser.write(f"M,{speed_i},{steering_i}\\n".encode("ascii", errors="ignore"))
            ser.write(f"{wasd}\\n".encode("ascii", errors="ignore"))
        else:
            wasd = _speed_steering_to_wasd(speed_i, steering_i)
            payload = wasd
            ser.write(f"{wasd}\\n".encode("ascii", errors="ignore"))

        with _arduino_lock:
            _arduino_state["connected"] = True
            _arduino_state["last_error"] = None
            _arduino_state["last_command"] = payload
        return True
    except Exception as exc:
        with _arduino_lock:
            _arduino_state["connected"] = False
            _arduino_state["last_error"] = str(exc)
        log.warning("Arduino: falha ao enviar comando (%s)", exc)
        try:
            ser.close()
        except Exception:
            pass
        with _arduino_lock:
            _arduino_serial = None
        return False

# ─────────────────────────────────────────────────────────────────────────────
# CALLBACKS FREENECT
# ─────────────────────────────────────────────────────────────────────────────

def _tick_fps():
    """Atualiza FPS a cada 2 segundos."""
    global _rgb_n, _depth_n, _fps_t
    now = time.time()
    dt = now - _fps_t
    if dt >= 2.0:
        with _lock:
            _state["fps_rgb"]   = round(_rgb_n   / dt, 1)
            _state["fps_depth"] = round(_depth_n / dt, 1)
        _rgb_n   = 0
        _depth_n = 0
        _fps_t   = now


def video_cb(dev, data, timestamp):
    """Chamado pelo runloop para cada frame RGB."""
    global _rgb_n
    try:
        frame = np.asarray(data, dtype=np.uint8).copy()
        # freenect entrega RGB → converter para BGR (OpenCV/MJPEG)
        bgr = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
        with _lock:
            _state["rgb"]       = bgr
            _state["kinect_ok"] = True
        _rgb_n += 1
    except Exception as exc:
        log.debug("video_cb: %s", exc)


def depth_cb(dev, data, timestamp):
    """Chamado pelo runloop para cada frame de profundidade."""
    global _depth_n
    try:
        d  = np.asarray(data, dtype=np.uint16).copy()
        h, w = d.shape

        # Detecta escala automaticamente:
        #   DEPTH_MM    → valores 0-8000+ (mm)
        #   DEPTH_11BIT → valores 0-2047  (disparity raw)
        is_mm = int(d.max()) > 2047

        y0 = int(h * 0.35)
        y1 = int(h * 0.65)
        x0 = int(w * 0.25)
        x1 = int(w * 0.75)
        roi = d[y0:y1, x0:x1]
        cols = roi.shape[1]
        third = max(1, cols // 3)
        left_seg = roi[:, :third]
        center_seg = roi[:, third:third * 2]
        right_seg = roi[:, third * 2:]
        dist_m = None

        if is_mm:
            valid = roi[(roi > 200) & (roi < 8000)]
            if valid.size:
                dist_m = float(np.min(valid)) / 1000.0
        else:
            # Disparity 11BIT: maior disparity = mais perto
            # Converter usando fórmula OpenKinect
            valid = roi[(roi > 100) & (roi < 2040)]
            if valid.size:
                r = float(np.max(valid))
                denom = (r * -0.0030711016 + 3.3309495161)
                if denom > 0.0:
                    depth_m = 1.0 / denom
                    if 0.15 <= depth_m <= 10.0:
                        dist_m = depth_m

        left_m = _depth_segment_distance(left_seg, is_mm)
        center_m = _depth_segment_distance(center_seg, is_mm)
        right_m = _depth_segment_distance(right_seg, is_mm)

        with _lock:
            _state["depth"]    = d
            _state["depth_mm"] = is_mm
            if dist_m is not None:
                _state["distance_m"] = dist_m
            _state["left_clearance_m"] = left_m
            _state["center_clearance_m"] = center_m
            _state["right_clearance_m"] = right_m
        _depth_n += 1
    except Exception as exc:
        log.debug("depth_cb: %s", exc)


def body_cb(dev, ctx):
    """
    Chamado a cada iteração do runloop.
    Lê acelerômetro + ângulo atual e ajusta motor para estabilização.
    """
    global _last_tilt_sent, _last_tilt_time, _init_done, _motor_tested
    import freenect

    if not _running:
        raise freenect.Kill

    # Na primeira iteração: configura LED e tenta mudar depth para MM
    if not _init_done:
        _init_done = True
        try:
            freenect.set_led(dev, freenect.LED_GREEN)
        except Exception:
            pass
        try:
            freenect.stop_depth(dev)
            freenect.set_depth_mode(dev,
                                    freenect.RESOLUTION_MEDIUM,
                                    freenect.DEPTH_MM)
            freenect.start_depth(dev)
            log.info("Kinect: depth reconfigurado para DEPTH_MM")
        except Exception as exc:
            log.warning("Kinect: set_depth_mode no body falhou (%s) — usando 11BIT", exc)

    _tick_fps()

    try:
        freenect.update_tilt_state(dev)
        state        = freenect.get_tilt_state(dev)
        accel        = freenect.get_mks_accel(state)   # (x, y, z) em m/s²
        raw_tilt     = float(freenect.get_tilt_degs(state))   # graus
        current_tilt = raw_tilt if -31.0 <= raw_tilt <= 31.0 else None

        ax = float(accel[0])
        ay = float(accel[1])
        az = float(accel[2])

        with _lock:
            _state["accel"]    = [ax, ay, az]
            _state["tilt_deg"] = current_tilt

        # Teste único de disponibilidade do motor
        if not _motor_tested:
            _motor_tested = True
            try:
                freenect.set_tilt_degs(dev, 0.0)
                with _lock:
                    _state["motor_ok"] = True
            except Exception:
                with _lock:
                    _state["motor_ok"] = False

        # ── Estabilização ──────────────────────────────────────────────────
        # ax ≈ aceleração no eixo frente/trás do Kinect (m/s²)
        # Quando o chassi inclina para frente, ax aumenta
        # Compensamos inclinando o motor na direção oposta
        now   = time.time()
        ratio = max(-1.0, min(1.0, ax / 9.81))
        chassis_pitch_deg = math.degrees(math.asin(ratio))
        target = float(np.clip(-chassis_pitch_deg, TILT_MIN, TILT_MAX))

        if (abs(target - _last_tilt_sent) > TILT_DEADBAND
                and (now - _last_tilt_time) > TILT_INTERVAL):
            freenect.set_tilt_degs(dev, target)
            _last_tilt_sent = target
            _last_tilt_time = now
            with _lock:
                _state["tilt_cmd"] = float(target)
            log.debug("motor: %.1f° → %.1f° (pitch=%.1f°)",
                      current_tilt if current_tilt is not None else -99.0,
                      target, chassis_pitch_deg)
    except freenect.Kill:
        raise
    except Exception as exc:
        log.debug("body_cb: %s", exc)


# ─────────────────────────────────────────────────────────────────────────────
# WORKER KINECT
# ─────────────────────────────────────────────────────────────────────────────

def _start_kinect():
    """Abre dispositivo Kinect, configura modos e inicia runloop em thread."""
    import freenect

    def _run():
        try:
            log.info("Kinect: inicializando...")
            # Deixar runloop criar e gerenciar ctx/dev internamente
            # (passar dev externo quebra process_events que precisa de ctx)
            log.info("Kinect: runloop iniciando (RGB + Depth + Motor)...")
            freenect.runloop(
                video=video_cb,
                depth=depth_cb,
                body=body_cb,
            )
        except Exception as exc:
            log.error("Kinect worker encerrou: %s", exc)
            with _lock:
                _state["error"] = str(exc)

    t = threading.Thread(target=_run, daemon=True, name="kinect-runloop")
    t.start()
    return t


# ─────────────────────────────────────────────────────────────────────────────
# FLASK — SERVIDOR WEB
# ─────────────────────────────────────────────────────────────────────────────

app = Flask(__name__)


def _encode_jpeg(frame, quality=82):
    ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, quality])
    return buf.tobytes() if ok else None


def _mjpeg_gen(get_fn, fps=15):
    """Gerador MJPEG genérico. get_fn() deve retornar numpy BGR ou None."""
    interval = 1.0 / fps
    while True:
        frame = get_fn()
        if frame is None:
            frame = np.zeros((480, 640, 3), dtype=np.uint8)
            cv2.putText(frame, "AGUARDANDO KINECT...", (60, 245),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.85, (0, 200, 0), 2)
        data = _encode_jpeg(frame)
        if data:
            yield (
                b"--frame\r\n"
                b"Content-Type: image/jpeg\r\nContent-Length: "
                + str(len(data)).encode()
                + b"\r\n\r\n"
                + data
                + b"\r\n"
            )
        time.sleep(interval)


@app.route("/")
def route_index():
    return _HTML, 200, {
        "Content-Type": "text/html; charset=utf-8",
        "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
        "Pragma": "no-cache",
        "Expires": "0",
    }


@app.route("/video")
def route_video():
    def get():
        with _lock:
            f = _state["rgb"]
            if f is None:
                return None
            frame = f.copy()
        return _draw_face_overlay(frame)
    return Response(_mjpeg_gen(get, fps=20),
                    mimetype="multipart/x-mixed-replace; boundary=frame")


@app.route("/depth_map")
def route_depth_map():
    def get():
        with _lock:
            d     = _state["depth"]
            is_mm = _state["depth_mm"]
            if d is None:
                return None
            d = d.copy()
        if is_mm:
            clipped = np.clip(d.astype(np.float32), 200, 4000)
            norm    = ((clipped - 200) / (4000 - 200) * 255).astype(np.uint8)
        else:
            clipped = np.clip(d.astype(np.float32), 0, 2047)
            norm    = (clipped / 2047 * 255).astype(np.uint8)
        return cv2.applyColorMap(255 - norm, cv2.COLORMAP_JET)
    return Response(_mjpeg_gen(get, fps=10),
                    mimetype="multipart/x-mixed-replace; boundary=frame")


@app.route("/status")
def route_status():
    with _arduino_lock:
        arduino_snapshot = dict(_arduino_state)
    with _lock:
        return jsonify({
            "kinect_ok":  _state["kinect_ok"],
            "motor_ok":   _state["motor_ok"],
            "distance_m": _state["distance_m"],
            "left_clearance_m": _state["left_clearance_m"],
            "center_clearance_m": _state["center_clearance_m"],
            "right_clearance_m": _state["right_clearance_m"],
            "tilt_deg":   _state["tilt_deg"],
            "tilt_cmd":   _state["tilt_cmd"],
            "accel":      _state["accel"],
            "fps_rgb":    _state["fps_rgb"],
            "fps_depth":  _state["fps_depth"],
            "error":      _state["error"],
            "agv":        dict(_agv_control),
            "arduino":    arduino_snapshot,
            "face":       _face_status_snapshot(),
            "runtime":    _runtime_meta_snapshot(),
        })


@app.route("/api/status")
def route_api_status():
    return route_status()


@app.route("/api/video")
def route_api_video():
    return route_video()


@app.route("/api/depth_map")
def route_api_depth_map():
    return route_depth_map()


@app.route("/api/control", methods=["POST"])
def route_api_control():
    payload = request.get_json(silent=True) or {}

    mode = str(payload.get("mode", _agv_control["mode"])).lower()
    if mode not in ("manual", "auto"):
        mode = "manual"

    try:
        speed = int(payload.get("speed", _agv_control["speed"]))
    except Exception:
        speed = _agv_control["speed"]
    try:
        steering = int(payload.get("steering", _agv_control["steering"]))
    except Exception:
        steering = _agv_control["steering"]

    speed = max(-100, min(100, speed))
    steering = max(-100, min(100, steering))

    with _lock:
        _agv_control["mode"] = mode
        _agv_control["speed"] = speed
        _agv_control["steering"] = steering
        _agv_control["updated_at"] = time.time()
        _agv_control["last_source"] = str(payload.get("source", "web"))
        data = dict(_agv_control)
        state_snapshot = dict(_state)
        state_snapshot["agv"] = dict(_agv_control)

    if _brain is not None:
        try:
            _brain.record(state_snapshot, speed=speed, steering=steering, source=data["last_source"], reward=0.0)
        except Exception as exc:
            log.warning("brain record falhou: %s", exc)

    sent = _send_to_arduino(speed=speed, steering=steering, mode=mode, source=data["last_source"])
    data["arduino_sent"] = bool(sent)

    return jsonify({"ok": True, "agv": data})


@app.route("/api/stop", methods=["POST"])
def route_api_stop():
    with _lock:
        _agv_control["speed"] = 0
        _agv_control["steering"] = 0
        _agv_control["updated_at"] = time.time()
        _agv_control["last_source"] = "web-stop"
        data = dict(_agv_control)
        state_snapshot = dict(_state)
        state_snapshot["agv"] = dict(_agv_control)

    if _brain is not None:
        try:
            _brain.record(state_snapshot, speed=0, steering=0, source="web-stop", reward=0.1)
        except Exception as exc:
            log.warning("brain record stop falhou: %s", exc)

    sent = _send_to_arduino(speed=0, steering=0, mode=data.get("mode", "manual"), source="web-stop")
    data["arduino_sent"] = bool(sent)

    return jsonify({"ok": True, "agv": data})


@app.route("/api/faces")
def route_faces_list():
    return jsonify({"ok": True, "face": _face_status_snapshot()})


@app.route("/api/faces/select", methods=["POST"])
def route_faces_select():
    payload = request.get_json(silent=True) or {}
    selected = _safe_ascii_name(payload.get("name", ""))

    with _face_lock:
        if selected and selected not in _face_db.get("people", {}):
            return jsonify({"ok": False, "error": "person_not_found"}), 404
        _face_db["selected"] = selected
        _save_face_db()
    return jsonify({"ok": True, "face": _face_status_snapshot()})


@app.route("/api/faces/mode", methods=["POST"])
def route_faces_mode():
    payload = request.get_json(silent=True) or {}
    enabled = bool(payload.get("enabled", False))

    with _face_lock:
        _face_db["enabled"] = enabled
        _save_face_db()

    return jsonify({"ok": True, "enabled": enabled, "face": _face_status_snapshot()})


@app.route("/api/faces/add", methods=["POST"])
def route_faces_add():
    name = _safe_ascii_name(request.form.get("name", ""))
    image = request.files.get("image")
    if not name:
        return jsonify({"ok": False, "error": "name_required"}), 400
    if image is None:
        return jsonify({"ok": False, "error": "image_required"}), 400

    raw = image.read()
    arr = np.frombuffer(raw, dtype=np.uint8)
    frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if frame is None:
        return jsonify({"ok": False, "error": "invalid_image"}), 400

    signature, _ = _extract_face_signature(frame)
    if signature is None:
        return jsonify({"ok": False, "error": "face_not_detected"}), 400

    with _face_lock:
        people = _face_db.setdefault("people", {})
        samples = people.setdefault(name, [])
        samples.append(signature.tolist())
        people[name] = samples[-30:]
        _face_db["selected"] = name
        _face_db["enabled"] = True
        _save_face_db()

    return jsonify({"ok": True, "name": name, "samples": len(_face_db["people"][name]), "face": _face_status_snapshot()})


@app.route("/api/faces/status")
def route_faces_status():
    return jsonify({"ok": True, "face": _face_status_snapshot()})


@app.route("/api/ai/status")
def route_ai_status():
    if _brain is None:
        return jsonify({"ok": False, "error": "brain_not_initialized"}), 503
    return jsonify({"ok": True, "brain": _brain.status()})


@app.route("/api/ai/recommend")
def route_ai_recommend():
    if _brain is None:
        return jsonify({"ok": False, "error": "brain_not_initialized"}), 503

    with _lock:
        state_snapshot = dict(_state)
        state_snapshot["agv"] = dict(_agv_control)

    rec = _brain.recommend(state_snapshot)
    return jsonify({"ok": True, "recommendation": rec})


@app.route("/api/ai/apply", methods=["POST"])
def route_ai_apply():
    if _brain is None:
        return jsonify({"ok": False, "error": "brain_not_initialized"}), 503

    with _lock:
        state_snapshot = dict(_state)
        state_snapshot["agv"] = dict(_agv_control)

    rec = _brain.recommend(state_snapshot)
    speed = int(np.clip(rec.get("speed", 0), -100, 100))
    steering = int(np.clip(rec.get("steering", 0), -100, 100))

    left = state_snapshot.get("left_clearance_m")
    right = state_snapshot.get("right_clearance_m")
    front = state_snapshot.get("distance_m")

    if left is not None and right is not None:
        side_bias = float(right - left)
        if abs(side_bias) > 0.08:
            preferred = 55 if side_bias > 0 else -55
            if (preferred > 0 and steering < 15) or (preferred < 0 and steering > -15):
                steering = preferred
        rec["side_bias_m"] = side_bias

    if front is not None and front < 0.45:
        speed = min(speed, -25)

    with _lock:
        _agv_control["mode"] = "auto"
        _agv_control["speed"] = speed
        _agv_control["steering"] = steering
        _agv_control["updated_at"] = time.time()
        _agv_control["last_source"] = "ai-apply"
        data = dict(_agv_control)
        state_snapshot2 = dict(_state)
        state_snapshot2["agv"] = dict(_agv_control)

    try:
        _brain.record(state_snapshot2, speed=speed, steering=steering, source="ai-apply", reward=0.0)
    except Exception as exc:
        log.warning("brain record ai-apply falhou: %s", exc)

    return jsonify({"ok": True, "agv": data, "recommendation": rec})


@app.route("/api/ai/feedback", methods=["POST"])
def route_ai_feedback():
    if _brain is None:
        return jsonify({"ok": False, "error": "brain_not_initialized"}), 503

    payload = request.get_json(silent=True) or {}
    try:
        reward = float(payload.get("reward", 0.0))
    except Exception:
        reward = 0.0
    reward = float(np.clip(reward, -2.0, 2.0))

    try:
        _brain.apply_feedback(reward)
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500

    return jsonify({"ok": True, "reward": reward})


@app.route("/api/ai/train", methods=["POST"])
def route_ai_train():
    if _brain is None:
        return jsonify({"ok": False, "error": "brain_not_initialized"}), 503

    payload = request.get_json(silent=True) or {}
    try:
        steps = int(payload.get("steps", 1))
    except Exception:
        steps = 1
    steps = max(1, min(50, steps))

    result = {"ok": True, "runs": []}
    for _ in range(steps):
        result["runs"].append(_brain.train_step())
    return jsonify(result)


def _brain_worker():
    global _brain_running
    while _brain_running:
        time.sleep(2.0)
        if _brain is None:
            continue
        try:
            out = _brain.train_step(batch_size=64)
            if out.get("ok") and out.get("train_steps", 0) % 20 == 0:
                log.info(
                    "brain treino: step=%s loss=%.6f version=%s",
                    out.get("train_steps"),
                    float(out.get("loss", 0.0)),
                    out.get("model_version"),
                )
        except Exception as exc:
            log.warning("brain worker falhou: %s", exc)


# ─────────────────────────────────────────────────────────────────────────────
# PAINEL HTML
# ─────────────────────────────────────────────────────────────────────────────

_HTML = """<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>AGV Neural Command</title>
<style>
    :root {
        --bg:#07111f;
        --bg-soft:#0f1e33;
        --bg-panel:rgba(10, 22, 39, 0.82);
        --bg-panel-2:rgba(14, 29, 50, 0.92);
        --line:rgba(114, 170, 255, 0.18);
        --line-strong:rgba(114, 170, 255, 0.42);
        --text:#edf4ff;
        --muted:#95b2d9;
        --cyan:#44d0ff;
        --teal:#4cf1bc;
        --amber:#ffc56f;
        --red:#ff6a7a;
        --blue:#7aa9ff;
        --shadow:0 24px 60px rgba(0, 0, 0, 0.35);
        --mono:"JetBrains Mono","DejaVu Sans Mono",monospace;
        --heading:"Rajdhani","Ubuntu","DejaVu Sans",sans-serif;
        --body:"Ubuntu","Segoe UI","DejaVu Sans",sans-serif;
    }

    * { box-sizing:border-box; margin:0; padding:0; }

    @keyframes drift {
        0% { background-position:0% 0%, 100% 0%, 0% 0%; }
        50% { background-position:10% 6%, 92% 8%, 0% 0%; }
        100% { background-position:0% 0%, 100% 0%, 0% 0%; }
    }

    @keyframes riseIn {
        from { opacity:0; transform:translateY(16px); }
        to { opacity:1; transform:translateY(0); }
    }

    body {
        min-height:100vh;
        font-family:var(--body);
        color:var(--text);
        background:
            radial-gradient(circle at 8% 10%, rgba(72, 135, 255, 0.28), transparent 28%),
            radial-gradient(circle at 92% 12%, rgba(49, 245, 191, 0.16), transparent 26%),
            linear-gradient(135deg, #06101b 0%, #09192d 45%, #0d2138 100%);
        background-size:120% 120%, 120% 120%, auto;
        animation:drift 18s ease-in-out infinite;
        overflow-x:hidden;
    }

    body::before {
        content:"";
        position:fixed;
        inset:0;
        pointer-events:none;
        background-image:linear-gradient(rgba(255,255,255,0.025) 1px, transparent 1px), linear-gradient(90deg, rgba(255,255,255,0.025) 1px, transparent 1px);
        background-size:42px 42px;
        mask-image:radial-gradient(circle at center, rgba(0,0,0,1) 35%, rgba(0,0,0,0.1) 95%);
        opacity:0.24;
    }

    .app {
        max-width:1600px;
        margin:0 auto;
        padding:22px;
        display:grid;
        grid-template-columns:290px minmax(0, 1fr);
        gap:18px;
    }

    .sidebar,
    .panel,
    .metric,
    .stream-card,
    .chart-card,
    .control-card,
    .ai-card,
    .setting-card,
    .telemetry-card,
    .event-card,
    .brain-card,
    .lane-card {
        background:var(--bg-panel);
        border:1px solid var(--line);
        border-radius:22px;
        box-shadow:var(--shadow);
        backdrop-filter:blur(18px);
        transition:transform .2s ease, border-color .2s ease, box-shadow .2s ease;
    }

    .panel:hover,
    .metric:hover,
    .stream-card:hover,
    .telemetry-card:hover,
    .chart-card:hover,
    .control-card:hover,
    .event-card:hover,
    .brain-card:hover,
    .setting-card:hover,
    .lane-card:hover {
        border-color:rgba(118, 204, 255, 0.34);
        box-shadow:0 28px 62px rgba(0, 0, 0, 0.4);
    }

    .sidebar {
        padding:22px;
        position:sticky;
        top:18px;
        height:calc(100vh - 36px);
        display:flex;
        flex-direction:column;
        gap:18px;
    }

    .brand {
        padding:16px 16px 18px;
        border-radius:18px;
        background:linear-gradient(180deg, rgba(42, 82, 136, 0.45), rgba(15, 31, 53, 0.25));
        border:1px solid rgba(126, 176, 255, 0.2);
    }

    .eyebrow,
    .section-tag,
    .mini-label,
    .stat-label,
    .card-title,
    .nav small,
    .lane-card small,
    .setting-card label,
    .bar-caption {
        text-transform:uppercase;
        letter-spacing:0.16em;
        font-size:11px;
        color:#8fb5ea;
    }

    .brand h1,
    .page-head h2,
    .stream-header h3,
    .control-card h3,
    .telemetry-card h3,
    .ai-card h3,
    .brain-card h3,
    .event-card h3,
    .setting-card h3 {
        font-family:var(--heading);
        font-weight:700;
        letter-spacing:0.04em;
    }

    .brand h1 {
        margin-top:10px;
        font-size:34px;
        line-height:0.95;
        background:linear-gradient(135deg, #ffffff, #79d4ff 55%, #76ffc6 100%);
        -webkit-background-clip:text;
        -webkit-text-fill-color:transparent;
        background-clip:text;
    }

    .brand p {
        margin-top:10px;
        color:var(--muted);
        line-height:1.45;
        font-size:14px;
    }

    .quick-status {
        display:grid;
        gap:10px;
    }

    .status-pill {
        display:flex;
        align-items:center;
        justify-content:space-between;
        gap:12px;
        padding:12px 14px;
        border-radius:16px;
        background:rgba(9, 20, 35, 0.86);
        border:1px solid rgba(120, 171, 255, 0.14);
    }

    .status-pill strong {
        font-size:13px;
    }

    .runtime-card {
        padding:12px 14px;
        border-radius:16px;
        background:linear-gradient(180deg, rgba(12, 26, 44, 0.92), rgba(7, 16, 28, 0.95));
        border:1px solid rgba(120, 171, 255, 0.18);
    }

    .runtime-grid {
        display:grid;
        gap:8px;
        margin-top:8px;
    }

    .runtime-item {
        display:flex;
        align-items:center;
        justify-content:space-between;
        gap:10px;
        font-size:12px;
        color:#c8ddfb;
    }

    .runtime-item .mono {
        color:#8cf5cb;
    }

    .status-dot {
        width:11px;
        height:11px;
        border-radius:999px;
        background:#56759d;
        box-shadow:0 0 0 5px rgba(86, 117, 157, 0.16);
    }

    .status-dot.ok { background:var(--teal); box-shadow:0 0 0 6px rgba(76, 241, 188, 0.18); }
    .status-dot.warn { background:var(--amber); box-shadow:0 0 0 6px rgba(255, 197, 111, 0.16); }
    .status-dot.err { background:var(--red); box-shadow:0 0 0 6px rgba(255, 106, 122, 0.16); }

    .nav {
        display:grid;
        gap:10px;
    }

    .tab-btn {
        width:100%;
        border:1px solid rgba(123, 177, 255, 0.16);
        border-radius:18px;
        background:rgba(12, 24, 42, 0.86);
        color:var(--text);
        text-align:left;
        padding:14px 16px;
        cursor:pointer;
        transition:transform .18s ease, border-color .18s ease, background .18s ease;
    }

    .tab-btn:hover,
    .tab-btn.active {
        transform:translateX(4px);
        border-color:rgba(122, 209, 255, 0.48);
        background:linear-gradient(135deg, rgba(41, 82, 138, 0.92), rgba(20, 48, 83, 0.96));
    }

    .tab-btn strong {
        display:block;
        font-family:var(--heading);
        font-size:18px;
        margin-bottom:2px;
    }

    .tab-btn small {
        display:block;
        letter-spacing:0.08em;
        color:#b7cff1;
        font-size:10px;
    }

    .side-note {
        margin-top:auto;
        padding:14px 16px;
        border-radius:18px;
        background:linear-gradient(180deg, rgba(17, 35, 60, 0.88), rgba(9, 18, 31, 0.92));
        border:1px solid rgba(124, 173, 255, 0.14);
    }

    .side-note p {
        margin-top:8px;
        font-size:13px;
        line-height:1.5;
        color:var(--muted);
    }

    .main {
        display:grid;
        gap:18px;
        min-width:0;
    }

    .panel {
        padding:22px;
        animation:riseIn .3s ease;
    }

    .page-head {
        display:flex;
        justify-content:space-between;
        gap:18px;
        align-items:flex-start;
        margin-bottom:18px;
    }

    .page-head h2 {
        font-size:38px;
        line-height:0.95;
    }

    .page-head p {
        margin-top:10px;
        max-width:700px;
        color:var(--muted);
        line-height:1.55;
        font-size:14px;
    }

    .head-actions {
        display:flex;
        flex-wrap:wrap;
        justify-content:flex-end;
        gap:10px;
    }

    .ghost,
    .action,
    .mode-btn,
    .quick-action,
    .train-btn,
    .feedback-btn {
        border:none;
        border-radius:14px;
        cursor:pointer;
        font-family:var(--body);
        font-weight:700;
        transition:transform .18s ease, filter .18s ease, background .18s ease;
    }

    .ghost:hover,
    .action:hover,
    .mode-btn:hover,
    .quick-action:hover,
    .train-btn:hover,
    .feedback-btn:hover {
        transform:translateY(-1px);
        filter:brightness(1.06);
    }

    .ghost {
        background:rgba(14, 28, 49, 0.85);
        color:var(--text);
        border:1px solid rgba(123, 177, 255, 0.16);
        padding:11px 14px;
    }

    .action {
        background:linear-gradient(135deg, #35bdf6, #2cf1c0);
        color:#04111c;
        padding:12px 16px;
    }

    .top-metrics,
    .status-metrics {
        display:grid;
        gap:12px;
    }

    .top-metrics {
        grid-template-columns:repeat(5, minmax(0, 1fr));
        margin-bottom:18px;
    }

    .metric.runtime-metric {
        background:linear-gradient(145deg, rgba(20, 42, 69, 0.95), rgba(8, 19, 34, 0.98));
        border-color:rgba(118, 204, 255, 0.28);
    }

    .build-chip {
        display:inline-flex;
        align-items:center;
        gap:8px;
        padding:6px 10px;
        border-radius:999px;
        border:1px solid rgba(132, 199, 255, 0.32);
        background:rgba(19, 40, 65, 0.72);
        font-size:11px;
        letter-spacing:0.08em;
        color:#bfe3ff;
    }

    .build-chip::before {
        content:"";
        width:8px;
        height:8px;
        border-radius:50%;
        background:#55f2be;
        box-shadow:0 0 0 4px rgba(85, 242, 190, 0.16);
    }

    .metric {
        padding:16px 18px;
        position:relative;
        overflow:hidden;
        min-height:112px;
    }

    .metric::after {
        content:"";
        position:absolute;
        width:110px;
        height:110px;
        right:-24px;
        bottom:-40px;
        background:radial-gradient(circle, rgba(71, 205, 255, 0.22), transparent 65%);
    }

    .metric .stat-value {
        display:block;
        margin-top:14px;
        font-family:var(--heading);
        font-size:33px;
        font-weight:700;
        letter-spacing:0.03em;
    }

    .metric .stat-hint {
        margin-top:10px;
        font-size:13px;
        color:var(--muted);
    }

    .grid-overview {
        display:grid;
        grid-template-columns:minmax(0, 1.2fr) minmax(0, 1.2fr) 380px;
        gap:14px;
    }

    .stream-card,
    .telemetry-card,
    .event-card,
    .control-card,
    .ai-card,
    .brain-card,
    .setting-card,
    .lane-card,
    .chart-card {
        padding:16px;
        min-width:0;
    }

    .stream-header,
    .card-top {
        display:flex;
        justify-content:space-between;
        align-items:flex-start;
        gap:14px;
        margin-bottom:12px;
    }

    .stream-header h3,
    .card-top h3 {
        font-size:22px;
    }

    .stream-header p,
    .card-top p {
        margin-top:4px;
        color:var(--muted);
        font-size:13px;
        line-height:1.45;
    }

    .live-pill,
    .metric-badge,
    .risk-badge,
    .command-badge {
        white-space:nowrap;
        border-radius:999px;
        padding:7px 12px;
        font-size:11px;
        font-weight:700;
        letter-spacing:0.08em;
    }

    .live-pill { background:rgba(58, 232, 175, 0.12); color:#7bf7cd; border:1px solid rgba(58, 232, 175, 0.25); }
    .metric-badge { background:rgba(72, 136, 255, 0.12); color:#9cc0ff; border:1px solid rgba(72, 136, 255, 0.24); }
    .command-badge { background:rgba(255, 197, 111, 0.12); color:#ffd38d; border:1px solid rgba(255, 197, 111, 0.24); }
    .risk-badge.low { background:rgba(76, 241, 188, 0.14); color:#88ffd7; border:1px solid rgba(76, 241, 188, 0.24); }
    .risk-badge.medium { background:rgba(255, 197, 111, 0.14); color:#ffd694; border:1px solid rgba(255, 197, 111, 0.24); }
    .risk-badge.high { background:rgba(255, 106, 122, 0.14); color:#ff9ba6; border:1px solid rgba(255, 106, 122, 0.24); }

    .stream {
        width:100%;
        display:block;
        aspect-ratio:4 / 3;
        object-fit:cover;
        border-radius:18px;
        background:#000;
        border:1px solid rgba(123, 177, 255, 0.14);
    }

    .stream-footer {
        display:flex;
        justify-content:space-between;
        gap:10px;
        margin-top:12px;
        color:var(--muted);
        font-size:12px;
    }

    .decision-hero {
        padding:16px;
        border-radius:18px;
        background:linear-gradient(135deg, rgba(37, 78, 132, 0.74), rgba(14, 27, 48, 0.92));
        border:1px solid rgba(123, 177, 255, 0.22);
        margin-bottom:14px;
    }

    .decision-hero p {
        margin-top:10px;
        color:#d5e7ff;
        line-height:1.55;
        font-size:14px;
    }

    .decision-grid,
    .rows,
    .mode-row,
    .quick-grid,
    .mini-grid,
    .brain-grid,
    .settings-grid,
    .analysis-grid,
    .lane-grid {
        display:grid;
        gap:10px;
    }

    .decision-grid { grid-template-columns:repeat(3, minmax(0, 1fr)); margin-top:12px; }

    .info-chip,
    .mini-stat,
    .lane-card {
        padding:12px 14px;
        border-radius:16px;
        background:rgba(8, 19, 33, 0.68);
        border:1px solid rgba(123, 177, 255, 0.12);
    }

    .info-chip strong,
    .mini-stat strong,
    .lane-card strong {
        display:block;
        margin-top:8px;
        font-size:18px;
    }

    .rows {
        margin-top:4px;
    }

    .row {
        display:flex;
        justify-content:space-between;
        align-items:center;
        gap:14px;
        padding:12px 0;
        border-bottom:1px solid rgba(120, 171, 255, 0.1);
    }

    .row:last-child {
        border-bottom:none;
    }

    .row .label {
        color:var(--muted);
        font-size:13px;
    }

    .row .value,
    .mono {
        font-family:var(--mono);
        font-size:13px;
    }

    .row .value {
        text-align:right;
    }

    .value.ok { color:var(--teal); }
    .value.warn { color:var(--amber); }
    .value.err { color:var(--red); }
    .value.info { color:#9fc8ff; }

    .progress-wrap,
    .brain-bar {
        margin-top:12px;
        height:12px;
        border-radius:999px;
        overflow:hidden;
        background:rgba(13, 26, 45, 0.92);
        border:1px solid rgba(123, 177, 255, 0.14);
    }

    .progress-bar,
    .brain-fill {
        height:100%;
        width:0%;
        transition:width .2s ease;
        background:linear-gradient(90deg, #38cff5, #47efbe);
    }

    .chart-grid,
    .control-layout,
    .intelligence-layout,
    .config-layout {
        display:grid;
        gap:14px;
    }

    .chart-grid {
        grid-template-columns:repeat(3, minmax(0, 1fr));
        margin-top:14px;
    }

    .chart-card canvas,
    .lane-card canvas {
        width:100%;
        height:190px;
        background:linear-gradient(180deg, rgba(7, 16, 28, 0.98), rgba(8, 18, 31, 0.92));
        border-radius:16px;
        border:1px solid rgba(123, 177, 255, 0.12);
    }

    .chart-card canvas { height:170px; }

    .minimap-wrap {
        margin-top:10px;
        border-radius:16px;
        border:1px solid rgba(123, 177, 255, 0.12);
        overflow:hidden;
        background:linear-gradient(180deg, rgba(6, 13, 22, 0.98), rgba(6, 12, 20, 0.98));
    }

    .minimap-wrap canvas {
        width:100%;
        height:260px;
        display:block;
    }

    .event-card .logs {
        height:100%;
        min-height:240px;
    }

    .logs {
        overflow:auto;
        border-radius:16px;
        background:rgba(6, 14, 24, 0.9);
        border:1px solid rgba(123, 177, 255, 0.12);
        padding:10px;
    }

    .log-item {
        padding:8px 6px;
        border-bottom:1px solid rgba(120, 171, 255, 0.08);
        font-size:12px;
        line-height:1.45;
        color:#d6e5fb;
    }

    .log-item:last-child {
        border-bottom:none;
    }

    .log-time {
        color:#79b8ff;
        margin-right:8px;
        font-family:var(--mono);
    }

    .telemetry-stack {
        display:grid;
        grid-template-columns:repeat(2, minmax(0, 1fr));
        gap:12px;
    }

    .mode-row {
        grid-template-columns:repeat(2, minmax(0, 1fr));
    }

    .mode-btn {
        padding:14px 16px;
        background:rgba(11, 23, 40, 0.9);
        color:#dfeaff;
        border:1px solid rgba(123, 177, 255, 0.16);
    }

    .mode-btn.active {
        background:linear-gradient(135deg, rgba(48, 188, 242, 0.98), rgba(56, 225, 192, 0.94));
        color:#031019;
        border-color:transparent;
    }

    .drive-pad {
        margin-top:16px;
        display:grid;
        grid-template-columns:repeat(3, minmax(0, 1fr));
        gap:10px;
    }

    .pad-key {
        min-height:84px;
        border:none;
        border-radius:18px;
        background:linear-gradient(180deg, rgba(15, 31, 53, 0.96), rgba(8, 16, 29, 0.96));
        color:#e9f4ff;
        font-family:var(--heading);
        font-size:28px;
        font-weight:700;
        cursor:pointer;
        border:1px solid rgba(123, 177, 255, 0.16);
        transition:transform .1s ease, border-color .12s ease, background .12s ease;
        user-select:none;
        touch-action:none;
    }

    .pad-key.empty {
        visibility:hidden;
    }

    .pad-key.active {
        transform:translateY(1px);
        background:linear-gradient(135deg, rgba(53, 189, 246, 0.98), rgba(45, 242, 187, 0.86));
        color:#04111d;
        border-color:transparent;
        box-shadow:0 0 0 3px rgba(61, 212, 255, 0.14);
    }

    .quick-grid {
        margin-top:14px;
        grid-template-columns:repeat(2, minmax(0, 1fr));
    }

    .quick-action,
    .train-btn,
    .feedback-btn {
        padding:12px 14px;
        background:rgba(11, 24, 40, 0.92);
        color:#e5f0ff;
        border:1px solid rgba(123, 177, 255, 0.16);
    }

    .quick-action.danger {
        background:rgba(63, 18, 28, 0.92);
        border-color:rgba(255, 106, 122, 0.22);
        color:#ffd6dc;
    }

    .vector {
        margin-top:16px;
        padding:16px;
        border-radius:18px;
        background:linear-gradient(180deg, rgba(9, 19, 33, 0.96), rgba(7, 15, 26, 0.96));
        border:1px solid rgba(123, 177, 255, 0.12);
    }

    .vector-gauge {
        position:relative;
        height:220px;
        border-radius:18px;
        overflow:hidden;
        background:radial-gradient(circle at 50% 90%, rgba(58, 189, 245, 0.12), transparent 50%), linear-gradient(180deg, rgba(10, 18, 31, 0.96), rgba(5, 11, 20, 0.98));
        border:1px solid rgba(123, 177, 255, 0.1);
    }

    .vector-gauge::before,
    .vector-gauge::after {
        content:"";
        position:absolute;
        background:rgba(121, 169, 255, 0.18);
    }

    .vector-gauge::before {
        left:50%;
        top:14px;
        bottom:14px;
        width:1px;
    }

    .vector-gauge::after {
        left:16px;
        right:16px;
        top:50%;
        height:1px;
    }

    .vector-arrow {
        position:absolute;
        left:50%;
        bottom:24px;
        width:6px;
        height:42%;
        border-radius:999px;
        background:linear-gradient(180deg, #4ff2be, #39c7f1);
        transform-origin:center bottom;
        transform:translateX(-50%) rotate(0deg);
        box-shadow:0 0 18px rgba(61, 212, 255, 0.4);
    }

    .vector-arrow::before {
        content:"";
        position:absolute;
        top:-18px;
        left:50%;
        transform:translateX(-50%);
        border-left:12px solid transparent;
        border-right:12px solid transparent;
        border-bottom:20px solid #4ff2be;
    }

    .vector-readout {
        display:grid;
        grid-template-columns:repeat(3, minmax(0, 1fr));
        gap:10px;
        margin-top:12px;
    }

    .mini-grid {
        grid-template-columns:repeat(3, minmax(0, 1fr));
        margin-top:10px;
    }

    .analysis-grid {
        grid-template-columns:minmax(0, 1.1fr) minmax(0, .9fr);
    }

    .lane-grid {
        grid-template-columns:repeat(3, minmax(0, 1fr));
        margin-top:12px;
    }

    .lane-card strong {
        font-family:var(--heading);
        font-size:24px;
    }

    .brain-grid {
        grid-template-columns:repeat(4, minmax(0, 1fr));
        margin-top:14px;
    }

    .brain-stat {
        padding:12px 14px;
        border-radius:16px;
        background:rgba(7, 16, 28, 0.76);
        border:1px solid rgba(123, 177, 255, 0.12);
    }

    .brain-stat strong {
        display:block;
        margin-top:8px;
        font-family:var(--heading);
        font-size:24px;
    }

    .brain-actions,
    .feedback-row {
        display:flex;
        flex-wrap:wrap;
        gap:10px;
        margin-top:14px;
    }

    .brain-explain {
        margin-top:14px;
        padding:14px 16px;
        border-radius:18px;
        background:rgba(9, 19, 33, 0.84);
        border:1px solid rgba(123, 177, 255, 0.12);
    }

    .brain-explain p {
        margin-top:8px;
        color:var(--muted);
        line-height:1.55;
        font-size:13px;
    }

    .settings-grid {
        grid-template-columns:repeat(2, minmax(0, 1fr));
    }

    .setting-card input[type="range"] {
        width:100%;
        margin-top:16px;
        accent-color:#37c6f1;
    }

    .setting-card .setting-value {
        margin-top:12px;
        font-family:var(--mono);
        color:#87ffd6;
        font-size:13px;
    }

    .timeline {
        margin-top:14px;
        display:grid;
        gap:8px;
    }

    .timeline-item {
        display:grid;
        grid-template-columns:84px 1fr;
        gap:10px;
        align-items:center;
    }

    .timeline-time {
        font-family:var(--mono);
        font-size:11px;
        color:#7eb8ff;
    }

    .timeline-track {
        display:grid;
        gap:4px;
    }

    .timeline-bar {
        height:10px;
        border-radius:999px;
        overflow:hidden;
        background:rgba(12, 24, 41, 0.94);
        border:1px solid rgba(123, 177, 255, 0.1);
    }

    .timeline-fill {
        height:100%;
        background:linear-gradient(90deg, #40cbf2, #49f0bb);
    }

    .timeline-label {
        font-size:11px;
        color:#d3e5ff;
        margin-top:4px;
    }

    .muted {
        color:var(--muted);
    }

    .hidden {
        display:none;
    }

    .face-grid {
        display:grid;
        grid-template-columns:repeat(2, minmax(0, 1fr));
        gap:10px;
        margin-top:12px;
    }

    .face-controls {
        display:flex;
        flex-wrap:wrap;
        gap:10px;
        margin-top:14px;
    }

    .face-controls select,
    .face-controls button {
        border:none;
        border-radius:12px;
        background:rgba(11, 24, 40, 0.92);
        color:#e5f0ff;
        border:1px solid rgba(123, 177, 255, 0.16);
        padding:10px 12px;
        font-family:var(--body);
        font-weight:700;
    }

    .face-chip {
        display:inline-flex;
        align-items:center;
        gap:8px;
        border-radius:999px;
        padding:7px 12px;
        font-size:11px;
        font-weight:700;
        letter-spacing:0.08em;
        border:1px solid rgba(123, 177, 255, 0.24);
        background:rgba(72, 136, 255, 0.12);
        color:#9cc0ff;
    }

    .face-chip.known {
        background:rgba(76, 241, 188, 0.14);
        color:#88ffd7;
        border-color:rgba(76, 241, 188, 0.24);
    }

    .face-chip.unknown {
        background:rgba(255, 106, 122, 0.14);
        color:#ffb3bb;
        border-color:rgba(255, 106, 122, 0.24);
    }

    .modal {
        position:fixed;
        inset:0;
        z-index:40;
        background:rgba(2, 7, 12, 0.72);
        display:flex;
        align-items:center;
        justify-content:center;
        padding:16px;
    }

    .modal.hidden {
        display:none;
    }

    .modal-card {
        width:min(460px, 96vw);
        border-radius:18px;
        background:linear-gradient(180deg, rgba(13, 28, 48, 0.98), rgba(7, 14, 25, 0.98));
        border:1px solid rgba(123, 177, 255, 0.2);
        padding:18px;
        position:relative;
    }

    .modal-head {
        display:flex;
        align-items:flex-start;
        justify-content:space-between;
        gap:10px;
    }

    .modal-close {
        width:34px;
        height:34px;
        border-radius:10px;
        border:1px solid rgba(123, 177, 255, 0.22);
        background:rgba(9, 19, 33, 0.92);
        color:#dcecff;
        font-size:18px;
        line-height:1;
        font-weight:700;
        cursor:pointer;
    }

    .modal-close:hover {
        border-color:rgba(123, 177, 255, 0.34);
        background:rgba(14, 29, 49, 0.96);
    }

    .modal-card h3 {
        font-family:var(--heading);
        font-size:26px;
    }

    .toast-stack {
        position:fixed;
        top:18px;
        right:18px;
        z-index:80;
        display:grid;
        gap:10px;
        width:min(340px, calc(100vw - 28px));
        pointer-events:none;
    }

    .toast {
        border-radius:16px;
        border:1px solid rgba(123, 177, 255, 0.18);
        background:rgba(7, 16, 28, 0.94);
        box-shadow:0 20px 60px rgba(0, 0, 0, 0.32);
        padding:12px 14px;
        transform:translateY(0);
        opacity:1;
        transition:opacity 0.22s ease, transform 0.22s ease;
    }

    .toast.fade-out {
        opacity:0;
        transform:translateY(-6px);
    }

    .toast-title {
        display:block;
        font-family:var(--heading);
        font-size:16px;
        color:#eef5ff;
        margin-bottom:4px;
    }

    .toast-message {
        display:block;
        font-size:13px;
        color:#d8e6ff;
        line-height:1.45;
    }

    .toast.known {
        border-color:rgba(76, 241, 188, 0.34);
        background:rgba(5, 31, 24, 0.94);
    }

    .toast.unknown {
        border-color:rgba(255, 175, 91, 0.34);
        background:rgba(39, 22, 7, 0.94);
    }

    .toast.info {
        border-color:rgba(123, 177, 255, 0.28);
        background:rgba(7, 16, 28, 0.94);
    }

    .modal-form {
        display:grid;
        gap:12px;
        margin-top:12px;
    }

    .modal-form input {
        width:100%;
        border-radius:12px;
        background:rgba(9, 19, 33, 0.92);
        border:1px solid rgba(123, 177, 255, 0.16);
        color:#e8f3ff;
        padding:11px 12px;
        font-family:var(--body);
    }

    .modal-actions {
        display:flex;
        gap:10px;
        margin-top:8px;
    }

    .tab {
        display:none;
    }

    .tab.active {
        display:block;
    }

    @media (max-width:1320px) {
        .app {
            grid-template-columns:1fr;
        }

        .sidebar {
            position:relative;
            top:auto;
            height:auto;
        }

        .grid-overview,
        .analysis-grid,
        .control-layout,
        .intelligence-layout,
        .config-layout {
            grid-template-columns:1fr;
        }
    }

    @media (max-width:1100px) {
        .top-metrics,
        .chart-grid,
        .brain-grid,
        .telemetry-stack,
        .settings-grid,
        .decision-grid,
        .vector-readout,
        .lane-grid {
            grid-template-columns:1fr 1fr;
        }

        .top-metrics {
            grid-template-columns:1fr 1fr;
        }

        .grid-overview {
            grid-template-columns:1fr 1fr;
        }

        .grid-overview > .telemetry-card {
            grid-column:1 / -1;
        }
    }

    @media (max-width:760px) {
        .app,
        .panel {
            padding:14px;
        }

        .page-head {
            flex-direction:column;
        }

        .head-actions {
            justify-content:flex-start;
        }

        .top-metrics,
        .chart-grid,
        .brain-grid,
        .telemetry-stack,
        .settings-grid,
        .decision-grid,
        .vector-readout,
        .lane-grid,
        .quick-grid,
        .mode-row,
        .mini-grid,
        .grid-overview {
            grid-template-columns:1fr;
        }

        .page-head h2 {
            font-size:30px;
        }
    }
</style>
</head>
<body>
    <div class="app">
        <aside class="sidebar">
            <div class="brand">
                <div class="eyebrow">AGV Neural Stack</div>
                <h1>Command Grid</h1>
                <p>Painel unico para video, profundidade, conducao estilo jogo, radar espacial e rede neural com banco de experiencias.</p>
            </div>

            <div class="quick-status">
                <div class="status-pill">
                    <div>
                        <div class="mini-label">Rede</div>
                        <strong id="side-link-status">conectando</strong>
                    </div>
                    <span id="side-link-dot" class="status-dot warn"></span>
                </div>
                <div class="status-pill">
                    <div>
                        <div class="mini-label">Modo AGV</div>
                        <strong id="side-mode">manual</strong>
                    </div>
                    <span id="side-mode-dot" class="status-dot ok"></span>
                </div>
                <div class="status-pill">
                    <div>
                        <div class="mini-label">Espaco frontal</div>
                        <strong id="side-distance">--</strong>
                    </div>
                    <span id="side-distance-dot" class="status-dot warn"></span>
                </div>
            </div>

            <div class="runtime-card">
                <div class="section-tag">Runtime</div>
                <div class="runtime-grid">
                    <div class="runtime-item">
                        <span>Modo</span>
                        <strong id="side-runtime-mode" class="mono">--</strong>
                    </div>
                    <div class="runtime-item">
                        <span>Revisao UI</span>
                        <strong id="side-frontend-rev" class="mono">--</strong>
                    </div>
                    <div class="runtime-item">
                        <span>Build local</span>
                        <strong id="side-build-time" class="mono">--</strong>
                    </div>
                </div>
            </div>

            <nav class="nav">
                <button class="tab-btn active" data-tab="overview">
                    <strong>Overview</strong>
                    <small>streams e resumo operacional</small>
                </button>
                <button class="tab-btn" data-tab="drive">
                    <strong>Drive</strong>
                    <small>controle WASD e vetor de comando</small>
                </button>
                <button class="tab-btn" data-tab="intelligence">
                    <strong>Neural</strong>
                    <small>IA real, treino e radar</small>
                </button>
                <button class="tab-btn" data-tab="settings">
                    <strong>Settings</strong>
                    <small>parametros do painel</small>
                </button>
            </nav>

            <div class="side-note">
                <div class="section-tag">Operacao</div>
                <p>Segure W A S D para enviar movimento continuo. Ao perder foco da janela o painel manda STOP automatico.</p>
                <div class="rows" style="margin-top:10px;">
                    <div class="row"><span class="label">Loop teclado</span><span id="side-loop" class="value info mono">90 ms</span></div>
                    <div class="row"><span class="label">Ultima fonte</span><span id="side-source" class="value info mono">--</span></div>
                </div>
            </div>
        </aside>

        <main class="main">
            <section id="tab-overview" class="panel tab active">
                <div class="page-head">
                    <div>
                        <div class="section-tag">Painel principal</div>
                        <h2>Visao operacional do AGV</h2>
                        <div id="front-build-chip" class="build-chip" style="margin-top:10px;">build --</div>
                        <p>O painel foi reorganizado para destacar o que interessa primeiro: saude do sensor, distancias, comando atual e leitura da rede neural antes de qualquer detalhe tecnico.</p>
                    </div>
                    <div class="head-actions">
                        <button class="ghost" type="button" onclick="hardRefresh()">recarregar pagina</button>
                        <button class="ghost" type="button" onclick="focusTab('drive')">abrir controle</button>
                        <button class="ghost" type="button" onclick="focusTab('intelligence')">abrir reconhecimento</button>
                        <button class="ghost" type="button" onclick="focusTab('intelligence'); openFaceModal();">cadastrar rosto</button>
                        <button class="action" type="button" onclick="applyAiCommand()">aplicar recomendacao IA</button>
                    </div>
                </div>

                <div class="top-metrics">
                    <article class="metric">
                        <div class="stat-label">Distancia central</div>
                        <span id="metric-distance" class="stat-value">--</span>
                        <div id="metric-distance-hint" class="stat-hint">aguardando profundidade</div>
                    </article>
                    <article class="metric">
                        <div class="stat-label">Recomendacao</div>
                        <span id="metric-ai-action" class="stat-value">--</span>
                        <div id="metric-ai-hint" class="stat-hint">sem leitura</div>
                    </article>
                    <article class="metric">
                        <div class="stat-label">Comando atual</div>
                        <span id="metric-command" class="stat-value">0 / 0</span>
                        <div id="metric-command-hint" class="stat-hint">speed / steering</div>
                    </article>
                    <article class="metric">
                        <div class="stat-label">Ciclo neural</div>
                        <span id="metric-brain-version" class="stat-value">v--</span>
                        <div id="metric-brain-hint" class="stat-hint">sem status da IA</div>
                    </article>
                    <article class="metric runtime-metric">
                        <div class="stat-label">Revisao do frontend</div>
                        <span id="metric-front-revision" class="stat-value">--</span>
                        <div id="metric-front-hint" class="stat-hint">validando build em execucao</div>
                    </article>
                </div>

                <div class="grid-overview">
                    <article class="stream-card">
                        <div class="stream-header">
                            <div>
                                <div class="section-tag">Sensor RGB</div>
                                <h3>Camera frontal</h3>
                                <p>Video principal usado para inspeção visual do ambiente.</p>
                            </div>
                            <span class="live-pill">LIVE RGB</span>
                        </div>
                        <img class="stream" src="/video" alt="Fluxo RGB do Kinect">
                        <div class="stream-footer">
                            <span id="rgb-status">fluxo em tempo real</span>
                            <span id="rgb-fps-foot" class="mono">fps --</span>
                        </div>
                    </article>

                    <article class="stream-card">
                        <div class="stream-header">
                            <div>
                                <div class="section-tag">Profundidade</div>
                                <h3>Mapa espacial</h3>
                                <p>Base para calculo de distancia e leitura de risco frontal.</p>
                            </div>
                            <span class="live-pill">LIVE DEPTH</span>
                        </div>
                        <img class="stream" src="/depth_map" alt="Mapa de profundidade do Kinect">
                        <div class="stream-footer">
                            <span id="depth-status">monitorando corredor</span>
                            <span id="depth-fps-foot" class="mono">fps --</span>
                        </div>
                    </article>

                    <article class="telemetry-card">
                        <div class="card-top">
                            <div>
                                <div class="section-tag">Leitura sintetica</div>
                                <h3>Resumo neural</h3>
                                <p>Explicacao curta do momento atual para nao precisar ler tudo.</p>
                            </div>
                            <span id="risk-badge" class="risk-badge low">RISCO BAIXO</span>
                        </div>

                        <div class="decision-hero">
                            <div class="section-tag">Decisao destacada</div>
                            <h3 id="decision-headline" style="font-size:28px; margin-top:8px;">Aguardando dados</h3>
                            <p id="decision-box">Sem profundidade suficiente para gerar leitura operacional.</p>
                        </div>

                        <div class="decision-grid">
                            <div class="info-chip">
                                <div class="mini-label">Acao sugerida</div>
                                <strong id="ai-action">--</strong>
                            </div>
                            <div class="info-chip">
                                <div class="mini-label">Confianca</div>
                                <strong id="ai-confidence">--</strong>
                            </div>
                            <div class="info-chip">
                                <div class="mini-label">Motivo</div>
                                <strong id="ai-why">--</strong>
                            </div>
                        </div>

                        <div class="progress-wrap">
                            <div id="dist-bar" class="progress-bar"></div>
                        </div>
                        <div class="stream-footer">
                            <span class="bar-caption">ocupacao de risco frontal</span>
                            <span id="safe-window" class="mono">janela segura --</span>
                        </div>

                        <div class="rows">
                            <div class="row"><span class="label">Kinect</span><span id="kok" class="value info">--</span></div>
                            <div class="row"><span class="label">Motor do sensor</span><span id="mok" class="value info">--</span></div>
                            <div class="row"><span class="label">Tilt lido</span><span id="tilt" class="value info mono">--</span></div>
                            <div class="row"><span class="label">Tilt comandado</span><span id="tiltcmd" class="value info mono">--</span></div>
                            <div class="row"><span class="label">Accel X / Y / Z</span><span id="accel" class="value info mono">--</span></div>
                            <div class="row"><span class="label">Erro do sistema</span><span id="err-msg" class="value err">nenhum</span></div>
                        </div>
                    </article>
                </div>

                <div class="chart-grid">
                    <article class="chart-card">
                        <div class="card-top">
                            <div>
                                <div class="section-tag">Historico</div>
                                <h3>Distancia</h3>
                                <p>Variacao frontal nos ultimos ciclos.</p>
                            </div>
                            <span class="metric-badge">4 m max</span>
                        </div>
                        <canvas id="chart-distance" width="420" height="170"></canvas>
                    </article>

                    <article class="chart-card">
                        <div class="card-top">
                            <div>
                                <div class="section-tag">Comando</div>
                                <h3>Speed e steering</h3>
                                <p>Ultimos comandos recebidos pelo AGV.</p>
                            </div>
                            <span class="command-badge">-100 a 100</span>
                        </div>
                        <canvas id="chart-control" width="420" height="170"></canvas>
                    </article>

                    <article class="event-card">
                        <div class="card-top">
                            <div>
                                <div class="section-tag">Eventos</div>
                                <h3>Log operacional</h3>
                                <p>Mudancas de modo, teclas e alertas da IA.</p>
                            </div>
                            <button class="ghost" type="button" onclick="clearLogs()">limpar</button>
                        </div>
                        <div id="event-log" class="logs"></div>
                    </article>
                </div>
            </section>

            <section id="tab-drive" class="panel tab">
                <div class="page-head">
                    <div>
                        <div class="section-tag">Controle</div>
                        <h2>Conducao direta</h2>
                        <p>Controle estilo jogo, com envio continuo enquanto a tecla estiver pressionada. O bloco ao lado mostra a direcao de vetor do comando em tempo real.</p>
                    </div>
                    <div class="head-actions">
                        <button class="ghost" type="button" onclick="setMode('manual')">modo manual</button>
                        <button class="ghost" type="button" onclick="setMode('auto')">modo auto</button>
                        <button class="action" type="button" onclick="forceStop()">stop imediato</button>
                    </div>
                </div>

                <div class="analysis-grid">
                    <article class="control-card">
                        <div class="card-top">
                            <div>
                                <div class="section-tag">Pilotagem</div>
                                <h3>Pad de movimento</h3>
                                <p>W frente, S re, A esquerda, D direita. Soltou, parou.</p>
                            </div>
                            <span id="mode-badge" class="metric-badge">manual</span>
                        </div>

                        <div class="mode-row">
                            <button id="btn-manual" class="mode-btn active" type="button" onclick="setMode('manual')">Manual</button>
                            <button id="btn-auto" class="mode-btn" type="button" onclick="setMode('auto')">Auto</button>
                        </div>

                        <div class="drive-pad">
                            <button class="pad-key empty" type="button">.</button>
                            <button class="pad-key" id="k-w" data-key="w" type="button">W</button>
                            <button class="pad-key empty" type="button">.</button>
                            <button class="pad-key" id="k-a" data-key="a" type="button">A</button>
                            <button class="pad-key" id="k-s" data-key="s" type="button">S</button>
                            <button class="pad-key" id="k-d" data-key="d" type="button">D</button>
                        </div>

                        <div class="quick-grid">
                            <button class="quick-action" type="button" onclick="sendManualCmd(cfg.forwardBoost, 0)">boost frente</button>
                            <button class="quick-action" type="button" onclick="sendManualCmd(-cfg.reverseBase, 0)">re reta</button>
                            <button class="quick-action" type="button" onclick="sendManualCmd(cfg.forwardBase, -cfg.turnBase)">curva esquerda</button>
                            <button class="quick-action" type="button" onclick="sendManualCmd(cfg.forwardBase, cfg.turnBase)">curva direita</button>
                            <button class="quick-action danger" type="button" style="grid-column:1 / -1;" onclick="forceStop()">STOP IMEDIATO</button>
                        </div>

                        <div class="rows">
                            <div class="row"><span class="label">Teclas ativas</span><span id="keys-active" class="value info mono">nenhuma</span></div>
                            <div class="row"><span class="label">Loop de envio</span><span id="loop-live" class="value info mono">90 ms</span></div>
                            <div class="row"><span class="label">Fonte atual</span><span id="source-live" class="value info mono">--</span></div>
                            <div class="row"><span class="label">Comando enviado</span><span id="cmd-live" class="value info mono">speed=0 steering=0</span></div>
                        </div>
                    </article>

                    <div class="control-layout">
                        <article class="control-card">
                            <div class="card-top">
                                <div>
                                    <div class="section-tag">Vetor</div>
                                    <h3>Intencao de movimento</h3>
                                    <p>Representacao visual do comando atual enviado ao AGV.</p>
                                </div>
                                <span id="command-state" class="command-badge">speed 0 | steer 0</span>
                            </div>

                            <div class="vector">
                                <div class="vector-gauge">
                                    <div id="vector-arrow" class="vector-arrow"></div>
                                </div>
                                <div class="vector-readout">
                                    <div class="mini-stat">
                                        <div class="mini-label">Speed</div>
                                        <strong id="vector-speed">0</strong>
                                    </div>
                                    <div class="mini-stat">
                                        <div class="mini-label">Steering</div>
                                        <strong id="vector-steering">0</strong>
                                    </div>
                                    <div class="mini-stat">
                                        <div class="mini-label">Modo</div>
                                        <strong id="mode-live">manual</strong>
                                    </div>
                                </div>
                            </div>
                        </article>

                        <article class="control-card">
                            <div class="card-top">
                                <div>
                                    <div class="section-tag">Estado</div>
                                    <h3>Saida operacional</h3>
                                    <p>Resumo do que o AGV esta executando agora.</p>
                                </div>
                                <span id="current-source-pill" class="metric-badge">source --</span>
                            </div>
                            <div class="telemetry-stack">
                                <div class="mini-stat">
                                    <div class="mini-label">Distancia</div>
                                    <strong id="dist">--</strong>
                                </div>
                                <div class="mini-stat">
                                    <div class="mini-label">FPS</div>
                                    <strong id="fps">--</strong>
                                </div>
                                <div class="mini-stat">
                                    <div class="mini-label">Comando atual</div>
                                    <strong id="agv-cmd">--</strong>
                                </div>
                                <div class="mini-stat">
                                    <div class="mini-label">Proxima acao</div>
                                    <strong id="ai-next">--</strong>
                                </div>
                            </div>
                        </article>
                    </div>
                </div>
            </section>

            <section id="tab-intelligence" class="panel tab">
                <div class="page-head">
                    <div>
                        <div class="section-tag">IA e percepcao</div>
                        <h2>Cerebro, radar e aprendizado</h2>
                        <p>Aqui entram os dados reais do modulo neural: amostras no SQLite, treino acumulado, versao do modelo e recomendacao atual do backend.</p>
                    </div>
                    <div class="head-actions">
                        <button class="ghost" type="button" onclick="trainBrain(5)">treinar 5 passos</button>
                        <button class="action" type="button" onclick="applyAiCommand()">executar acao da IA</button>
                    </div>
                </div>

                <div class="intelligence-layout">
                    <article class="brain-card">
                        <div class="card-top">
                            <div>
                                <div class="section-tag">Backend neural</div>
                                <h3>Status do cerebro</h3>
                                <p>Indicadores do treino incremental da rede neural e do banco de experiencias.</p>
                            </div>
                            <span id="brain-online" class="live-pill">brain pending</span>
                        </div>

                        <div class="brain-grid">
                            <div class="brain-stat">
                                <div class="mini-label">Amostras</div>
                                <strong id="brain-samples">--</strong>
                            </div>
                            <div class="brain-stat">
                                <div class="mini-label">Train steps</div>
                                <strong id="brain-train-steps">--</strong>
                            </div>
                            <div class="brain-stat">
                                <div class="mini-label">Loss</div>
                                <strong id="brain-loss">--</strong>
                            </div>
                            <div class="brain-stat">
                                <div class="mini-label">Versao</div>
                                <strong id="brain-version">--</strong>
                            </div>
                        </div>

                        <div class="brain-explain">
                            <div class="mini-label">Recomendacao do backend</div>
                            <strong id="brain-recommendation" style="display:block; margin-top:8px; font-size:28px; font-family:var(--heading);">aguardando</strong>
                            <p id="brain-summary">Sem snapshot suficiente para mostrar recomendacao real da rede neural.</p>
                            <div class="brain-bar"><div id="brain-confidence-bar" class="brain-fill"></div></div>
                            <div class="stream-footer">
                                <span class="bar-caption">confianca da recomendacao</span>
                                <span id="brain-confidence-text" class="mono">--</span>
                            </div>
                        </div>

                        <div class="brain-actions">
                            <button class="train-btn" type="button" onclick="trainBrain(1)">treinar 1</button>
                            <button class="train-btn" type="button" onclick="trainBrain(10)">treinar 10</button>
                            <button class="train-btn" type="button" onclick="refreshAiSnapshot(true)">atualizar IA</button>
                            <button class="train-btn" type="button" onclick="applyAiCommand()">aplicar IA</button>
                        </div>

                        <div class="feedback-row">
                            <button class="feedback-btn" type="button" onclick="sendBrainFeedback(1)">feedback positivo</button>
                            <button class="feedback-btn" type="button" onclick="sendBrainFeedback(-1)">feedback negativo</button>
                        </div>
                    </article>

                    <div class="analysis-grid">
                        <article class="ai-card">
                            <div class="card-top">
                                <div>
                                    <div class="section-tag">Risco espacial</div>
                                    <h3>Radar frontal</h3>
                                    <p>Estimativa visual por faixas para entender qual lado parece mais limpo.</p>
                                </div>
                                <span id="best-side-pill" class="metric-badge">lado --</span>
                            </div>

                            <canvas id="spatial-radar" width="520" height="260"></canvas>

                            <div class="lane-grid">
                                <div class="lane-card">
                                    <small>Faixa esquerda</small>
                                    <strong id="lane-left">--</strong>
                                </div>
                                <div class="lane-card">
                                    <small>Faixa centro</small>
                                    <strong id="lane-center">--</strong>
                                </div>
                                <div class="lane-card">
                                    <small>Faixa direita</small>
                                    <strong id="lane-right">--</strong>
                                </div>
                            </div>
                        </article>

                        <article class="ai-card">
                            <div class="card-top">
                                <div>
                                    <div class="section-tag">Tendencia</div>
                                    <h3>Linha de decisao</h3>
                                    <p>Historico curto das ultimas decisoes para perceber mudancas de risco.</p>
                                </div>
                                <span id="risk-trend" class="metric-badge">--</span>
                            </div>

                            <div class="rows">
                                <div class="row"><span class="label">Espaco frontal</span><span id="space-front" class="value info mono">--</span></div>
                                <div class="row"><span class="label">Tendencia</span><span id="trend-direction" class="value info">--</span></div>
                                <div class="row"><span class="label">Ultima heuristica</span><span id="decision-headline-mini" class="value info">--</span></div>
                            </div>

                            <div id="decision-timeline" class="timeline"></div>
                        </article>
                    </div>

                    <article class="ai-card">
                        <div class="card-top">
                            <div>
                                <div class="section-tag">Mapa superior</div>
                                <h3>Mini mapa de obstaculos</h3>
                                <p>Visao por cima com linha branca mostrando onde o AGV detecta obstaculos no corredor.</p>
                            </div>
                            <span class="metric-badge">top view</span>
                        </div>
                        <div class="minimap-wrap">
                            <canvas id="obstacle-minimap" width="720" height="260"></canvas>
                        </div>
                        <div class="stream-footer">
                            <span class="bar-caption">branco forte: leitura atual | branco suave: historico recente</span>
                            <span id="minimap-hint" class="mono">aguardando depth</span>
                        </div>
                    </article>

                    <article class="event-card">
                        <div class="card-top">
                            <div>
                                <div class="section-tag">Eventos unificados</div>
                                <h3>Log completo</h3>
                                <p>Mesmo log do painel principal, mantido aqui para o modo de analise.</p>
                            </div>
                            <span id="last-ai-source" class="metric-badge">source --</span>
                        </div>
                        <div id="event-log-2" class="logs" style="min-height:260px;"></div>
                    </article>

                    <article class="brain-card" style="margin-top:14px;">
                        <div class="card-top">
                            <div>
                                <div class="section-tag">Reconhecimento facial</div>
                                <h3>Pessoas cadastradas</h3>
                                <p>Cadastro e reconhecimento facial agora ficam nesta aba para acesso direto no fluxo de IA.</p>
                            </div>
                            <span id="face-known-pill" class="face-chip">SEM LEITURA</span>
                        </div>

                        <div class="face-grid">
                            <div class="mini-stat">
                                <div class="mini-label">Selecionado</div>
                                <strong id="face-selected">--</strong>
                            </div>
                            <div class="mini-stat">
                                <div class="mini-label">Ultimo rosto</div>
                                <strong id="face-last">--</strong>
                            </div>
                            <div class="mini-stat">
                                <div class="mini-label">Score</div>
                                <strong id="face-score">--</strong>
                            </div>
                            <div class="mini-stat">
                                <div class="mini-label">Modo</div>
                                <strong id="face-mode-state">desligado</strong>
                            </div>
                        </div>

                        <div class="face-controls">
                            <select id="face-select"></select>
                            <button type="button" onclick="selectFaceTarget()">selecionar</button>
                            <button id="face-toggle-btn" type="button" onclick="toggleFaceMode()">ligar reconhecimento</button>
                            <button type="button" onclick="refreshFaces(true)">atualizar lista</button>
                            <button type="button" onclick="openFaceModal()">adicionar pessoa</button>
                        </div>
                    </article>

                </div>
            </section>

            <section id="tab-settings" class="panel tab">
                <div class="page-head">
                    <div>
                        <div class="section-tag">Parametros</div>
                        <h2>Ajustes do painel</h2>
                        <p>Esses controles afetam o ritmo do frontend, a experiencia do gamepad e as referencias visuais usadas pela explicacao local.</p>
                    </div>
                </div>

                <div class="settings-grid">
                    <article class="setting-card">
                        <label for="cfg-forward">Velocidade base frente</label>
                        <h3>Forward base</h3>
                        <input id="cfg-forward" type="range" min="20" max="100" value="55">
                        <div id="cfg-forward-val" class="setting-value">55</div>
                    </article>
                    <article class="setting-card">
                        <label for="cfg-reverse">Velocidade base re</label>
                        <h3>Reverse base</h3>
                        <input id="cfg-reverse" type="range" min="20" max="100" value="45">
                        <div id="cfg-reverse-val" class="setting-value">45</div>
                    </article>
                    <article class="setting-card">
                        <label for="cfg-turn">Direcao base</label>
                        <h3>Turn base</h3>
                        <input id="cfg-turn" type="range" min="20" max="100" value="60">
                        <div id="cfg-turn-val" class="setting-value">60</div>
                    </article>
                    <article class="setting-card">
                        <label for="cfg-boost">Boost frente</label>
                        <h3>Forward boost</h3>
                        <input id="cfg-boost" type="range" min="30" max="100" value="70">
                        <div id="cfg-boost-val" class="setting-value">70</div>
                    </article>
                    <article class="setting-card">
                        <label for="cfg-loop">Loop de envio</label>
                        <h3>Keyboard loop</h3>
                        <input id="cfg-loop" type="range" min="50" max="250" value="90">
                        <div id="cfg-loop-val" class="setting-value">90 ms</div>
                    </article>
                    <article class="setting-card">
                        <label for="cfg-safe">Distancia segura local</label>
                        <h3>Safe distance</h3>
                        <input id="cfg-safe" type="range" min="0.4" max="2.0" step="0.1" value="0.9">
                        <div id="cfg-safe-val" class="setting-value">0.9 m</div>
                    </article>
                </div>

                <article class="brain-card" style="margin-top:14px;">
                    <div class="card-top">
                        <div>
                            <div class="section-tag">Reconhecimento facial</div>
                            <h3>Atalho de acesso</h3>
                            <p>Cadastro e leitura facial foram movidos para a aba IA e percepcao.</p>
                        </div>
                    </div>
                    <div class="head-actions" style="margin-top:10px;">
                        <button class="ghost" type="button" onclick="focusTab('intelligence')">abrir reconhecimento</button>
                        <button class="action" type="button" onclick="focusTab('intelligence'); openFaceModal();">cadastrar rosto</button>
                    </div>
                </article>
            </section>
        </main>
    </div>

    <div id="face-modal" class="modal hidden">
        <div class="modal-card">
            <div class="modal-head">
                <div>
                    <div class="section-tag">Novo rosto</div>
                    <h3>Adicionar pessoa</h3>
                </div>
                <button class="modal-close" type="button" onclick="closeFaceModal()" aria-label="fechar cadastro">x</button>
            </div>
            <p class="muted" style="margin-top:8px;">Escolha um nome e envie uma foto frontal com boa luz.</p>
            <form id="face-form" class="modal-form">
                <input id="face-name" name="name" type="text" maxlength="40" placeholder="Nome da pessoa" required>
                <input id="face-image" name="image" type="file" accept="image/*" required>
                <div class="modal-actions">
                    <button class="ghost" type="button" onclick="closeFaceModal()">cancelar</button>
                    <button class="action" type="submit">salvar rosto</button>
                </div>
            </form>
        </div>
    </div>

    <div id="toast-stack" class="toast-stack" aria-live="polite" aria-atomic="false"></div>

<script>
function clamp(value, minValue, maxValue) {
    return Math.max(minValue, Math.min(maxValue, value));
}

function postJson(url, body) {
    return fetch(url, {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify(body || {})
    });
}

function setText(id, value) {
    const el = document.getElementById(id);
    if (el) el.textContent = value;
}

function setHtml(id, value) {
    const el = document.getElementById(id);
    if (el) el.innerHTML = value;
}

const keyState = {w:false, a:false, s:false, d:false};
let driveTimer = null;
let lastSent = {speed:null, steering:null, mode:null};
let currentMode = "manual";
let prevDistance = null;
let lastAiAction = "--";
let aiRefreshPending = false;
let lastAiFetchAt = 0;
let aiBackend = {status:null, recommendation:null};
let lastFaceTriggerId = 0;
let faceRefreshPending = false;
let runtimeSignature = "";
let lastManualBlockedAt = 0;
let lastFaceNoticeKey = "";

const history = {distance:[], speed:[], steering:[], fpsRgb:[], fpsDepth:[]};
const logs = [];
const decisionTimeline = [];
const minimap = {
    maxMeters:4.0,
    laneAngles:[-30, 0, 30],
    historyFrames:[]
};

const cfg = {
    forwardBase:55,
    reverseBase:45,
    turnBase:60,
    forwardBoost:70,
    loopMs:90,
    safeDistance:0.9,
    criticalDistance:0.45
};

function addLog(message) {
    const entry = {t:new Date().toLocaleTimeString(), msg:message};
    logs.unshift(entry);
    if (logs.length > 180) logs.pop();
    const html = logs.map(function(item) {
        return '<div class="log-item"><span class="log-time">' + item.t + '</span>' + item.msg + '</div>';
    }).join("");
    setHtml("event-log", html);
    setHtml("event-log-2", html);
}

function showToast(title, message, kind) {
    const stack = document.getElementById("toast-stack");
    if (!stack) return;
    const toast = document.createElement("div");
    toast.className = "toast " + (kind || "info");

    const strong = document.createElement("strong");
    strong.className = "toast-title";
    strong.textContent = title;

    const body = document.createElement("span");
    body.className = "toast-message";
    body.textContent = message;

    toast.appendChild(strong);
    toast.appendChild(body);
    stack.prepend(toast);

    while (stack.children.length > 4) {
        stack.removeChild(stack.lastChild);
    }

    window.setTimeout(function() {
        toast.classList.add("fade-out");
        window.setTimeout(function() {
            if (toast.parentNode) toast.parentNode.removeChild(toast);
        }, 240);
    }, 3200);
}

function isTypingTarget(target) {
    if (!target) return false;
    const tag = String(target.tagName || "").toUpperCase();
    if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return true;
    if (target.isContentEditable) return true;
    return false;
}

function clearLogs() {
    logs.length = 0;
    setHtml("event-log", "");
    setHtml("event-log-2", "");
    addLog("Log reiniciado no painel");
}

function focusTab(name) {
    document.querySelectorAll(".tab-btn").forEach(function(btn) {
        btn.classList.toggle("active", btn.dataset.tab === name);
    });
    document.querySelectorAll(".tab").forEach(function(tab) {
        tab.classList.toggle("active", tab.id === "tab-" + name);
    });
}

function bindTabs() {
    document.querySelectorAll(".tab-btn").forEach(function(btn) {
        btn.addEventListener("click", function() {
            focusTab(btn.dataset.tab);
        });
    });
}

function hardRefresh() {
    window.location.reload(true);
}

function updateRuntimeMeta(runtime) {
    if (!runtime) return;
    const revision = runtime.frontend_revision || "--";
    const mode = runtime.runtime_mode || "--";
    const source = runtime.runtime_source || "--";
    const buildTime = runtime.build_local_time || "--";
    setText("side-runtime-mode", mode + " | " + source);
    setText("side-frontend-rev", revision);
    setText("side-build-time", buildTime);
    setText("metric-front-revision", revision);
    setText("metric-front-hint", mode + " | build " + buildTime);
    setText("front-build-chip", "build " + revision + " | " + mode);
    const signature = [revision, mode, source, buildTime].join("|");
    if (signature !== runtimeSignature) {
        runtimeSignature = signature;
        addLog("Runtime do painel: " + mode + " | " + source + " | rev " + revision + " | " + buildTime);
    }
}

function qualityLabel(value) {
    if (value >= 70) return "Livre";
    if (value >= 45) return "Moderado";
    return "Critico";
}

function inferLanes(distance, steering) {
    const base = distance == null ? 0 : clamp((distance / 2.0) * 100, 0, 100);
    const leftDistance = arguments.length > 2 ? arguments[2] : null;
    const centerDistance = arguments.length > 3 ? arguments[3] : null;
    const rightDistance = arguments.length > 4 ? arguments[4] : null;
    if (leftDistance != null && centerDistance != null && rightDistance != null) {
        return {
            left: clamp((leftDistance / 2.2) * 100, 0, 100),
            center: clamp((centerDistance / 2.2) * 100, 0, 100),
            right: clamp((rightDistance / 2.2) * 100, 0, 100)
        };
    }
    const rightBias = steering < 0 ? 12 : (steering > 0 ? -12 : 0);
    return {
        left: clamp(base - 8 - rightBias, 0, 100),
        center: clamp(base - 15, 0, 100),
        right: clamp(base - 8 + rightBias, 0, 100)
    };
}

function addDecisionPoint(ai, distance) {
    const score = distance == null ? 0 : clamp((distance / 2.2) * 100, 0, 100);
    decisionTimeline.unshift({
        t:new Date().toLocaleTimeString(),
        score:score,
        label:ai.action
    });
    if (decisionTimeline.length > 12) decisionTimeline.pop();
}

function renderDecisionTimeline() {
    const html = decisionTimeline.map(function(item) {
        return '<div class="timeline-item">'
            + '<div class="timeline-time">' + item.t + '</div>'
            + '<div class="timeline-track">'
            + '<div class="timeline-bar"><div class="timeline-fill" style="width:' + item.score + '%"></div></div>'
            + '<div class="timeline-label">' + item.label + '</div>'
            + '</div>'
            + '</div>';
    }).join("");
    setHtml("decision-timeline", html);
}

function renderSpatialRadar(distance, steering, ai, status) {
    const canvas = document.getElementById("spatial-radar");
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    const width = canvas.width;
    const height = canvas.height;
    ctx.clearRect(0, 0, width, height);

    const background = ctx.createLinearGradient(0, 0, 0, height);
    background.addColorStop(0, "#081321");
    background.addColorStop(1, "#050c16");
    ctx.fillStyle = background;
    ctx.fillRect(0, 0, width, height);

    ctx.strokeStyle = "rgba(125, 180, 255, 0.22)";
    ctx.lineWidth = 1;
    for (let ring = 1; ring <= 4; ring += 1) {
        ctx.beginPath();
        ctx.moveTo(18, (height / 5) * ring);
        ctx.lineTo(width - 18, (height / 5) * ring);
        ctx.stroke();
    }

    for (let col = 1; col <= 2; col += 1) {
        const x = (width / 3) * col;
        ctx.beginPath();
        ctx.moveTo(x, 18);
        ctx.lineTo(x, height - 18);
        ctx.stroke();
    }

    const lanes = inferLanes(
        distance,
        steering,
        status ? status.left_clearance_m : null,
        status ? status.center_clearance_m : null,
        status ? status.right_clearance_m : null
    );
    const values = [lanes.left, lanes.center, lanes.right];
    const labels = ["lane-left", "lane-center", "lane-right"];
    const laneWidth = width / 3;

    values.forEach(function(value, index) {
        const barWidth = laneWidth - 42;
        const x = index * laneWidth + 21;
        const barHeight = clamp(value, 2, 100) * (height - 58) / 100;
        const y = height - 20 - barHeight;
        const color = value >= 70 ? "#4cf1bc" : (value >= 45 ? "#ffc56f" : "#ff6a7a");
        ctx.fillStyle = color;
        ctx.globalAlpha = 0.78;
        ctx.fillRect(x, y, barWidth, barHeight);
        ctx.globalAlpha = 1;
        ctx.strokeStyle = "rgba(255,255,255,0.16)";
        ctx.strokeRect(x, y, barWidth, barHeight);
        setText(labels[index], value.toFixed(0) + "% | " + qualityLabel(value));
    });

    const best = values[0] >= values[2] ? "Esquerda" : "Direita";
    setText("best-side-pill", "lado " + best.toLowerCase());

    const centerX = width / 2;
    const carY = height - 26;
    ctx.fillStyle = "#95d9ff";
    ctx.fillRect(centerX - 18, carY - 8, 36, 10);
    ctx.fillRect(centerX - 12, carY - 15, 24, 8);

    const steerOffset = clamp(steering, -100, 100) / 100 * (width * 0.22);
    ctx.strokeStyle = "#4dd1ff";
    ctx.lineWidth = 3;
    ctx.beginPath();
    ctx.moveTo(centerX, carY - 15);
    ctx.lineTo(centerX + steerOffset, height * 0.42);
    ctx.stroke();

    ctx.fillStyle = "rgba(77, 209, 255, 0.18)";
    ctx.beginPath();
    ctx.moveTo(centerX - 20, carY - 15);
    ctx.lineTo(centerX + 20, carY - 15);
    ctx.lineTo(centerX + steerOffset + 34, height * 0.42);
    ctx.lineTo(centerX + steerOffset - 34, height * 0.42);
    ctx.closePath();
    ctx.fill();

    if (ai && ai.risk) {
        ctx.fillStyle = "#bedcff";
        ctx.font = "12px Ubuntu";
        ctx.fillText("Risco: " + ai.risk, 14, 18);
    }
}

function bindConfig() {
    function bind(id, key, suffix) {
        const el = document.getElementById(id);
        const valueEl = document.getElementById(id + "-val");
        if (!el || !valueEl) return;
        const update = function() {
            const raw = parseFloat(el.value);
            cfg[key] = raw;
            valueEl.textContent = raw + (suffix || "");
            if (key === "loopMs") {
                setText("loop-live", raw + " ms");
                setText("side-loop", raw + " ms");
                if (driveTimer !== null) {
                    clearInterval(driveTimer);
                    driveTimer = setInterval(driveTick, cfg.loopMs);
                }
            }
            if (key === "safeDistance") {
                cfg.criticalDistance = Math.max(0.2, raw * 0.5);
            }
        };
        el.addEventListener("input", update);
        update();
    }

    bind("cfg-forward", "forwardBase", "");
    bind("cfg-reverse", "reverseBase", "");
    bind("cfg-turn", "turnBase", "");
    bind("cfg-boost", "forwardBoost", "");
    bind("cfg-loop", "loopMs", " ms");
    bind("cfg-safe", "safeDistance", " m");
}

function pushHistory(array, value, maxSize) {
    array.push(value);
    if (array.length > maxSize) array.shift();
}

function drawLineChart(canvasId, series, minValue, maxValue, colors) {
    const canvas = document.getElementById(canvasId);
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    const width = canvas.width;
    const height = canvas.height;
    ctx.clearRect(0, 0, width, height);

    const bg = ctx.createLinearGradient(0, 0, 0, height);
    bg.addColorStop(0, "#091221");
    bg.addColorStop(1, "#050c15");
    ctx.fillStyle = bg;
    ctx.fillRect(0, 0, width, height);

    ctx.strokeStyle = "rgba(103, 150, 215, 0.2)";
    ctx.lineWidth = 1;
    for (let index = 1; index <= 4; index += 1) {
        const y = (height / 5) * index;
        ctx.beginPath();
        ctx.moveTo(0, y);
        ctx.lineTo(width, y);
        ctx.stroke();
    }

    series.forEach(function(values, index) {
        if (values.length < 2) return;
        ctx.strokeStyle = colors[index] || "#4bd0ff";
        ctx.lineWidth = 2.4;
        ctx.beginPath();
        values.forEach(function(value, pos) {
            const x = (pos / (values.length - 1)) * (width - 2) + 1;
            const norm = (value - minValue) / (maxValue - minValue || 1);
            const y = height - (clamp(norm, 0, 1) * (height - 12) + 6);
            if (pos === 0) ctx.moveTo(x, y);
            else ctx.lineTo(x, y);
        });
        ctx.stroke();
    });
}

function renderCharts() {
    drawLineChart("chart-distance", [history.distance], 0, 4, ["#4cf1bc"]);
    drawLineChart("chart-control", [history.speed, history.steering], -100, 100, ["#4bcfff", "#ffc56f"]);
}

function inferAiDecision(distance) {
    if (distance == null) {
        return {
            action:"Sem leitura",
            confidence:"baixa",
            why:"Depth ainda sem frame",
            detail:"Sem profundidade suficiente para decidir. O painel fica em observacao e evita narrativas falsas.",
            next:"Aguardar depth",
            risk:"Sem leitura"
        };
    }

    const trend = prevDistance == null ? 0 : (distance - prevDistance);
    const approachingFast = trend < -0.06;

    let action = "Seguir em frente";
    let confidence = "alta";
    let why = "Area frontal livre";
    let detail = "Ha espaco suficiente na frente. Estrategia visual recomendada: manter avanco e acompanhar variacao da distancia.";
    let next = "Frente estavel";
    let risk = "Baixo";

    if (distance < cfg.criticalDistance) {
        action = "Recuar e abrir lado";
        confidence = "alta";
        why = "Obstaculo critico no centro";
        detail = "A distancia central entrou em zona critica. O frontend recomenda recuo curto e abertura para buscar faixa mais limpa.";
        next = "Re + correção";
        risk = "Critico";
    } else if (distance < cfg.safeDistance) {
        action = "Reduzir e corrigir";
        confidence = approachingFast ? "alta" : "media";
        why = "Espaco reduzido a frente";
        detail = "Ha espaco, mas a margem esta pequena. Vale reduzir velocidade e corrigir o rumo antes de seguir.";
        next = "Reducao controlada";
        risk = "Moderado";
    } else if (approachingFast) {
        action = "Monitorar aproximacao";
        confidence = "media";
        why = "Distancia caiu rapido";
        detail = "Ainda existe espaco, mas a aproximacao piorou. Segue com cautela e prontidao para correção.";
        next = "Atencao elevada";
        risk = "Baixo";
    }

    return {action:action, confidence:confidence, why:why, detail:detail, next:next, risk:risk};
}

function computeDrive() {
    const forward = (keyState.w ? 1 : 0) - (keyState.s ? 1 : 0);
    const turn = (keyState.d ? 1 : 0) - (keyState.a ? 1 : 0);
    let speed = 0;
    let steering = 0;

    if (forward !== 0) {
        speed = forward * (forward > 0 ? cfg.forwardBase : cfg.reverseBase);
        steering = turn * cfg.turnBase;
    } else if (turn !== 0) {
        steering = turn * cfg.turnBase;
    }

    return {speed:speed, steering:steering};
}

function updateVector(speed, steering, mode) {
    const arrow = document.getElementById("vector-arrow");
    if (arrow) {
        const angle = clamp(steering, -100, 100) * 0.35;
        const scale = 0.68 + Math.abs(speed) / 170;
        arrow.style.transform = "translateX(-50%) rotate(" + angle + "deg) scaleY(" + scale + ")";
    }

    setText("vector-speed", String(speed));
    setText("vector-steering", String(steering));
    setText("mode-live", mode || "manual");
    setText("command-state", "speed " + speed + " | steer " + steering);
}

function sendCmd(speed, steering, force) {
    if (!force && lastSent.speed === speed && lastSent.steering === steering && lastSent.mode === currentMode) {
        return;
    }

    lastSent.speed = speed;
    lastSent.steering = steering;
    lastSent.mode = currentMode;

    postJson("/api/control", {
        mode:currentMode,
        speed:speed,
        steering:steering,
        source:"site-gamepad"
    });

    setText("cmd-live", "speed=" + speed + " steering=" + steering);
    updateVector(speed, steering, currentMode);
}

function sendManualCmd(speed, steering) {
    if (currentMode === "auto") {
        const now = Date.now();
        if (now - lastManualBlockedAt > 1200) {
            addLog("Modo auto ativo: comando manual bloqueado");
            lastManualBlockedAt = now;
        }
        return;
    }
    sendCmd(speed, steering, true);
}

function forceStop() {
    Object.keys(keyState).forEach(function(key) {
        keyState[key] = false;
    });
    refreshKeyLights();
    sendCmd(0, 0, true);
    fetch("/api/stop", {method:"POST"});
    addLog("STOP imediato acionado");
}

function syncModeButtons() {
    const isAuto = currentMode === "auto";
    const manual = document.getElementById("btn-manual");
    const auto = document.getElementById("btn-auto");
    if (manual) manual.classList.toggle("active", !isAuto);
    if (auto) auto.classList.toggle("active", isAuto);
    setText("mode-badge", currentMode);
    setText("side-mode", currentMode);
    const dot = document.getElementById("side-mode-dot");
    if (dot) {
        dot.className = "status-dot " + (isAuto ? "warn" : "ok");
    }
}

function setMode(mode, emit) {
    const shouldEmit = emit !== false;
    currentMode = mode === "auto" ? "auto" : "manual";

    if (currentMode === "auto") {
        Object.keys(keyState).forEach(function(key) {
            keyState[key] = false;
        });
        refreshKeyLights();
        if (driveTimer !== null) {
            clearInterval(driveTimer);
            driveTimer = null;
        }
        sendCmd(0, 0, true);
    }

    syncModeButtons();
    if (shouldEmit) {
        postJson("/api/control", {mode:currentMode, source:"site-mode"});
        addLog("Modo alterado para " + currentMode);
    }
}

function driveTick() {
    const command = computeDrive();
    sendCmd(command.speed, command.steering, false);
}

function startDriveLoop() {
    if (driveTimer !== null) return;
    driveTimer = setInterval(driveTick, cfg.loopMs);
    driveTick();
}

function stopDriveLoopIfIdle() {
    if (Object.values(keyState).some(Boolean)) return;
    if (driveTimer !== null) {
        clearInterval(driveTimer);
        driveTimer = null;
    }
    sendCmd(0, 0, true);
}

function refreshKeyLights() {
    ["w", "a", "s", "d"].forEach(function(key) {
        const el = document.getElementById("k-" + key);
        if (el) el.classList.toggle("active", keyState[key]);
    });

    const active = Object.entries(keyState).filter(function(entry) {
        return entry[1];
    }).map(function(entry) {
        return entry[0].toUpperCase();
    });

    const text = active.length ? active.join(" ") : "nenhuma";
    setText("keys-active", text);
}

function setKeyState(key, pressed) {
    if (!(key in keyState)) return;
    if (currentMode === "auto") {
        if (pressed) {
            const now = Date.now();
            if (now - lastManualBlockedAt > 1200) {
                addLog("W A S D bloqueado no modo auto");
                lastManualBlockedAt = now;
            }
        }
        return;
    }
    if (keyState[key] === pressed) return;
    keyState[key] = pressed;
    refreshKeyLights();
    if (pressed) {
        addLog("Tecla " + key.toUpperCase() + " pressionada");
        startDriveLoop();
    } else {
        addLog("Tecla " + key.toUpperCase() + " solta");
        stopDriveLoopIfIdle();
    }
}

function mapKey(evtKey) {
    const key = (evtKey || "").toLowerCase();
    if (key === "w" || key === "arrowup") return "w";
    if (key === "a" || key === "arrowleft") return "a";
    if (key === "s" || key === "arrowdown") return "s";
    if (key === "d" || key === "arrowright") return "d";
    return null;
}

window.addEventListener("keydown", function(event) {
    if (isTypingTarget(event.target)) return;
    const key = mapKey(event.key);
    if (!key) return;
    event.preventDefault();
    setKeyState(key, true);
});

window.addEventListener("keyup", function(event) {
    if (isTypingTarget(event.target)) return;
    const key = mapKey(event.key);
    if (!key) return;
    event.preventDefault();
    setKeyState(key, false);
});

window.addEventListener("blur", function() {
    forceStop();
});

function bindPadKeys() {
    document.querySelectorAll(".pad-key[data-key]").forEach(function(el) {
        const key = el.dataset.key;
        const down = function(ev) {
            ev.preventDefault();
            setKeyState(key, true);
        };
        const up = function(ev) {
            ev.preventDefault();
            setKeyState(key, false);
        };
        el.addEventListener("pointerdown", down);
        el.addEventListener("pointerup", up);
        el.addEventListener("pointerleave", up);
        el.addEventListener("pointercancel", up);
    });
}

function updateRiskBadge(risk) {
    const badge = document.getElementById("risk-badge");
    if (!badge) return;
    let cls = "low";
    if ((risk || "").toLowerCase() === "moderado") cls = "medium";
    if ((risk || "").toLowerCase() === "critico") cls = "high";
    badge.className = "risk-badge " + cls;
    badge.textContent = "RISCO " + (risk || "--").toUpperCase();
}

function updateHeuristicPanels(status) {
    const ai = inferAiDecision(status.distance_m);
    const distanceValue = status.distance_m == null ? "--" : status.distance_m.toFixed(2) + " m";
    setText("ai-action", ai.action);
    setText("ai-confidence", ai.confidence);
    setText("ai-why", ai.why);
    setText("ai-next", ai.next);
    setText("space-front", distanceValue);
    setText("decision-headline", ai.action);
    setText("decision-headline-mini", ai.action);
    setText("metric-ai-action", ai.action === "Sem leitura" ? "--" : ai.action);
    setText("metric-ai-hint", ai.why);
    setText("trend-direction", prevDistance == null || status.distance_m == null ? "sem referencia" : (status.distance_m >= prevDistance ? "abrindo" : "fechando"));
    setText("risk-trend", ai.risk || "--");
    setHtml("decision-box", ai.detail);
    updateRiskBadge(ai.risk);
    renderSpatialRadar(status.distance_m, status.agv.steering || 0, ai, status);
    updateMinimap(status);
    addDecisionPoint(ai, status.distance_m);
    renderDecisionTimeline();
    if (lastAiAction !== ai.action) {
        addLog("Leitura visual sugere: " + ai.action + " (" + ai.confidence + ")");
        lastAiAction = ai.action;
    }
    prevDistance = status.distance_m;
}

function updateMetricSummary(status) {
    const distance = status.distance_m;
    const distanceText = distance == null ? "--" : distance.toFixed(2) + " m";
    const distanceHint = distance == null ? "aguardando profundidade" : (distance < cfg.criticalDistance ? "zona critica" : (distance < cfg.safeDistance ? "margem curta" : "folga segura"));
    setText("metric-distance", distanceText);
    setText("metric-distance-hint", distanceHint);
    setText("metric-command", String(status.agv.speed || 0) + " / " + String(status.agv.steering || 0));
    setText("metric-command-hint", "speed / steering");
    setText("dist", distanceText);
    setText("fps", String(status.fps_rgb || 0) + " / " + String(status.fps_depth || 0));
    setText("agv-cmd", "speed=" + String(status.agv.speed || 0) + " steering=" + String(status.agv.steering || 0));
    setText("rgb-fps-foot", "fps " + String(status.fps_rgb || 0));
    setText("depth-fps-foot", "fps " + String(status.fps_depth || 0));
    setText("safe-window", cfg.safeDistance.toFixed(1) + " m alvo");
    setText("side-distance", distanceText);
    const sideDot = document.getElementById("side-distance-dot");
    if (sideDot) {
        sideDot.className = "status-dot " + (distance == null ? "warn" : (distance < cfg.criticalDistance ? "err" : (distance < cfg.safeDistance ? "warn" : "ok")));
    }

    const barWidth = distance == null ? 0 : clamp((1 - distance / 4) * 100, 0, 100);
    const progress = document.getElementById("dist-bar");
    if (progress) progress.style.width = barWidth + "%";

    updateVector(status.agv.speed || 0, status.agv.steering || 0, status.agv.mode || currentMode);
    setText("current-source-pill", "source " + (status.agv.last_source || "--"));
}

function updateTelemetry(status) {
    const kok = document.getElementById("kok");
    if (kok) {
        kok.textContent = status.kinect_ok ? "ONLINE" : "OFFLINE";
        kok.className = "value " + (status.kinect_ok ? "ok" : "err");
    }

    const mok = document.getElementById("mok");
    if (mok) {
        mok.textContent = status.motor_ok ? "OK" : "FALHA";
        mok.className = "value " + (status.motor_ok ? "ok" : "err");
    }

    setText("tilt", status.tilt_deg == null ? "--" : status.tilt_deg.toFixed(1) + " deg");
    setText("tiltcmd", (status.tilt_cmd || 0).toFixed(1) + " deg");
    setText("accel", (status.accel || [0, 0, 0]).map(function(value) { return Number(value).toFixed(3); }).join(" / "));
    setText("side-source", status.agv.last_source || "--");
    setText("source-live", status.agv.last_source || "--");
    setText("err-msg", status.error || "nenhum");
    setText("side-link-status", status.kinect_ok ? "online" : "instavel");

    const linkDot = document.getElementById("side-link-dot");
    if (linkDot) {
        linkDot.className = "status-dot " + (status.kinect_ok ? "ok" : "warn");
    }
}

function updateModeFromStatus(status) {
    setMode(status.agv.mode || "manual", false);
    setText("mode-live", status.agv.mode || "manual");
}

function updateHistory(status) {
    pushHistory(history.distance, status.distance_m == null ? 0 : status.distance_m, 120);
    pushHistory(history.speed, status.agv.speed || 0, 120);
    pushHistory(history.steering, status.agv.steering || 0, 120);
    pushHistory(history.fpsRgb, status.fps_rgb || 0, 120);
    pushHistory(history.fpsDepth, status.fps_depth || 0, 120);
    renderCharts();
}

function updateBrainStatus(data) {
    aiBackend.status = data || null;
    if (!data) {
        setText("brain-online", "brain offline");
        setText("metric-brain-version", "v--");
        setText("metric-brain-hint", "sem status da IA");
        return;
    }

    setText("brain-online", "brain online");
    setText("brain-samples", String(data.samples));
    setText("brain-train-steps", String(data.train_steps));
    setText("brain-loss", String(data.last_loss));
    setText("brain-version", "v" + String(data.model_version));
    setText("metric-brain-version", "v" + String(data.model_version));
    setText("metric-brain-hint", String(data.samples) + " amostras | " + String(data.train_steps) + " steps");
}

function updateBrainRecommendation(rec) {
    aiBackend.recommendation = rec || null;
    if (!rec) {
        setText("brain-recommendation", "aguardando");
        setText("brain-summary", "Sem snapshot suficiente para mostrar recomendacao real da rede neural.");
        setText("brain-confidence-text", "--");
        return;
    }

    const text = "speed " + String(rec.speed) + " | steer " + String(rec.steering);
    setText("brain-recommendation", text);
    setText("brain-summary", "Recomendacao entregue pelo backend neural. Pode ser aplicada diretamente no AGV pelo painel.");
    const confidence = clamp((Number(rec.confidence) || 0) * 100, 0, 100);
    const bar = document.getElementById("brain-confidence-bar");
    if (bar) bar.style.width = confidence + "%";
    setText("brain-confidence-text", confidence.toFixed(0) + "%");
    setText("last-ai-source", "source model-v" + String(rec.model_version || "--"));
}

function refreshAiSnapshot(force) {
    const now = Date.now();
    if (!force && (aiRefreshPending || now - lastAiFetchAt < 1800)) return;
    aiRefreshPending = true;
    lastAiFetchAt = now;

    Promise.all([
        fetch("/api/ai/status").then(function(response) { return response.json(); }),
        fetch("/api/ai/recommend").then(function(response) { return response.json(); })
    ]).then(function(results) {
        const brainStatus = results[0];
        const recommendation = results[1];
        if (brainStatus && brainStatus.ok) {
            updateBrainStatus(brainStatus.brain);
        }
        if (recommendation && recommendation.ok) {
            updateBrainRecommendation(recommendation.recommendation);
        }
    }).catch(function() {
        updateBrainStatus(null);
        updateBrainRecommendation(null);
    }).finally(function() {
        aiRefreshPending = false;
    });
}

function applyAiCommand() {
    postJson("/api/ai/apply", {}).then(function(response) {
        return response.json();
    }).then(function(payload) {
        if (payload && payload.ok) {
            addLog("IA aplicada: speed=" + payload.agv.speed + " steering=" + payload.agv.steering);
            if (payload.recommendation) updateBrainRecommendation(payload.recommendation);
            setMode("auto", false);
        } else {
            addLog("Falha ao aplicar IA");
        }
    }).catch(function() {
        addLog("Erro de rede ao aplicar IA");
    });
}

function trainBrain(steps) {
    postJson("/api/ai/train", {steps:steps}).then(function(response) {
        return response.json();
    }).then(function(payload) {
        if (!payload || !payload.ok) {
            addLog("Treino da IA nao executado");
            return;
        }
        const lastRun = payload.runs && payload.runs.length ? payload.runs[payload.runs.length - 1] : null;
        if (lastRun && lastRun.ok) {
            addLog("Treino IA: steps=" + lastRun.train_steps + " loss=" + Number(lastRun.loss).toFixed(6));
        } else if (lastRun && lastRun.reason) {
            addLog("Treino IA bloqueado: " + lastRun.reason);
        }
        refreshAiSnapshot(true);
    }).catch(function() {
        addLog("Erro ao treinar IA");
    });
}

function sendBrainFeedback(reward) {
    postJson("/api/ai/feedback", {reward:reward}).then(function(response) {
        return response.json();
    }).then(function(payload) {
        if (payload && payload.ok) {
            addLog("Feedback enviado para IA: " + String(payload.reward));
        } else {
            addLog("Falha ao enviar feedback para IA");
        }
    }).catch(function() {
        addLog("Erro ao enviar feedback para IA");
    });
}

function laneDistanceToPoint(angleDeg, distanceM, width, height) {
    const agvX = width / 2;
    const agvY = height - 22;
    const scale = (height - 34) / minimap.maxMeters;
    const dist = clamp(distanceM, 0.15, minimap.maxMeters);
    const rad = angleDeg * Math.PI / 180;
    const x = agvX + Math.sin(rad) * dist * scale;
    const y = agvY - Math.cos(rad) * dist * scale;
    return {x:x, y:y};
}

function buildMinimapFrame(status) {
    const left = status.left_clearance_m;
    const center = status.center_clearance_m || status.distance_m;
    const right = status.right_clearance_m;
    const distances = [left, center, right];

    const points = [];
    distances.forEach(function(distance, index) {
        if (distance == null) return;
        points.push({
            angle:minimap.laneAngles[index],
            distance:distance
        });
    });
    return points;
}

function renderMinimap() {
    const canvas = document.getElementById("obstacle-minimap");
    if (!canvas) return;

    const ctx = canvas.getContext("2d");
    const width = canvas.width;
    const height = canvas.height;
    ctx.clearRect(0, 0, width, height);

    const bg = ctx.createLinearGradient(0, 0, 0, height);
    bg.addColorStop(0, "#060f1a");
    bg.addColorStop(1, "#040a12");
    ctx.fillStyle = bg;
    ctx.fillRect(0, 0, width, height);

    const agvX = width / 2;
    const agvY = height - 22;
    const scale = (height - 34) / minimap.maxMeters;

    ctx.strokeStyle = "rgba(120, 173, 255, 0.18)";
    ctx.lineWidth = 1;
    for (let m = 1; m <= minimap.maxMeters; m += 1) {
        const y = agvY - m * scale;
        ctx.beginPath();
        ctx.moveTo(18, y);
        ctx.lineTo(width - 18, y);
        ctx.stroke();
    }

    ctx.strokeStyle = "rgba(120, 173, 255, 0.22)";
    [-35, 0, 35].forEach(function(a) {
        const rad = a * Math.PI / 180;
        const x = agvX + Math.sin(rad) * (minimap.maxMeters * scale);
        const y = agvY - Math.cos(rad) * (minimap.maxMeters * scale);
        ctx.beginPath();
        ctx.moveTo(agvX, agvY);
        ctx.lineTo(x, y);
        ctx.stroke();
    });

    minimap.historyFrames.forEach(function(frame, frameIndex) {
        if (!frame || frame.length < 2) return;
        const alpha = (frameIndex + 1) / (minimap.historyFrames.length + 2);
        ctx.strokeStyle = "rgba(255,255,255," + (alpha * 0.35).toFixed(3) + ")";
        ctx.lineWidth = 2;
        ctx.beginPath();
        frame.forEach(function(node, nodeIndex) {
            const p = laneDistanceToPoint(node.angle, node.distance, width, height);
            if (nodeIndex === 0) ctx.moveTo(p.x, p.y);
            else ctx.lineTo(p.x, p.y);
        });
        ctx.stroke();
    });

    const latest = minimap.historyFrames[minimap.historyFrames.length - 1] || [];
    if (latest.length >= 2) {
        ctx.strokeStyle = "rgba(255,255,255,0.96)";
        ctx.lineWidth = 3;
        ctx.beginPath();
        latest.forEach(function(node, nodeIndex) {
            const p = laneDistanceToPoint(node.angle, node.distance, width, height);
            if (nodeIndex === 0) ctx.moveTo(p.x, p.y);
            else ctx.lineTo(p.x, p.y);
        });
        ctx.stroke();
    }

    ctx.fillStyle = "#8fd7ff";
    ctx.fillRect(agvX - 14, agvY - 10, 28, 12);
    ctx.fillRect(agvX - 10, agvY - 17, 20, 8);
}

function updateMinimap(status) {
    const frame = buildMinimapFrame(status);
    if (frame.length > 0) {
        minimap.historyFrames.push(frame);
        if (minimap.historyFrames.length > 40) minimap.historyFrames.shift();
        const avg = frame.reduce(function(acc, item) { return acc + item.distance; }, 0) / frame.length;
        setText("minimap-hint", "obstaculos em " + avg.toFixed(2) + " m (media)");
    } else {
        setText("minimap-hint", "aguardando depth");
    }
    renderMinimap();
}

function playRecognitionTheme() {
    try {
        const Ctx = window.AudioContext || window.webkitAudioContext;
        if (!Ctx) return;
        const ctx = new Ctx();
        const notes = [164.81, 196.00, 220.00, 196.00, 164.81, 146.83, 164.81];
        let t = ctx.currentTime;
        notes.forEach(function(freq) {
            const osc = ctx.createOscillator();
            const gain = ctx.createGain();
            osc.type = "sawtooth";
            osc.frequency.value = freq;
            gain.gain.setValueAtTime(0.0001, t);
            gain.gain.exponentialRampToValueAtTime(0.09, t + 0.02);
            gain.gain.exponentialRampToValueAtTime(0.0001, t + 0.22);
            osc.connect(gain);
            gain.connect(ctx.destination);
            osc.start(t);
            osc.stop(t + 0.24);
            t += 0.18;
        });
    } catch (err) {
        addLog("Audio do reconhecimento indisponivel no navegador");
    }
}

function openFaceModal() {
    const modal = document.getElementById("face-modal");
    if (modal) modal.classList.remove("hidden");
}

function closeFaceModal() {
    const modal = document.getElementById("face-modal");
    if (modal) modal.classList.add("hidden");
}

function bindFaceModal() {
    const modal = document.getElementById("face-modal");
    if (!modal) return;

    modal.addEventListener("click", function(event) {
        if (event.target === modal) closeFaceModal();
    });

    document.addEventListener("keydown", function(event) {
        if (event.key !== "Escape") return;
        if (modal.classList.contains("hidden")) return;
        event.preventDefault();
        closeFaceModal();
    });
}

function fillFaceSelect(face) {
    const select = document.getElementById("face-select");
    if (!select) return;
    const selected = face && face.selected ? face.selected : "";
    const people = face && face.people ? face.people : [];
    let html = '<option value="">qualquer pessoa</option>';
    people.forEach(function(person) {
        const name = String(person.name || "");
        const samples = Number(person.samples || 0);
        const isSel = name === selected ? ' selected' : '';
        html += '<option value="' + name + '"' + isSel + '>' + name + ' (' + samples + ')</option>';
    });
    select.innerHTML = html;
}

function updateFacePanels(face) {
    if (!face) return;
    fillFaceSelect(face);
    setText("face-selected", face.selected || "qualquer pessoa");
    setText("face-mode-state", face.enabled ? "ligado" : "desligado");
    const toggleBtn = document.getElementById("face-toggle-btn");
    if (toggleBtn) toggleBtn.textContent = face.enabled ? "desligar reconhecimento" : "ligar reconhecimento";

    const last = face.last || {};
    const label = last.label || "--";
    setText("face-last", label);
    setText("face-score", last.distance == null ? "--" : Number(last.distance).toFixed(3));

    const chip = document.getElementById("face-known-pill");
    if (chip) {
        chip.className = "face-chip " + (last.known ? "known" : "unknown");
        chip.textContent = last.known ? ("RECONHECIDO: " + label.toUpperCase()) : (label === "Sem rosto" ? "SEM ROSTO" : "DESCONHECIDO");
    }

    const triggerId = Number(last.trigger_id || 0);
    const noticeKey = [triggerId, label, last.known ? "1" : "0"].join("|");
    if (triggerId > 0 && triggerId > lastFaceTriggerId && label !== "Sem rosto") {
        lastFaceTriggerId = triggerId;
        if (last.known) {
            addLog("Rosto conhecido detectado: " + label);
            showToast("Rosto reconhecido", "Pessoa identificada: " + label, "known");
            playRecognitionTheme();
        } else {
            addLog("Rosto estranho detectado");
            showToast("Rosto estranho", "Pessoa fora do cadastro atual: " + label, "unknown");
        }
        lastFaceNoticeKey = noticeKey;
    } else if (noticeKey !== lastFaceNoticeKey && label === "Sem rosto") {
        lastFaceNoticeKey = noticeKey;
    }
}

function refreshFaces(force) {
    if (!force && faceRefreshPending) return;
    faceRefreshPending = true;
    fetch("/api/faces").then(function(response) {
        return response.json();
    }).then(function(payload) {
        if (payload && payload.ok) {
            updateFacePanels(payload.face);
        }
    }).catch(function() {
        addLog("Falha ao carregar lista de rostos");
    }).finally(function() {
        faceRefreshPending = false;
    });
}

function selectFaceTarget() {
    const select = document.getElementById("face-select");
    if (!select) return;
    postJson("/api/faces/select", {name:select.value || ""}).then(function(response) {
        return response.json();
    }).then(function(payload) {
        if (payload && payload.ok) {
            updateFacePanels(payload.face);
            addLog("Pessoa alvo atualizada para: " + (payload.face.selected || "qualquer pessoa"));
        }
    }).catch(function() {
        addLog("Falha ao selecionar pessoa");
    });
}

function toggleFaceMode() {
    const modeText = document.getElementById("face-mode-state");
    const enabled = modeText && modeText.textContent === "ligado";
    postJson("/api/faces/mode", {enabled:!enabled}).then(function(response) {
        return response.json();
    }).then(function(payload) {
        if (payload && payload.ok) {
            updateFacePanels(payload.face);
            addLog("Reconhecimento facial " + (payload.enabled ? "ligado" : "desligado"));
        }
    }).catch(function() {
        addLog("Falha ao alterar modo de reconhecimento");
    });
}

function bindFaceForm() {
    const form = document.getElementById("face-form");
    if (!form) return;
    form.addEventListener("submit", function(event) {
        event.preventDefault();
        const nameInput = document.getElementById("face-name");
        const fileInput = document.getElementById("face-image");
        if (!nameInput || !fileInput || !fileInput.files || !fileInput.files[0]) {
            addLog("Informe nome e imagem para cadastrar");
            return;
        }
        const data = new FormData();
        data.append("name", nameInput.value || "");
        data.append("image", fileInput.files[0]);

        fetch("/api/faces/add", {method:"POST", body:data}).then(function(response) {
            return response.json();
        }).then(function(payload) {
            if (!payload || !payload.ok) {
                const errorMap = {
                    name_required:"Informe um nome para a pessoa.",
                    image_required:"Selecione uma imagem para cadastrar.",
                    invalid_image:"A imagem enviada nao pode ser lida.",
                    face_not_detected:"Nao achei um rosto claro na imagem enviada."
                };
                const errorCode = payload && payload.error ? payload.error : "erro";
                const errorText = errorMap[errorCode] || errorCode;
                addLog("Falha no cadastro do rosto: " + errorText);
                showToast("Cadastro facial falhou", errorText, "unknown");
                return;
            }
            addLog("Rosto cadastrado: " + payload.name + " (" + payload.samples + " amostra(s))");
            showToast("Rosto salvo", payload.name + " foi cadastrado e o reconhecimento foi ligado.", "known");
            updateFacePanels(payload.face);
            nameInput.value = "";
            fileInput.value = "";
            closeFaceModal();
        }).catch(function() {
            addLog("Erro de rede no cadastro do rosto");
        });
    });
}

function poll() {
    fetch("/api/status").then(function(response) {
        return response.json();
    }).then(function(status) {
        updateRuntimeMeta(status.runtime || null);
        updateModeFromStatus(status);
        updateMetricSummary(status);
        updateTelemetry(status);
        updateHistory(status);
        updateHeuristicPanels(status);
        updateFacePanels(status.face || null);
        if (Date.now() - lastAiFetchAt > 2200) refreshAiSnapshot(false);
    }).catch(function() {
        setText("side-link-status", "offline");
        setText("side-runtime-mode", "offline");
        const linkDot = document.getElementById("side-link-dot");
        if (linkDot) linkDot.className = "status-dot err";
    }).finally(function() {
        setTimeout(poll, 450);
    });
}

bindTabs();
bindConfig();
bindPadKeys();
bindFaceModal();
bindFaceForm();
refreshKeyLights();
syncModeButtons();
setText("side-loop", cfg.loopMs + " ms");
addLog("Painel neural iniciado");
refreshFaces(true);
refreshAiSnapshot(true);
poll();
</script>
</body>
</html>
"""

# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    HOST = os.environ.get("AGV_HOST", "0.0.0.0")
    PORT = int(os.environ.get("AGV_PORT", "5000"))
    AI_DB = os.environ.get("AGV_AI_DB", os.path.join("logs", "ai_brain.db"))

    _load_face_db()

    if not _FACE_RECOGNITION_AVAILABLE:
        log.warning("face_recognition indisponivel: modulo nao instalado (funcao de faces desativada)")

    if _arduino_state["protocol"] not in ("wasd", "csv", "dual"):
        _arduino_state["protocol"] = "wasd"

    log.info("=" * 55)
    log.info("  AGV — KINECT v1 COMPLETO")
    log.info("  RGB + Depth + Acell + Motor simultâneos")
    log.info("=" * 55)

    # Verifica freenect antes de fazer qualquer coisa
    try:
        import freenect
        n = freenect.num_devices(freenect.init())
        log.info("freenect OK — %d dispositivo(s) detectado(s)", n)
        if n == 0:
            log.error("Nenhum Kinect encontrado! Verifique o cabo USB.")
            sys.exit(1)
    except Exception as exc:
        log.error("freenect não disponível: %s", exc)
        sys.exit(1)

    _connect_arduino()

    # Inicia worker Kinect
    kinect_thread = _start_kinect()

    try:
        _brain = AGVBrain(db_path=AI_DB)
        log.info("AGVBrain inicializado: %s", AI_DB)
    except Exception as exc:
        _brain = None
        log.warning("AGVBrain indisponivel: %s", exc)

    brain_thread = threading.Thread(target=_brain_worker, daemon=True)
    brain_thread.start()

    # Aguarda primeiro frame (máximo 20s)
    log.info("Aguardando primeiro frame do Kinect...")
    for i in range(20):
        time.sleep(1)
        with _lock:
            ok = _state["kinect_ok"]
        if ok:
            log.info("✅ Kinect online — primeiro frame recebido!")
            break
        log.info("  ...%d/20s", i + 1)
    else:
        log.warning("⚠ Nenhum frame após 20s, continuando assim mesmo...")

    log.info("")
    log.info("Painel local:  http://127.0.0.1:%d", PORT)
    log.info("Painel rede:   http://0.0.0.0:%d  (use IP deste PC)", PORT)
    log.info("Video RGB:     /video")
    log.info("Depth map:     /depth_map")
    log.info("Status JSON:   /status")
    log.info("AGV API:       /api/status  /api/control  /api/stop")
    log.info("Arduino:       porta=%s protocolo=%s conectado=%s",
             _arduino_state.get("port") or "--",
             _arduino_state.get("protocol"),
             _arduino_state.get("connected"))
    log.info("AI API:        /api/ai/status  /api/ai/recommend  /api/ai/apply  /api/ai/feedback  /api/ai/train")
    log.info("Face API:      /api/faces  /api/faces/add  /api/faces/select  /api/faces/mode  /api/faces/status")
    log.info("")

    try:
        app.run(host=HOST, port=PORT,
                debug=False, threaded=True, use_reloader=False)
    except KeyboardInterrupt:
        pass
    finally:
        _brain_running = False
        _running = False
        kinect_thread.join(timeout=3)
        brain_thread.join(timeout=2)
        with _arduino_lock:
            ser = _arduino_serial
        if ser is not None:
            try:
                ser.close()
            except Exception:
                pass
        if _brain is not None:
            try:
                _brain.close()
            except Exception:
                pass
        log.info("Sistema encerrado.")
