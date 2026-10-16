"""Simulador de quadros de profundidade do Kinect v1 numa sala 2D (apoio aos testes do mapa).

Lanca um raio por coluna da imagem ate as paredes (segmentos) e devolve a profundidade
em mm, como o Kinect: a distancia z ao longo do eixo da camera, nao o comprimento do
raio. O ruido segue o modelo do Kinect v1, que cresce com o quadrado da distancia
(Nguyen, Izadi e Lovell, 2012).
"""

import math

import numpy as np

from pc_agv.config import KINECT_DEPTH_CX, KINECT_DEPTH_FX
from pc_agv.mapeamento.geometria import Pose, compor


def caixa(x0, y0, x1, y1):
    return [((x0, y0), (x1, y0)), ((x1, y0), (x1, y1)), ((x1, y1), (x0, y1)), ((x0, y1), (x0, y0))]


def sala():
    """Sala 6 x 4 m com duas caixas e dois pedacos de parede (quebram a simetria)."""
    segmentos = caixa(-1.0, -2.0, 5.0, 2.0) + caixa(2.0, 0.8, 2.6, 1.3) + caixa(3.5, -1.2, 3.9, -0.6)
    segmentos += [((0.5, -2.0), (0.5, -1.4)), ((4.3, 1.2), (5.0, 1.2))]
    return np.array(segmentos, dtype=float)


def quadro(pose: Pose, segmentos: np.ndarray, ruido=False, rng=None, largura=640, altura=480) -> np.ndarray:
    escala = largura / 640.0
    fx, cx = KINECT_DEPTH_FX * escala, KINECT_DEPTH_CX * escala
    u = np.arange(largura) + 0.5
    alfa = np.arctan(-(u - cx) / fx)
    direcao = np.stack([np.cos(alfa + pose.theta), np.sin(alfa + pose.theta)], axis=1)
    a, b = segmentos[:, 0, :], segmentos[:, 1, :]
    e = b - a
    det = direcao[:, None, 0] * (-e[None, :, 1]) - direcao[:, None, 1] * (-e[None, :, 0])
    rx = a[None, :, 0] - pose.x
    ry = a[None, :, 1] - pose.y
    with np.errstate(divide="ignore", invalid="ignore"):
        t = (rx * (-e[None, :, 1]) - ry * (-e[None, :, 0])) / det
        s = (direcao[:, None, 0] * ry - direcao[:, None, 1] * rx) / det
    t = np.where((t > 0) & (s >= 0) & (s <= 1), t, np.inf).min(axis=1)
    z = t * np.cos(alfa)
    mm = np.where(np.isfinite(z), z * 1000.0, 0.0)
    depth = np.tile(mm, (altura, 1))
    if ruido:
        sigma_mm = (0.0012 + 0.0019 * (depth / 1000.0 - 0.4) ** 2) * 1000.0
        depth = depth + (rng or np.random.default_rng(0)).normal(0.0, 1.0, depth.shape) * sigma_mm * (depth > 0)
    return np.clip(depth, 0, 9000).astype(np.uint16)


def reta(inicio: Pose, passos: int, passo_m: float) -> list[Pose]:
    poses, pose = [], inicio
    for _ in range(passos):
        pose = compor(pose, Pose(passo_m, 0.0, 0.0))
        poses.append(pose)
    return poses


def giro(inicio: Pose, passos: int, passo_graus: float) -> list[Pose]:
    poses, pose = [], inicio
    for _ in range(passos):
        pose = compor(pose, Pose(0.0, 0.0, math.radians(passo_graus)))
        poses.append(pose)
    return poses
