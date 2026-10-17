"""Calibracao da montagem do RPLIDAR pelo Kinect (funcoes puras).

A faixa horizontal da profundidade do Kinect aponta para a frente do AGV. Encaixando-a
na volta do laser (ICP partindo de todas as rotacoes), a rotacao encontrada diz para
onde o laser "olha" e o deslocamento, onde ele esta. Usado por scripts/calibrar_lidar.py.
"""

import math
from typing import NamedTuple, Optional

import numpy as np

from pc_agv.mapeamento.geometria import Pose, icp, transformar, vizinhos


CASAMENTO_MIN = 0.80   # fracao dos pontos do Kinect a menos de 5 cm do laser para confiar
AMBIGUO_GRAUS = 15     # outra rotacao, longe desta, que case quase tao bem ...
AMBIGUO_MARGEM = 0.10  # ... (ate 10 pontos percentuais abaixo) torna a resposta ambigua


class Calibracao(NamedTuple):
    casamento: float        # fracao dos pontos do Kinect a menos de 5 cm do laser
    mediana_m: float
    frente_graus: float     # valor para AGV_LIDAR_FRENTE_GRAUS
    lidar_x_m: float        # posicao do laser no referencial do Kinect
    lidar_y_m: float
    ambiguo: bool

    @property
    def confiavel(self) -> bool:
        return not self.ambiguo and self.casamento >= CASAMENTO_MIN


def _diferenca_graus(a: float, b: float) -> float:
    return abs(math.degrees(math.atan2(math.sin(a - b), math.cos(a - b))))


def calibrar(kinect_pontos: np.ndarray, laser: np.ndarray) -> Optional[Calibracao]:
    """kinect_pontos: varredura do Kinect (referencial do Kinect); laser: volta do LIDAR
    medida com a montagem zerada. Uma parede reta casa em qualquer parede do laser: se
    outra rotacao bem diferente casa quase tao bem, a resposta e ambigua."""
    laser = laser[np.argsort(np.arctan2(laser[:, 1], laser[:, 0]))]
    candidatos = []
    for graus in range(0, 360, 5):
        r = icp(kinect_pontos, laser, Pose(0.0, 0.0, math.radians(graus)), iteracoes=25, dist_max_m=0.5,
                aproximacao=25)
        if not r.ok:
            continue
        _, distancia = vizinhos(transformar(kinect_pontos, r.pose), laser)
        candidatos.append((float((distancia < 0.05).mean()), r.pose, float(np.median(distancia))))
    if not candidatos:
        return None
    casamento, pose, mediana = max(candidatos, key=lambda c: c[0])
    ambiguo = any(
        _diferenca_graus(c[1].theta, pose.theta) > AMBIGUO_GRAUS and c[0] >= casamento - AMBIGUO_MARGEM
        for c in candidatos
    )
    # origem do laser no referencial do Kinect: R^T (0 - t)
    c, s = math.cos(pose.theta), math.sin(pose.theta)
    return Calibracao(
        casamento=casamento,
        mediana_m=mediana,
        frente_graus=(-math.degrees(pose.theta)) % 360.0,
        lidar_x_m=-(c * pose.x + s * pose.y),
        lidar_y_m=-(-s * pose.x + c * pose.y),
        ambiguo=ambiguo,
    )
