"""Poses no plano e ICP 2D (funcoes puras).

Pose = (x, y, theta): leva um ponto do referencial do AGV para o do mapa,
p_mapa = R(theta) * p_agv + (x, y).
"""

import math
from typing import NamedTuple, Optional

import numpy as np


class Pose(NamedTuple):
    x: float = 0.0
    y: float = 0.0
    theta: float = 0.0


ORIGEM = Pose()


def angulo_normalizado(angulo: float) -> float:
    return (angulo + math.pi) % (2.0 * math.pi) - math.pi


def compor(a: Pose, b: Pose) -> Pose:
    """a o b: aplica b e depois a."""
    c, s = math.cos(a.theta), math.sin(a.theta)
    return Pose(a.x + c * b.x - s * b.y, a.y + s * b.x + c * b.y, angulo_normalizado(a.theta + b.theta))


def inverter(pose: Pose) -> Pose:
    c, s = math.cos(pose.theta), math.sin(pose.theta)
    return Pose(-(c * pose.x + s * pose.y), -(-s * pose.x + c * pose.y), angulo_normalizado(-pose.theta))


def transformar(pontos: np.ndarray, pose: Pose) -> np.ndarray:
    c, s = math.cos(pose.theta), math.sin(pose.theta)
    rotacao = np.array([[c, -s], [s, c]])
    return pontos @ rotacao.T + np.array([pose.x, pose.y])


class ResultadoIcp(NamedTuple):
    pose: Pose
    erro_m: float        # distancia media entre os pares casados
    casados: float       # fracao dos pontos da varredura nova que acharam par
    ok: bool


VIZINHO_MAX_M = 0.15  # pontos vizinhos mais afastados que isso nao estao na mesma superficie


def normais(pontos: np.ndarray) -> np.ndarray:
    """Normal de cada ponto de uma varredura ordenada por angulo (NaN onde nao da para saber).

    Usa os vizinhos da mesma superficie (antes e depois na varredura). Um ponto isolado,
    numa quina ou numa borda de objeto, fica sem normal e nao entra no casamento.
    """
    n = len(pontos)
    saida = np.full((n, 2), np.nan)
    if n < 3:
        return saida
    antes = np.vstack([pontos[:1], pontos[:-1]])
    depois = np.vstack([pontos[1:], pontos[-1:]])
    perto_antes = np.hypot(*(pontos - antes).T) < VIZINHO_MAX_M
    perto_depois = np.hypot(*(depois - pontos).T) < VIZINHO_MAX_M
    perto_antes[0] = False
    perto_depois[-1] = False
    a = np.where(perto_antes[:, None], antes, pontos)
    b = np.where(perto_depois[:, None], depois, pontos)
    tangente = b - a
    comprimento = np.hypot(tangente[:, 0], tangente[:, 1])
    validos = (perto_antes | perto_depois) & (comprimento > 1e-6)
    saida[validos, 0] = -tangente[validos, 1] / comprimento[validos]
    saida[validos, 1] = tangente[validos, 0] / comprimento[validos]
    return saida


def vizinhos(pontos: np.ndarray, alvo: np.ndarray, norma_alvo: Optional[np.ndarray] = None):
    """Indice e distancia do ponto do alvo mais proximo de cada ponto.

    |a - b|^2 = |a|^2 + |b|^2 - 2 a.b: o produto de matrizes evita montar o array
    N x M x 2 e e varias vezes mais rapido.
    """
    if norma_alvo is None:
        norma_alvo = (alvo ** 2).sum(axis=1)
    d2 = (pontos ** 2).sum(axis=1)[:, None] + norma_alvo[None, :] - 2.0 * (pontos @ alvo.T)
    indice = d2.argmin(axis=1)
    return indice, np.sqrt(np.maximum(d2[np.arange(len(pontos)), indice], 0.0))


VOLTAS_PONTO_A_PONTO = 10        # aproximacao robusta antes do refinamento
PASSO_MAX_M = 0.10               # limite por volta do refinamento (evita extrapolar)
PASSO_MAX_RAD = math.radians(4)
PESO_LATERAL = 0.3               # forca da restricao "tracao diferencial nao anda de lado" (ajustada em simulacao)


