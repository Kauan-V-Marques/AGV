"""Telemetria: junta o estado de todos os modulos para /api/status."""

from pc_agv.config import (
    DEFAULT_HOST,
    DEFAULT_PORT,
    PROJECT_ROOT,
    ZED_DEVICE,
)
from pc_agv import estado
from pc_agv import util
from pc_agv.hardware import arduino as arduino_link
from pc_agv.hardware import kinect
from pc_agv.visao import rostos


def status_snapshot() -> dict:
    with estado.state_lock:
        state = {
            "distance_m": estado.state["distance_m"],
            "left_clearance_m": estado.state["left_clearance_m"],
            "center_clearance_m": estado.state["center_clearance_m"],
            "right_clearance_m": estado.state["right_clearance_m"],
            "tilt_deg": estado.state["tilt_deg"],
            "tilt_target_deg": estado.state["tilt_target_deg"],
            "accel": list(estado.state["accel"]),
            "fps_rgb": estado.state["fps_rgb"],
            "fps_depth": estado.state["fps_depth"],
            "kinect_ok": estado.state["kinect_ok"],
            "kinect_error": estado.state["kinect_error"],
            "last_frame_at": estado.state["last_frame_at"],
        }
    with estado.control_lock:
        control = {
            "mode": estado.control_state["mode"],
            "manual_speed": estado.control_state["manual_speed"],
            "manual_steering": estado.control_state["manual_steering"],
            "active_speed": estado.control_state["active_speed"],
            "active_steering": estado.control_state["active_steering"],
            "updated_at": estado.control_state["updated_at"],
            "last_source": estado.control_state["last_source"],
            "arduino_sent": estado.control_state["arduino_sent"],
            "arduino_error": estado.control_state["arduino_error"],
        }
    with estado.autopilot_lock:
        autopilot = dict(estado.autopilot_state)
    arduino = arduino_link.status_snapshot()

    last_frame_at = state["last_frame_at"]
    state["frame_age_s"] = None if last_frame_at <= 0 else round(util.now() - last_frame_at, 2)
    zed_last_frame_at = float(estado.state["zed_last_frame_at"])
    zed_frame_age = None if zed_last_frame_at <= 0 else round(util.now() - zed_last_frame_at, 2)

    return {
        "ok": True,
        "state": state,
        "agv": control,
        "autopilot": autopilot,
        "arduino": arduino,
        "kinect": {
            "stabilization_enabled": kinect.get_kinect_stabilization(),
        },
        "zed": {
            "online": bool(estado.state["zed_ok"]),
            "error": estado.state["zed_error"],
            "fps": float(estado.state["fps_zed"]),
            "frame_age_s": zed_frame_age,
            "device": estado.state.get("zed_device") or ZED_DEVICE,
        },
        "face": rostos.face_status_snapshot(),
        "settings": dict(estado.settings),
        "runtime": {
            "host": DEFAULT_HOST,
            "port": DEFAULT_PORT,
            "source": "iniciar_sistema.py",
            "project_root": PROJECT_ROOT,
        },
    }
