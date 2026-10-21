#!/usr/bin/env python3
"""Calibra a montagem do RPLIDAR usando o Kinect como referencia da frente do AGV.

Com os dois sensores fixos no AGV e vendo as mesmas paredes, a faixa horizontal da
profundidade do Kinect (que aponta para a frente) e encaixada na volta do laser por
ICP, partindo de todas as rotacoes. A rotacao encontrada da AGV_LIDAR_FRENTE_GRAUS e
o deslocamento da a posicao do laser em relacao ao Kinect.

Uso (com o servidor parado, o AGV parado e paredes a 1-4 m na frente do Kinect):
    .venv/bin/python scripts/calibrar_lidar.py

Nao envia nada ao Arduino.
"""

import os
import sys
import threading
import time

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)

# Mede a montagem "crua": zera a montagem atual antes de o pc_agv ler a configuracao.
os.environ.update(AGV_LIDAR_FRENTE_GRAUS="0", AGV_LIDAR_X_M="0", AGV_LIDAR_Y_M="0", AGV_LIDAR_MASCARA="",
                  AGV_ARDUINO_ENABLED="0")
from pc_agv import arquivo_env  # noqa: E402

arquivo_env.carregar()

import numpy as np  # noqa: E402

from pc_agv import estado  # noqa: E402
from pc_agv.hardware import kinect, rplidar  # noqa: E402
from pc_agv.mapeamento import varredura  # noqa: E402
from pc_agv.mapeamento.calibracao import CASAMENTO_MIN, calibrar  # noqa: E402

QUADROS = 10


def coletar(segundos: float = 30.0):
    kinect.release_kinect_usb_claims()
    for alvo in (kinect.capture_loop, rplidar.lidar_loop):
        threading.Thread(target=alvo, daemon=True).start()
    quadros, voltas = [], []
    fim = time.time() + segundos
    while time.time() < fim and (len(quadros) < QUADROS or len(voltas) < QUADROS):
        time.sleep(0.3)
        with estado.state_lock:
            depth, is_mm, ok = estado.state["depth"], estado.state["depth_mm"], estado.state["kinect_ok"]
        if ok and depth is not None:
            quadros.append(varredura.varredura(depth, is_mm))
        leitura = rplidar.leitura_recente()
        if leitura is not None:
            voltas.append(leitura["pontos"])
    estado.stop_event.set()
    time.sleep(1.5)  # o laser desliga o motor ao sair
    return quadros, voltas


def main() -> int:
    if os.path.exists("/tmp/agv_launch_production.lock"):
        import fcntl
        with open("/tmp/agv_launch_production.lock") as trava:
            try:
                fcntl.flock(trava.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                print("O servidor do AGV esta rodando: pare-o (Ctrl+C) antes de calibrar.")
                return 1
    print("Lendo o Kinect e o RPLIDAR (ate 30 s)...")
    quadros, voltas = coletar()
    print(f"quadros do Kinect: {len(quadros)} | voltas do LIDAR: {len(voltas)}")
    if not quadros or not voltas:
        print("Falta um dos sensores: confira o painel (Kinect e LIDAR online).")
        return 1
    kinect_pontos = quadros[-1]
    laser = np.vstack(voltas[-4:])
    if len(kinect_pontos) < 30:
        print(f"O Kinect so viu {len(kinect_pontos)} pontos: aponte-o para paredes a 1-4 m e tente de novo.")
        return 1
    resultado = calibrar(kinect_pontos, laser)
    if resultado is None:
        print("Nao foi possivel encaixar as duas varreduras.")
        return 1
    print(f"casamento: {resultado.casamento * 100:.0f}% dos pontos do Kinect a menos de 5 cm do laser"
          f" (mediana {resultado.mediana_m * 100:.1f} cm)")
    if resultado.ambiguo:
        print("Resultado AMBIGUO: outra rotacao bem diferente encaixa quase tao bem (o Kinect so ve uma parede"
              " reta?). Aponte o Kinect para um canto ou para objetos com forma e tente de novo.")
        return 2
    if resultado.casamento < CASAMENTO_MIN:
        print(f"Abaixo de {CASAMENTO_MIN * 100:.0f}%: o resultado NAO e confiavel. Os dois sensores estao fixos no AGV,"
              " vendo as mesmas paredes, e o laser na altura do campo do Kinect?")
        return 2
    print("\nAcrescente ao agv.env:")
    print(f"AGV_LIDAR_FRENTE_GRAUS={resultado.frente_graus:.0f}")
    print(f"# LIDAR em relacao ao Kinect: {resultado.lidar_x_m:+.3f} m para a frente, {resultado.lidar_y_m:+.3f} m"
          " para a esquerda.")
    print("# AGV_LIDAR_X_M = avanco do Kinect (Mapa > Calibracao, em m) + o primeiro valor;")
    print(f"AGV_LIDAR_Y_M={resultado.lidar_y_m:.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
