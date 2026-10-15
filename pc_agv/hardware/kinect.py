"""Kinect v1: captura RGB + profundidade, tilt/estabilizacao e liberacao do USB."""

import math
import subprocess
import threading
from typing import Optional

import cv2
import numpy as np

from pc_agv.config import (
    KINECT_RELEASE_UVCVIDEO,
    KINECT_STABILIZATION_ENABLED,
    KINECT_TILT_DEADBAND,
    KINECT_TILT_GAIN,
    KINECT_TILT_INTERVAL,
    KINECT_TILT_MANUAL_HOLD,
    KINECT_TILT_MAX,
    KINECT_TILT_MAX_STEP,
    KINECT_TILT_MIN,
    KINECT_TILT_NEUTRAL,
    KINECT_TILT_PITCH_DEADBAND,
    KINECT_TILT_SMOOTHING,
)
from pc_agv import estado
from pc_agv import util
from pc_agv.visao import profundidade


try:
    import freenect  # type: ignore[import-not-found]
except Exception:
    freenect = None

# KINECT_STABILIZATION_ENABLED vem do config e pode ser alterado em tempo de
# execucao por set_kinect_stabilization (rebind local deste modulo).

_tilt_last_set_at = 0.0

_tilt_last_target = 0.0

_tilt_manual_until = 0.0

_tilt_manual_target = 0.0

_tilt_pitch_filtered = 0.0

_tilt_control_lock = threading.Lock()



def release_kinect_usb_claims() -> None:
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


def command_kinect_tilt(angle: float) -> tuple[bool, str, Optional[float]]:
    global _tilt_manual_until, _tilt_manual_target, _tilt_last_set_at, _tilt_last_target

    target = float(max(KINECT_TILT_MIN, min(KINECT_TILT_MAX, float(angle))))
    _tilt_manual_target = target
    _tilt_manual_until = util.now() + KINECT_TILT_MANUAL_HOLD
    _tilt_last_target = target
    _tilt_last_set_at = 0.0

    with estado.state_lock:
        estado.state["tilt_target_deg"] = round(target, 2)

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


def set_kinect_stabilization(enabled: bool) -> bool:
    global KINECT_STABILIZATION_ENABLED
    with _tilt_control_lock:
        KINECT_STABILIZATION_ENABLED = bool(enabled)
    return KINECT_STABILIZATION_ENABLED


def get_kinect_stabilization() -> bool:
    with _tilt_control_lock:
        return bool(KINECT_STABILIZATION_ENABLED)


def _do_tilt_body(dev) -> None:
    """Ajusta o tilt do Kinect para manter o frame mais nivelado."""
    global _tilt_last_set_at, _tilt_last_target, _tilt_pitch_filtered

    stabilization_enabled = get_kinect_stabilization()
    # Mesmo com estabilizacao OFF, respeita comando manual recente de tilt.
    if (not stabilization_enabled) and (util.now() >= _tilt_manual_until):
        return

    now = util.now()
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

        with estado.state_lock:
            estado.state["tilt_deg"] = raw_tilt
            estado.state["tilt_target_deg"] = round(target_f, 2)
            estado.state["accel"] = [round(ax, 3), round(ay, 3), round(az, 3)]
            if abs(ax) < 0.001 and abs(ay) < 0.001 and abs(az) < 0.001:
                estado.state["kinect_error"] = "tilt sem resposta do acelerometro"
            elif str(estado.state.get("kinect_error") or "").startswith("tilt sem resposta"):
                estado.state["kinect_error"] = None
    except Exception as exc:
        with estado.state_lock:
            estado.state["kinect_error"] = f"falha estabilizacao: {exc}"


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



def request_reconnect() -> None:
    """Para a captura atual e libera o USB; o loop de captura reabre o Kinect."""
    if freenect is not None:
        try:
            freenect.sync_stop()
        except Exception:
            pass
    release_kinect_usb_claims()



def capture_loop() -> None:
    if freenect is None:
        while not estado.stop_event.is_set():
            with estado.state_lock:
                estado.state["kinect_ok"] = False
                estado.state["kinect_error"] = "python3-freenect nao disponivel no sistema"
            estado.stop_event.wait(1.0)
        return

    # Fila mínima para passar dados brutos das callbacks (rápidas) para o
    # thread de processamento pesado (face detection, depth metrics).
    _raw_state: dict = {"rgb": None, "depth": None, "rgb_fresh": False, "depth_fresh": False}
    _raw_lock = threading.Lock()
    _process_event = threading.Event()

    # FPS contadores
    rgb_counter = [0]
    depth_counter = [0]
    fps_mark = [util.now()]

    def _process_loop():
        """Thread separado: face detection + depth metrics (pesado, libera GIL)."""
        while not estado.stop_event.is_set():
            _process_event.wait(timeout=0.1)
            _process_event.clear()

            with _raw_lock:
                rgb = _raw_state["rgb"] if _raw_state["rgb_fresh"] else None
                dep = _raw_state["depth"] if _raw_state["depth_fresh"] else None
                _raw_state["rgb_fresh"] = False
                _raw_state["depth_fresh"] = False

            if rgb is not None:
                with estado.state_lock:
                    estado.state["rgb"] = rgb
                    estado.state["kinect_ok"] = True
                    estado.state["kinect_error"] = None
                    estado.state["last_frame_at"] = util.now()
                # FPS
                rgb_counter[0] += 1
                now = util.now()
                elapsed = now - fps_mark[0]
                if elapsed >= 1.0:
                    with estado.state_lock:
                        estado.state["fps_rgb"] = round(rgb_counter[0] / elapsed, 1)
                        estado.state["fps_depth"] = round(depth_counter[0] / elapsed, 1)
                    rgb_counter[0] = 0
                    depth_counter[0] = 0
                    fps_mark[0] = now

            if dep is not None:
                depth_array = np.asarray(dep)
                is_mm, distance, left, center, right = profundidade.compute_depth_metrics(depth_array)
                with estado.state_lock:
                    estado.state["depth"] = depth_array
                    estado.state["depth_mm"] = is_mm
                    estado.state["distance_m"] = distance
                    estado.state["left_clearance_m"] = left
                    estado.state["center_clearance_m"] = center
                    estado.state["right_clearance_m"] = right
                depth_counter[0] += 1

    proc_thread = threading.Thread(target=_process_loop, daemon=True, name="agv-kinect-proc")
    proc_thread.start()

    while not estado.stop_event.is_set():
        release_kinect_usb_claims()
        available, error = _kinect_device_available()
        if not available:
            with estado.state_lock:
                estado.state["kinect_ok"] = False
                estado.state["kinect_error"] = error
            estado.stop_event.wait(4.0)
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
            if estado.stop_event.is_set():
                raise freenect.Kill
            _do_tilt_body(dev)

        try:
            freenect.runloop(depth=_depth_cb, video=_video_cb, body=_body_cb)
        except Exception as exc:
            with estado.state_lock:
                estado.state["kinect_ok"] = False
                estado.state["kinect_error"] = str(exc) or "falha no Kinect"
            try:
                if hasattr(freenect, "sync_stop"):
                    freenect.sync_stop()
            except Exception:
                pass
            if not estado.stop_event.is_set():
                estado.stop_event.wait(5.0)
