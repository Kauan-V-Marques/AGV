"""RPLIDAR: varredura 360 graus no referencial do AGV e folgas por setor (funcoes puras).

Referencial do AGV (o mesmo do mapeamento): origem no eixo das rodas, x para a
frente, y para a esquerda, angulos positivos para a esquerda. O RPLIDAR mede no
sentido horario visto de cima, com o 0 grau dele onde a montagem deixar.

As folgas sao medidas a partir da borda do chassi (um retangulo de largura x
comprimento), nao do centro do laser: "frente 0,30 m" quer dizer que ha 30 cm
entre o para-choque e o obstaculo, na mesma ideia das distancias do Kinect.
"""

import math
from typing import NamedTuple, Optional

import numpy as np


class Montagem(NamedTuple):
    frente_graus: float = 0.0   # angulo do LIDAR que aponta para a frente do AGV
    x_m: float = 0.0            # posicao do LIDAR em relacao ao eixo das rodas
    y_m: float = 0.0
    min_m: float = 0.15         # mais perto que isso: o proprio AGV ou ruido
    max_m: float = 8.0
    mascara: tuple = ()         # faixas (graus do AGV) ignoradas, ex.: ((170, 190),)


class Corpo(NamedTuple):
    """Retangulo do AGV em torno do eixo das rodas (metros)."""
    frente_m: float = 0.30      # do eixo ate o para-choque dianteiro
    tras_m: float = 0.30        # do eixo ate a traseira
    largura_m: float = 0.50


MONTAGEM_PADRAO = Montagem()
CORPO_PADRAO = Corpo()

SETORES = ("frente", "frente_esq", "esquerda", "tras_esq", "tras", "tras_dir", "direita", "frente_dir")


def ler_mascara(texto: str) -> tuple:
    """'170:190, 85:95' -> ((170.0, 190.0), (85.0, 95.0)). Trechos invalidos sao ignorados."""
    faixas = []
    for trecho in str(texto or "").split(","):
        partes = trecho.strip().split(":")
        if len(partes) != 2:
            continue
        try:
            inicio, fim = float(partes[0]), float(partes[1])
        except ValueError:
            continue
        if math.isfinite(inicio) and math.isfinite(fim):
            faixas.append((inicio, fim))
    return tuple(faixas)


def _dentro_da_faixa(graus: np.ndarray, inicio: float, fim: float) -> np.ndarray:
    """Angulos (0..360) dentro de [inicio, fim], aceitando faixas que passam pelo 0."""
    inicio, fim = inicio % 360.0, fim % 360.0
    if inicio <= fim:
        return (graus >= inicio) & (graus <= fim)
    return (graus >= inicio) | (graus <= fim)


def para_agv(angulos_graus, distancias_m, montagem: Montagem = MONTAGEM_PADRAO) -> tuple[np.ndarray, np.ndarray]:
    """Converte a varredura bruta do LIDAR.

    Devolve (pontos, angulos): pontos (N, 2) em metros no referencial do AGV, ordenados
    pelo angulo visto do LIDAR (o ICP do mapa usa a ordem para achar as superficies), e
    o angulo de cada ponto em radianos (0 = frente, positivo para a esquerda).
    """
    angulos = np.asarray(angulos_graus, dtype=np.float64)
    distancias = np.asarray(distancias_m, dtype=np.float64)
    graus_agv = (montagem.frente_graus - angulos) % 360.0  # horario -> anti-horario
    validos = (
        np.isfinite(distancias)
        & (distancias >= montagem.min_m)
        & (distancias <= montagem.max_m)
        & (angulos >= 0.0)
        & (angulos < 360.0)
    )
    for inicio, fim in montagem.mascara:
        validos &= ~_dentro_da_faixa(graus_agv, inicio, fim)
    graus_agv, distancias = graus_agv[validos], distancias[validos]
    ordem = np.argsort(graus_agv, kind="stable")
    graus_agv, distancias = graus_agv[ordem], distancias[ordem]
    rad = np.radians(graus_agv)
    pontos = np.stack([montagem.x_m + distancias * np.cos(rad), montagem.y_m + distancias * np.sin(rad)], axis=1)
    return pontos, np.arctan2(np.sin(rad), np.cos(rad))


def _menor(valores: np.ndarray) -> Optional[float]:
    return round(float(valores.min()), 3) if valores.size else None


def folgas(pontos: np.ndarray, corpo: Corpo = CORPO_PADRAO) -> dict[str, Optional[float]]:
    """Folga entre a borda do AGV e o obstaculo mais proximo em cada um dos 8 setores.

    frente/tras: obstaculos no corredor da largura do AGV. esquerda/direita: ao lado do
    chassi. Diagonais: distancia ate o canto mais proximo. None = nada visto ali.
    """
    pontos = np.asarray(pontos, dtype=np.float64).reshape(-1, 2)
    x, y = pontos[:, 0], pontos[:, 1]
    meia = corpo.largura_m / 2.0
    frente, tras = corpo.frente_m, -corpo.tras_m

    no_corredor = np.abs(y) <= meia
    ao_lado = (x >= tras) & (x <= frente)
    adiante, atras = x > frente, x < tras
    lado_esq, lado_dir = y > meia, y < -meia

    def canto(sel: np.ndarray, cx: float, cy: float) -> Optional[float]:
        return _menor(np.hypot(x[sel] - cx, y[sel] - cy))

    return {
        "frente": _menor(x[no_corredor & adiante] - frente),
        "frente_esq": canto(adiante & lado_esq, frente, meia),
        "esquerda": _menor(y[ao_lado & lado_esq] - meia),
        "tras_esq": canto(atras & lado_esq, tras, meia),
        "tras": _menor(tras - x[no_corredor & atras]),
        "tras_dir": canto(atras & lado_dir, tras, -meia),
        "direita": _menor(-meia - y[ao_lado & lado_dir]),
        "frente_dir": canto(adiante & lado_dir, frente, -meia),
    }
