"""O Kinect usado como um "laser": varredura 2D a partir da profundidade (funcoes puras).

Uma faixa horizontal da imagem de profundidade vira uma linha de pontos no plano do
chao. Referencial do AGV: x para a frente, y para a esquerda, em metros.
O Kinect v1 mede a distancia z ao longo do eixo da camera (nao o comprimento do raio),
entao o ponto da coluna u fica em x = z e y = -(u - cx) * z / fx.
"""

import numpy as np

from pc_agv.config import KINECT_DEPTH_CX, KINECT_DEPTH_FX
from pc_agv.visao import profundidade


LARGURA_CALIBRADA = 640       # fx e cx valem para a imagem de profundidade 640 x 480
FAIXA_ALTURA = (0.40, 0.60)   # linhas da imagem usadas: perto do horizonte, acima do chao
ALCANCE_MIN_M = 0.5           # o Kinect v1 nao mede abaixo disso
ALCANCE_MAX_M = 4.5           # acima disso o erro passa de alguns centimetros
RAIOS = 160                   # um raio a cada 4 colunas
PERCENTIL = 0.10
MINIMO_LEITURAS = 8           # menos pixels validos que isso na direcao: sem leitura


def varredura(depth: np.ndarray, is_mm: bool, fx: float = KINECT_DEPTH_FX, cx: float = KINECT_DEPTH_CX) -> np.ndarray:
    """Pontos (N, 2) dos obstaculos mais proximos em cada direcao, no referencial do AGV."""
    depth = np.asarray(depth)
    altura, largura = depth.shape[:2]
    escala = largura / LARGURA_CALIBRADA
    fx, cx = fx * escala, cx * escala

    inicio, fim = int(altura * FAIXA_ALTURA[0]), max(int(altura * FAIXA_ALTURA[1]), int(altura * FAIXA_ALTURA[0]) + 1)
    metros = profundidade.depth_to_meters(depth[inicio:fim], is_mm)
    passo = max(1, largura // RAIOS)
    raios = largura // passo
    blocos = metros[:, : raios * passo].reshape(fim - inicio, raios, passo).transpose(1, 0, 2).reshape(raios, -1)
    # Obstaculo mais proximo de cada direcao, pelo percentil 10 das leituras: o minimo
    # de centenas de pixels com ruido puxaria a parede para perto. Sem leitura -> inf.
    quantidade = np.isfinite(blocos).sum(axis=1)
    ordenados = np.sort(np.where(np.isfinite(blocos), blocos, np.inf), axis=1)
    indice = np.minimum((np.maximum(quantidade - 1, 0) * PERCENTIL).astype(np.int64), blocos.shape[1] - 1)
    z = np.where(quantidade >= MINIMO_LEITURAS, ordenados[np.arange(raios), indice], np.inf)
    u = np.arange(raios) * passo + passo / 2.0

    validos = (z >= ALCANCE_MIN_M) & (z <= ALCANCE_MAX_M)
    x = z[validos]
    y = -(u[validos] - cx) * x / fx
    return np.stack([x, y], axis=1).astype(np.float64)
