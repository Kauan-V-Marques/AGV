"""Estado compartilhado entre as threads do agv.

Cada dicionario so deve ser lido/alterado com o lock correspondente.
"""

import os
import threading
import time

from pc_agv.config import (
    ZED_DEVICE,
)


stop_event = threading.Event()

state_lock = threading.Lock()

state = {
    "rgb": None,
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

control_lock = threading.Lock()

control_state = {
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

autopilot_lock = threading.Lock()

autopilot_state = {
    "enabled": True,
    "running": False,
    "target_speed": 0,
    "target_steering": 0,
    "last_reason": "idle",
    "last_tick": 0.0,
}

settings_lock = threading.Lock()

settings = {
    "speed_limit_pct": int(os.environ.get("AGV_SPEED_LIMIT_PCT", "100")),
}
