"""Aplicacao Flask: painel, streams MJPEG e API REST do agv."""

import os
import socket

from flask import Flask, Response, jsonify, request, send_from_directory

from pc_agv.config import (
    DEFAULT_PORT,
    DEPTH_FPS,
    VIDEO_FPS,
    ZED_FPS,
)
from pc_agv import estado
from pc_agv import telemetria
from pc_agv import util
from pc_agv.hardware import arduino
from pc_agv.hardware import kinect
from pc_agv.visao import rostos
from pc_agv.web import mjpeg


STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

app = Flask(__name__, static_folder=STATIC_DIR, static_url_path="/static")
# Sempre revalida painel.css/painel.js para nao servir versao antiga apos atualizar o agv.
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0



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



@app.route("/")
def route_index():
    response = send_from_directory(STATIC_DIR, "painel.html")
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    return response


@app.route("/video")
def route_video():
    return Response(mjpeg.mjpeg_generator("rgb", VIDEO_FPS), mimetype="multipart/x-mixed-replace; boundary=frame")


@app.route("/depth_map")
def route_depth_map():
    return Response(mjpeg.mjpeg_generator("depth", DEPTH_FPS), mimetype="multipart/x-mixed-replace; boundary=frame")


@app.route("/zed")
def route_zed():
    return Response(mjpeg.mjpeg_generator("zed", ZED_FPS), mimetype="multipart/x-mixed-replace; boundary=frame")


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
    resp = jsonify(telemetria.status_snapshot())
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
    kinect.request_reconnect()
    return jsonify({"ok": True, "requested": True})


@app.route("/api/kinect/tilt", methods=["POST"])
def route_kinect_tilt():
    payload = request.get_json(silent=True) or {}
    angle = float(payload.get("angle", 0.0))
    ok, message, applied = kinect.command_kinect_tilt(angle)
    return jsonify({"ok": ok, "message": message, "tilt_target_deg": applied})


@app.route("/api/kinect/stabilization", methods=["GET", "POST"])
def route_kinect_stabilization():
    if request.method == "POST":
        payload = request.get_json(silent=True) or {}
        enabled = bool(payload.get("enabled", True))
        applied = kinect.set_kinect_stabilization(enabled)
        return jsonify({"ok": True, "enabled": applied})

    return jsonify({"ok": True, "enabled": kinect.get_kinect_stabilization()})


@app.route("/api/face/status")
def route_face_status():
    return jsonify({"ok": True, "face": rostos.face_status_snapshot()})


@app.route("/api/face/register_face", methods=["POST"])
def route_face_register_face():
    payload, status_code = rostos.register_face(request.form.get("name", ""), request.files.get("image"))
    return jsonify(payload), status_code


@app.route("/api/face/select", methods=["POST"])
def route_face_select():
    payload = request.get_json(silent=True) or {}
    ok, message = rostos.set_selected_face(str(payload.get("name", "")))
    status_code = 200 if ok else 404
    return jsonify({"ok": ok, "message": message, "face": rostos.face_status_snapshot()}), status_code


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
    speed = util.clamp(speed, -100, 100)
    steering = util.clamp(steering, -100, 100)

    with estado.control_lock:
        estado.control_state["mode"] = mode
        estado.control_state["manual_speed"] = speed
        estado.control_state["manual_steering"] = steering
        estado.control_state["updated_at"] = util.now()
        estado.control_state["last_source"] = source

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
            with estado.settings_lock:
                estado.settings["speed_limit_pct"] = pct
    with estado.settings_lock:
        return jsonify({"ok": True, "settings": dict(estado.settings)})


@app.route("/api/stop", methods=["POST"])
def route_stop():
    arduino.send_to_arduino(0, 0, "manual", "stop")
    with estado.control_lock:
        estado.control_state["mode"] = "manual"
        estado.control_state["manual_speed"] = 0
        estado.control_state["manual_steering"] = 0
        estado.control_state["active_speed"] = 0
        estado.control_state["active_steering"] = 0
        estado.control_state["updated_at"] = util.now()
        estado.control_state["last_source"] = "stop"
        estado.control_state["arduino_sent"] = True
        estado.control_state["arduino_error"] = None

    resp = jsonify({"ok": True})
    resp.headers["Connection"] = "close"
    return resp