class Restricao(NamedTuple):
    """O AGV tem tracao diferencial: o eixo das rodas so anda para a frente ou para tras.

    previo: pose do Kinect no quadro anterior (no referencial do alvo).
    avanco_m: distancia do eixo das rodas ate o Kinect, para a frente. Girando no lugar,
    o Kinect descreve um arco em torno do eixo; quem nao anda de lado e o eixo.
    """
    previo: Pose
    avanco_m: float = 0.0


def _eixo(pose: Pose, avanco_m: float) -> np.ndarray:
    return np.array([pose.x - avanco_m * math.cos(pose.theta), pose.y - avanco_m * math.sin(pose.theta)])


def _lateral(pose: Pose, restricao: Restricao) -> tuple[float, np.ndarray]:
    """Deslocamento lateral do eixo desde o quadro anterior e a direcao lateral usada."""
    previo = restricao.previo
    meio = previo.theta + angulo_normalizado(pose.theta - previo.theta) / 2.0
    direcao = np.array([-math.sin(meio), math.cos(meio)])
    deslocamento = _eixo(pose, restricao.avanco_m) - _eixo(previo, restricao.avanco_m)
    return float(direcao @ deslocamento), direcao


def _sem_lateral(pose: Pose, restricao: Optional[Restricao]) -> Pose:
    if restricao is None:
        return pose
    lateral, direcao = _lateral(pose, restricao)
    return Pose(pose.x - lateral * direcao[0], pose.y - lateral * direcao[1], pose.theta)


def _passo_ponto_a_ponto(p: np.ndarray, q: np.ndarray) -> Pose:
    """Rotacao + translacao que melhor leva p sobre q (Kabsch em 2D, solucao exata)."""
    centro_p, centro_q = p.mean(axis=0), q.mean(axis=0)
    h = (p - centro_p).T @ (q - centro_q)
    theta = math.atan2(h[0, 1] - h[1, 0], h[0, 0] + h[1, 1])
    c, s = math.cos(theta), math.sin(theta)
    t = centro_q - np.array([[c, -s], [s, c]]) @ centro_p
    return Pose(float(t[0]), float(t[1]), theta)


def _passo_ponto_a_reta(p: np.ndarray, q: np.ndarray, n: np.ndarray, pose: Pose,
                        restricao: Optional[Restricao]) -> Pose:
    """Passo pequeno (dx, dy, dtheta), aplicado como passo o pose, que minimiza a
    distancia de p ate a reta de q (normal n). Linearizado:
    residuo = n . (p + dtheta * (-py, px) + (dx, dy) - q).
    Com restricao, soma uma linha que pede deslocamento lateral do eixo igual a zero.
    """
    jacobiano = np.column_stack([n[:, 0], n[:, 1], n[:, 1] * p[:, 0] - n[:, 0] * p[:, 1]])
    residuo = ((p - q) * n).sum(axis=1)
    if restricao is not None:
        lateral, lado = _lateral(pose, restricao)
        frente = np.array([-math.sin(pose.theta), math.cos(pose.theta)])
        giro = lado @ np.array([-pose.y, pose.x]) - restricao.avanco_m * (lado @ frente)
        peso = PESO_LATERAL * math.sqrt(len(p))
        jacobiano = np.vstack([jacobiano, peso * np.array([lado[0], lado[1], giro])])
        residuo = np.append(residuo, peso * lateral)
    (dx, dy, dtheta), *_ = np.linalg.lstsq(jacobiano, -residuo, rcond=None)
    escala = min(1.0, PASSO_MAX_M / max(math.hypot(dx, dy), 1e-12), PASSO_MAX_RAD / max(abs(dtheta), 1e-12))
    return Pose(float(dx) * escala, float(dy) * escala, float(dtheta) * escala)


