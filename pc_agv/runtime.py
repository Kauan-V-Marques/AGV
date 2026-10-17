"""Ciclo de vida das threads do agv (Kinect, RPLIDAR, ZED, rostos, autopiloto, controle, aprendizado e mapa)."""

import atexit
import threading

from pc_agv.config import (
    log,
)
from pc_agv import controle
from pc_agv import estado
from pc_agv.aprendizado import servico as aprendizado
from pc_agv.hardware import arduino, kinect, rplidar, zed
from pc_agv.mapeamento import servico as mapeamento
from pc_agv.visao import rostos


_runtime_lock = threading.Lock()

_runtime_started = False

_threads = []



def start_runtime() -> None:
    global _runtime_started

    with _runtime_lock:
        if _runtime_started:
            return
        _runtime_started = True
        estado.stop_event.clear()
        rostos.load_known_faces()
        aprendizado.iniciar()
        mapeamento.iniciar()

        specs = [
            ("agv-kinect", kinect.capture_loop),
            ("agv-lidar", rplidar.lidar_loop),
            ("agv-zed-face", rostos.face_worker_loop),
            ("agv-zed", zed.capture_loop),
            ("agv-autopilot", controle.autopilot_loop),
            ("agv-control", controle.control_loop),
            ("agv-aprendizado", aprendizado.aprendizado_loop),
            ("agv-mapeamento", mapeamento.mapeamento_loop),
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
        estado.stop_event.set()

    arduino.close()

    for thread in list(_threads):
        thread.join(timeout=1.5)
    _threads.clear()
    aprendizado.encerrar()
    mapeamento.encerrar()
    log.info("Runtime agv finalizado")



atexit.register(stop_runtime)
