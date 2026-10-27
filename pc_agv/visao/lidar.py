"""RPLIDAR: varredura 360 graus no referencial do AGV e folgas por setor (funcoes puras).

Referencial do AGV (o mesmo do mapeamento): origem no eixo das rodas, x para a
frente, y para a esquerda, angulos positivos para a esquerda. O RPLIDAR mede no
sentido horario visto de cima, com o 0 grau dele onde a montagem deixar.

As folgas sao medidas a partir da borda do chassi (um retangulo de largura x
comprimento), nao do centro do laser: "frente 0,30 m" quer dizer que ha 30 cm
entre o para-choque e o obstaculo, na mesma ideia das distancias do Kinect.

Mais perto que o alcance minimo (~0,15 m) o laser nao mede: a amostra volta sem
eco, igual a quando nao ha nada. Sem leitura nao quer dizer livre; perto_demais
acha esses buracos na volta e devolve onde provavelmente ha algo encostado.
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

CEGO_MIN_GRAUS = 8.0     # buraco mais estreito que isso (entre as leituras dos lados) e falha do laser
CEGO_LARGO_GRAUS = 30.0  # buraco a partir disso conta sempre (caixa de 10 cm encostada no laser: ~50 graus)
PERTO_M = 0.40           # leitura ate essa distancia do laser, na beira do buraco: o objeto continua ali


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


def _graus_perto(pontos, montagem: Montagem) -> np.ndarray:
    """Angulos (graus do AGV, vistos do laser) dos pontos a ate PERTO_M dele."""
    if pontos is None or not len(pontos):
        return np.empty(0)
    rel = np.asarray(pontos, dtype=np.float64).reshape(-1, 2) - (montagem.x_m, montagem.y_m)
    perto = np.hypot(rel[:, 0], rel[:, 1]) <= PERTO_M
    return np.degrees(np.arctan2(rel[perto, 1], rel[perto, 0])) % 360.0


def perto_demais(angulos_graus, distancias_m, montagem: Montagem = MONTAGEM_PADRAO, antes=None) -> np.ndarray:
    """Pontos (N, 2) no referencial do AGV onde provavelmente ha algo mais perto que min_m do laser.

    Recebe a volta inteira, inclusive as amostras sem eco (distancia 0). Abaixo do alcance
    minimo o RPLIDAR nao mede (sem eco ou um valor abaixo do minimo) e para_agv descarta a
    amostra: sem isto, um obstaculo encostado no laser deixa o setor vazio e ele parece livre.
    Um buraco na volta (amostras seguidas sem leitura, de pelo menos CEGO_MIN_GRAUS entre as
    leituras dos dois lados) conta como obstaculo quando:
      - tem CEGO_LARGO_GRAUS ou mais: um objeto pequeno inteiro dentro do minimo (uma caixa
        encostada no laser) deixa um buraco largo, com o fundo longe dos dois lados;
      - uma das leituras na beira dele esta a ate PERTO_M: o objeto continua alem do minimo;
      - alguma amostra dele mediu abaixo do minimo; ou
      - a volta anterior tinha algo a ate PERTO_M ali (antes: os pontos dela, inclusive os
        devolvidos aqui): o objeto chegou perto e entrou na zona cega.
    Um buraco estreito longe de tudo (fim de corredor comprido, falha do laser) continua sem
    ponto; um largo (vidro, superficie preta, porta para um salao) vira obstaculo, que e o
    lado seguro do erro. Volta inteira sem leitura (laser tapado) conta toda. Cada ponto fica
    a min_m do laser, na direcao da amostra.
    """
    graus = (montagem.frente_graus - np.asarray(angulos_graus, dtype=np.float64)) % 360.0
    distancias = np.asarray(distancias_m, dtype=np.float64)
    ordem = np.argsort(graus, kind="stable")
    graus, distancias = graus[ordem], distancias[ordem]
    mascarado = np.zeros(graus.shape, dtype=bool)
    for inicio, fim in montagem.mascara:
        mascarado |= _dentro_da_faixa(graus, inicio, fim)
    medido = np.isfinite(distancias) & (distancias >= montagem.min_m) & ~mascarado
    cego = ~medido & ~mascarado
    if medido.any() and cego.any():
        cego = _buracos_perto(graus, distancias, medido, cego, _graus_perto(antes, montagem))
    rad = np.radians(graus[cego])
    return np.stack([montagem.x_m + montagem.min_m * np.cos(rad), montagem.y_m + montagem.min_m * np.sin(rad)], axis=1)


def _buracos_perto(graus, distancias, medido, cego, graus_antes) -> np.ndarray:
    """Das amostras sem leitura (cego), as que estao num buraco que conta como obstaculo."""
    # Comeca numa amostra que nao e buraco: nenhum buraco fica partido no 0 grau.
    desloc = int(np.flatnonzero(~cego)[0])
    graus, distancias, medido, cego = (np.roll(v, -desloc) for v in (graus, distancias, medido, cego))
    perto = medido & (distancias <= PERTO_M)
    curto = cego & np.isfinite(distancias) & (distancias > 0)
    borda = np.diff(np.concatenate([[0], cego.astype(np.int8), [0]]))
    conta = np.zeros_like(cego)
    for inicio, fim in zip(np.flatnonzero(borda == 1), np.flatnonzero(borda == -1)):
        de, ate = inicio - 1, fim % len(graus)  # leituras dos dois lados do buraco [inicio, fim)
        largura = (graus[ate] - graus[de]) % 360.0 if de != ate else 360.0
        if largura < CEGO_MIN_GRAUS:
            continue
        if (largura >= CEGO_LARGO_GRAUS or perto[de] or perto[ate] or curto[inicio:fim].any()
                or _dentro_da_faixa(graus_antes, graus[de], graus[ate]).any()):
            conta[inicio:fim] = True
    return np.roll(conta, desloc)


def espaco_de_giro(pontos: np.ndarray, corpo: Corpo = CORPO_PADRAO) -> Optional[float]:
    """Sobra (m) entre o circulo que os cantos do chassi descrevem girando no lugar e o
    obstaculo mais proximo. None = nenhum ponto em volta. Pontos dentro do chassi sao o
    proprio AGV e nao contam."""
    meia = corpo.largura_m / 2.0
    pontos = np.asarray(pontos, dtype=np.float64).reshape(-1, 2)
    fora = ~((pontos[:, 0] <= corpo.frente_m) & (pontos[:, 0] >= -corpo.tras_m) & (np.abs(pontos[:, 1]) <= meia))
    if not fora.any():
        return None
    raio_cantos = math.hypot(max(corpo.frente_m, corpo.tras_m), meia)
    return float(np.hypot(pontos[fora, 0], pontos[fora, 1]).min()) - raio_cantos


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
