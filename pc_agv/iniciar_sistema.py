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

import threading
import time

from werkzeug.serving import make_server

from pc_agv.config import DEFAULT_HOST, DEFAULT_PORT, log
from pc_agv.runtime import start_runtime, stop_runtime
from pc_agv.web.rotas import app


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


if __name__ == "__main__":
    raise SystemExit(main())
