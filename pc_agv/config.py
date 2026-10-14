"""Configuracao do agv: logging e variaveis de ambiente AGV_*."""

import logging
import os
import sys


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
