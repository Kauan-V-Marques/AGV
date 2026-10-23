"""Aplicacao Flask: painel, streams MJPEG e API REST do agv."""

import math
import mimetypes
import os
import socket
import threading
from typing import Optional

from flask import Flask, Response, jsonify, request, send_from_directory

from pc_agv.config import (
    DEFAULT_PORT,
    DEPTH_FPS,
    MAX_UPLOAD_MB,
    VIDEO_FPS,
    ZED_STREAM_FPS,
)
from pc_agv import estado
from pc_agv import telemetria
from pc_agv import util
from pc_agv.aprendizado import servico as aprendizado
from pc_agv.hardware import arduino
from pc_agv.hardware import kinect
from pc_agv.hardware import rplidar
from pc_agv.mapeamento import servico as mapeamento
from pc_agv.visao import rostos
from pc_agv.web import mjpeg


# O painel usa modulos ES: o navegador so os executa com o tipo JavaScript correto,
# e no Windows o mimetypes pode herdar outro tipo do registro do sistema.
mimetypes.add_type("text/javascript", ".js")

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

app = Flask(__name__, static_folder=STATIC_DIR, static_url_path="/static")
# Sempre revalida painel.css/painel.js para nao servir versao antiga apos atualizar o agv.
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0
# Evita que um upload enorme esgote a memoria do notebook do agv.
app.config["MAX_CONTENT_LENGTH"] = int(MAX_UPLOAD_MB * 1024 * 1024)


@app.errorhandler(413)
def route_file_too_large(_error):
    return jsonify({"ok": False, "error": "file_too_large", "max_mb": MAX_UPLOAD_MB}), 413



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


def _inteiro(nome: str, minimo: int, maximo: int) -> Optional[int]:
    try:
        valor = int(request.args.get(nome, ""))
    except ValueError:
        return None
    return max(minimo, min(maximo, valor))


def _stream(kind: str, fps_maximo: int) -> Response:
    """Stream MJPEG. O painel pode pedir menos (?fps=, ?q= qualidade do JPEG, ?w= largura)
    num celular ou numa rede lenta; nunca mais que o configurado no servidor."""
    fps = _inteiro("fps", 1, max(1, fps_maximo)) or fps_maximo
    gerador = mjpeg.mjpeg_generator(kind, fps, qualidade=_inteiro("q", 20, 95), largura=_inteiro("w", 160, 1920))
    return Response(gerador, mimetype="multipart/x-mixed-replace; boundary=frame")


@app.route("/video")
def route_video():
    return _stream("rgb", VIDEO_FPS)


@app.route("/depth_map")
def route_depth_map():
    return _stream("depth", DEPTH_FPS)


@app.route("/zed")
def route_zed():
    return _stream("zed", ZED_STREAM_FPS)


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
    try:
        angle = float(payload.get("angle", 0.0))
    except (TypeError, ValueError):
        angle = math.nan
    if not math.isfinite(angle):
        return jsonify({"ok": False, "error": "invalid_angle"}), 400
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
    if mode not in estado.MODOS:
        mode = "manual"
    if mode == "ia" and not aprendizado.tem_modelo():
        return jsonify({"ok": False, "error": "sem_modelo"}), 409

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
        for chave in ("speed_limit_pct", "auto_speed_limit_pct"):
            if chave not in payload:
                continue
            try:
                pct = max(0, min(100, int(payload[chave])))
            except Exception:
                return jsonify({"ok": False, "error": f"invalid {chave}"}), 400
            with estado.settings_lock:
                estado.settings[chave] = pct
    with estado.settings_lock:
        return jsonify({"ok": True, "settings": dict(estado.settings)})


def _enviar_parada() -> threading.Thread:
    """0,0 imediato numa thread propria: a resposta da parada nunca espera a serial (se
    ela estiver presa, o agv-control segue mandando 0,0 e o firmware para em 500 ms)."""
    envio = threading.Thread(target=arduino.send_to_arduino, args=(0, 0, "manual", "stop"),
                             kwargs={"conectar": False}, daemon=True, name="agv-parada")
    envio.start()
    return envio


@app.route("/api/stop", methods=["POST"])
def route_stop():
    # Primeiro o estado: mesmo que a serial esteja presa ou desconectada, o modo volta a
    # manual e o agv-control passa a mandar 0,0. So depois o 0,0 imediato, sem reconectar.
    with estado.control_lock:
        modo_anterior = estado.control_state["mode"]
        estado.control_state["mode"] = "manual"
        estado.control_state["manual_speed"] = 0
        estado.control_state["manual_steering"] = 0
        estado.control_state["active_speed"] = 0
        estado.control_state["active_steering"] = 0
        estado.control_state["updated_at"] = util.now()
        estado.control_state["last_source"] = "stop"
        estado.control_state["arduino_sent"] = True
        estado.control_state["arduino_error"] = None
    _enviar_parada()
    if modo_anterior == "ia":
        # A IA levou o operador a apertar a parada: isso entra como penalidade no treino.
        aprendizado.registrar_parada()

    resp = jsonify({"ok": True})
    resp.headers["Connection"] = "close"
    return resp