def icp(origem: np.ndarray, alvo: np.ndarray, chute: Pose = ORIGEM, iteracoes: int = 30,
        dist_max_m: float = 0.30, minimo_pares: int = 12, restricao: Optional[Restricao] = None,
        aproximacao: int = VOLTAS_PONTO_A_PONTO) -> ResultadoIcp:
    """Pose que leva a varredura `origem` para cima da varredura `alvo`.

    Cada volta casa cada ponto com o vizinho mais proximo do alvo. As primeiras voltas
    usam o casamento ponto a ponto, que converge mesmo partindo longe da resposta. As
    seguintes refinam ponto a reta: minimizam a distancia do ponto ate a reta da
    superficie do alvo. Isso corrige o vies do ponto a ponto, que encurta o deslocamento
    porque duas varreduras nunca amostram os mesmos lugares da parede. O limite de
    distancia dos pares aperta a cada volta.

    Com `restricao`, o deslocamento lateral do eixo e retirado ao fim da aproximacao e
    penalizado no refinamento: diante de uma parede lisa, a posicao ao longo dela nao
    pode ser vista, e sem isso o ICP escorrega.
    """
    pose = Pose(*chute)
    if len(origem) < minimo_pares or len(alvo) < minimo_pares:
        return ResultadoIcp(pose, math.inf, 0.0, False)

    normal_alvo = normais(alvo)
    tem_normal = np.isfinite(normal_alvo[:, 0])
    norma_alvo = (alvo ** 2).sum(axis=1)
    menor = min(len(origem), len(alvo))
    limite = dist_max_m
    erro, fracao = math.inf, 0.0
    for volta in range(iteracoes):
        refinando = volta >= aproximacao
        if volta == aproximacao:
            pose = _sem_lateral(pose, restricao)
        movidos = transformar(origem, pose)
        vizinho, distancia = vizinhos(movidos, alvo, norma_alvo)
        pares = distancia < limite
        if refinando:
            pares &= tem_normal[vizinho]
        fracao = min(1.0, float(pares.sum()) / menor)
        if pares.sum() < minimo_pares:
            return ResultadoIcp(pose, math.inf, fracao, False)

        p, q = movidos[pares], alvo[vizinho[pares]]
        if refinando:
            n = normal_alvo[vizinho[pares]]
            passo = _passo_ponto_a_reta(p, q, n, pose, restricao)
            erro = float(np.abs(((p - q) * n).sum(axis=1)).mean())
        else:
            passo = _passo_ponto_a_ponto(p, q)
            erro = float(distancia[pares].mean())
        pose = compor(passo, pose)
        limite = max(0.08, limite * 0.85)
        if refinando and abs(passo.theta) < 1e-4 and math.hypot(passo.x, passo.y) < 1e-4:
            break
    return ResultadoIcp(pose, erro, fracao, True)


PARTIDAS_RAD = tuple(math.radians(g) for g in (0, -6, 6, -12, 12, -18, 18))
CASADO_PERTO_M = 0.05


def _casados_perto(origem: np.ndarray, alvo: np.ndarray, pose: Pose) -> int:
    _, distancia = vizinhos(transformar(origem, pose), alvo)
    return int((distancia < CASADO_PERTO_M).sum())


def icp_robusto(origem: np.ndarray, alvo: np.ndarray, chute: Pose = ORIGEM,
                restricao: Optional[Restricao] = None) -> ResultadoIcp:
    """ICP partindo de varias rotacoes em torno do chute.

    Ao girar, os pontos distantes se deslocam muito para o lado; partindo so do chute,
    o ICP pode deslizar ao longo de uma parede e parar numa resposta errada. Cada
    partida roda poucas voltas, fica a que deixa mais pontos a menos de 5 cm do alvo,
    e essa e refinada ate o fim.
    """
    menor = min(len(origem), len(alvo))
    melhor, melhor_casados = None, -1
    for desvio in PARTIDAS_RAD:
        partida = Pose(chute.x, chute.y, angulo_normalizado(chute.theta + desvio))
        previa = icp(origem, alvo, partida, iteracoes=VOLTAS_PONTO_A_PONTO)
        if not previa.ok:
            continue
        candidata = _sem_lateral(previa.pose, restricao)
        casados = _casados_perto(origem, alvo, candidata)
        if casados > melhor_casados:
            melhor, melhor_casados = candidata, casados
        if desvio == 0 and casados >= 0.8 * menor:
            break  # o chute ja estava bom: nao gasta tempo com as outras partidas
    if melhor is None:
        return ResultadoIcp(Pose(*chute), math.inf, 0.0, False)
    # A aproximacao ja foi feita na partida escolhida: daqui em diante so refina.
    return icp(origem, alvo, melhor, iteracoes=20, restricao=restricao, aproximacao=0)