# ── Auto-aprendizado ────────────────────────────────────────────────
_STATUS_ERRO_IA = {
    "banco_indisponivel": 503,
    "treino_em_andamento": 409,
    "amostras_insuficientes": 409,
    "modelo_inexistente": 404,
    "modelo_incompativel": 409,
}


def _erro_ia(codigo: str):
    return jsonify({"ok": False, "error": codigo}), _STATUS_ERRO_IA.get(codigo, 400)


@app.route("/api/ia/status")
def route_ia_status():
    return jsonify({"ok": True, "ia": aprendizado.status_snapshot()})


@app.route("/api/ia/config", methods=["POST"])
def route_ia_config():
    ok, resultado = aprendizado.configurar(request.get_json(silent=True) or {})
    if not ok:
        return _erro_ia(resultado)
    return jsonify({"ok": True, "preferencias": resultado})


@app.route("/api/ia/treinar", methods=["POST"])
def route_ia_treinar():
    ok, resultado = aprendizado.iniciar_treino()
    if not ok:
        return _erro_ia(resultado)
    return jsonify({"ok": True, "treino": resultado}), 202


@app.route("/api/ia/modelos")
def route_ia_modelos():
    return jsonify({"ok": True, "modelos": aprendizado.listar_modelos()})


@app.route("/api/ia/modelos/<int:modelo_id>/ativar", methods=["POST"])
def route_ia_ativar_modelo(modelo_id: int):
    ok, resultado = aprendizado.ativar_modelo(modelo_id)
    if not ok:
        return _erro_ia(resultado)
    return jsonify({"ok": True, "modelo": resultado})


@app.route("/api/ia/apagar", methods=["POST"])
def route_ia_apagar():
    payload = request.get_json(silent=True) or {}
    if payload.get("confirmar") is not True:
        return _erro_ia("confirmacao_necessaria")
    ok, resultado = aprendizado.apagar(str(payload.get("alvo", "")))
    if not ok:
        return _erro_ia(resultado)
    return jsonify({"ok": True, "apagado": resultado})


# ── Auto-mapeamento ─────────────────────────────────────────────────
_STATUS_ERRO_MAPA = {
    "banco_indisponivel": 503,
    "mapa_inexistente": 404,
    "mapa_somente_leitura": 409,
    "mapa_vazio": 409,
    "mapa_ilegivel": 422,
}


def _resposta_mapa(ok: bool, resultado):
    if not ok:
        return jsonify({"ok": False, "error": resultado}), _STATUS_ERRO_MAPA.get(resultado, 400)
    return jsonify({"ok": True, "resultado": resultado, "mapa": mapeamento.status_detalhado()})


@app.route("/api/lidar")
def route_lidar():
    return jsonify({"ok": True, "lidar": rplidar.varredura_publica()})


@app.route("/api/mapa/status")
def route_mapa_status():
    return jsonify({"ok": True, "mapa": mapeamento.status_detalhado()})


@app.route("/api/mapa/ligar", methods=["POST"])
def route_mapa_ligar():
    return _resposta_mapa(*mapeamento.ligar())


@app.route("/api/mapa/pausar", methods=["POST"])
def route_mapa_pausar():
    return _resposta_mapa(*mapeamento.pausar())


@app.route("/api/mapa/novo", methods=["POST"])
def route_mapa_novo():
    return _resposta_mapa(*mapeamento.novo())


@app.route("/api/mapa/salvar", methods=["POST"])
def route_mapa_salvar():
    payload = request.get_json(silent=True) or {}
    return _resposta_mapa(*mapeamento.salvar(payload.get("nome", "")))


@app.route("/api/mapa/config", methods=["POST"])
def route_mapa_config():
    return _resposta_mapa(*mapeamento.configurar(request.get_json(silent=True) or {}))


@app.route("/api/mapa/lista")
def route_mapa_lista():
    return jsonify({"ok": True, "mapas": mapeamento.listar()})


@app.route("/api/mapa/<int:mapa_id>/abrir", methods=["POST"])
def route_mapa_abrir(mapa_id: int):
    return _resposta_mapa(*mapeamento.abrir(mapa_id))


@app.route("/api/mapa/<int:mapa_id>/apagar", methods=["POST"])
def route_mapa_apagar(mapa_id: int):
    return _resposta_mapa(*mapeamento.apagar(mapa_id))


@app.route("/api/mapa/imagem.png")
def route_mapa_imagem():
    dados = mapeamento.imagem_png()
    if dados is None:
        return "", 204
    resposta = Response(dados, mimetype="image/png")
    resposta.headers["Cache-Control"] = "no-store"
    return resposta
