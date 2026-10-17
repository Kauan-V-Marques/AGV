"""AGV virtual em malha fechada: Kinect + RPLIDAR simulados, firmware e cinematica.

Usa o codigo real do servidor (regras do autonomo, IA, camada de seguranca, mapa e a
conversao de comando para o Arduino) e simula so o que e fisico:

- Kinect v1: profundidade bruta de 11 bits (2047 = sem leitura), campo de 57 graus e
  zona cega abaixo de 0,5 m, como o sensor real;
- RPLIDAR: ~13 voltas/s, ~120 pontos por volta, ruido de 1 cm, angulo no sentido horario;
- firmware do Arduino (sketch_may13a): filtro de 3 amostras, zona morta de +-30 na
  direcao, potencia de cada roda, protecao anti-reversao e timeout de 500 ms;
- tracao diferencial: velocidade de cada roda proporcional ao PWM.

Tudo roda em tempo simulado (util.now e substituido), sem threads e sem hardware.
"""

import math
from contextlib import ExitStack
from dataclasses import dataclass, field
from typing import Callable, Optional
from unittest.mock import patch

import numpy as np

from simulador_kinect import caixa, quadro

from pc_agv import controle, estado, util
from pc_agv.hardware import arduino, rplidar
from pc_agv.mapeamento.geometria import Pose, compor, inverter
from pc_agv.visao import lidar as geometria
from pc_agv.visao import profundidade


# ── Parametros fisicos (estimativas; ajuste quando medir o AGV) ─────
VMAX_M_S = 0.6          # velocidade de uma roda com PWM 255
BITOLA_M = 0.45         # distancia entre as rodas
CORPO = geometria.Corpo(frente_m=0.30, tras_m=0.30, largura_m=0.50)
AVANCO_KINECT_M = 0.25  # Kinect a frente do eixo das rodas
LIDAR_XY = (0.0, 0.0)   # LIDAR sobre o eixo das rodas
PONTOS_POR_VOLTA = 120
LIDAR_HZ = 13.0
KINECT_HZ = 10.0        # quadros processados por segundo (o real entrega 30)
DT = 0.02


# ── Cenarios ────────────────────────────────────────────────────────
def armazem() -> np.ndarray:
    """Sala 8 x 6 m com prateleiras e caixas: corredores de ~1 m entre elas."""
    seg = caixa(-1.0, -3.0, 7.0, 3.0)
    seg += caixa(1.5, 0.9, 4.5, 1.4)        # prateleira de cima
    seg += caixa(1.5, -1.4, 4.5, -0.9)      # prateleira de baixo
    seg += caixa(5.6, -0.4, 6.1, 0.4)       # palete no fundo
    seg += caixa(0.2, 2.0, 0.7, 2.5)        # caixas soltas
    seg += caixa(2.6, -2.5, 3.0, -2.1)
    return np.array(seg, dtype=float)


def beco() -> np.ndarray:
    """Corredor sem saida de 0,9 m de largura e 2 m de fundo, aberto para tras."""
    seg = [((-3.0, 0.45), (1.5, 0.45)), ((-3.0, -0.45), (1.5, -0.45)), ((1.5, -0.45), (1.5, 0.45))]
    seg += caixa(-6.0, -3.0, 3.0, 3.0)
    return np.array(seg, dtype=float)


# ── Sensores ────────────────────────────────────────────────────────
def raios(origem: np.ndarray, angulos: np.ndarray, segmentos: np.ndarray) -> np.ndarray:
    """Distancia ate a parede mais proxima em cada direcao (inf se nao bate)."""
    direcao = np.stack([np.cos(angulos), np.sin(angulos)], axis=1)
    a, b = segmentos[:, 0, :], segmentos[:, 1, :]
    e = b - a
    det = direcao[:, None, 0] * (-e[None, :, 1]) - direcao[:, None, 1] * (-e[None, :, 0])
    rx, ry = a[None, :, 0] - origem[0], a[None, :, 1] - origem[1]
    with np.errstate(divide="ignore", invalid="ignore"):
        t = (rx * (-e[None, :, 1]) - ry * (-e[None, :, 0])) / det
        s = (direcao[:, None, 0] * ry - direcao[:, None, 1] * rx) / det
    return np.where((t > 0) & (s >= 0) & (s <= 1), t, np.inf).min(axis=1)


def kinect_bruto(pose_kinect: Pose, segmentos: np.ndarray, rng) -> np.ndarray:
    """Quadro 240 x 320 no formato de 11 bits do freenect."""
    mm = quadro(pose_kinect, segmentos, ruido=True, rng=rng, largura=320, altura=240).astype(np.float64)
    z = mm / 1000.0
    with np.errstate(divide="ignore"):
        bruto = (1.0 / z - 3.3309495161) / -0.0030711016
    sem_leitura = (z < 0.5) | (z > 6.0) | ~np.isfinite(bruto)
    return np.where(sem_leitura, 2047, np.clip(bruto, 0, 2046)).astype(np.uint16)


def lidar_volta(pose_lidar: Pose, segmentos: np.ndarray, rng) -> tuple[np.ndarray, np.ndarray]:
    """Angulos do LIDAR (graus, horario a partir da frente) e distancias (m; 0 = sem eco)."""
    inicio = rng.uniform(0.0, 360.0 / PONTOS_POR_VOLTA)
    angulos = (inicio + np.arange(PONTOS_POR_VOLTA) * 360.0 / PONTOS_POR_VOLTA) % 360.0
    distancia = raios(np.array([pose_lidar.x, pose_lidar.y]), pose_lidar.theta - np.radians(angulos), segmentos)
    distancia = distancia + rng.normal(0.0, 0.01, distancia.shape)
    return angulos, np.where(np.isfinite(distancia) & (distancia < 12.0), distancia, 0.0)


# ── Firmware e cinematica ───────────────────────────────────────────
class Firmware:
    """Modelo do sketch_may13a: devolve a potencia com sinal de cada roda (esquerda, direita)."""

    def __init__(self):
        self.historico = [(0, 0)] * 3
        self.indice = 0
        self.rodas = (0, 0)
        self.ultimo = -10.0

    def comando(self, aceleracao: int, direcao: int, agora: float) -> None:
        aceleracao = max(-255, min(255, aceleracao))
        direcao = max(-254, min(254, direcao))
        self.historico[self.indice] = (aceleracao, direcao)
        self.indice = (self.indice + 1) % 3
        # media inteira como em C (trunca para zero)
        aceleracao = int(sum(h[0] for h in self.historico) / 3)
        direcao = int(sum(h[1] for h in self.historico) / 3)
        magnitude, re = abs(aceleracao), aceleracao < 0
        sinal = -1 if re else 1
        if -30 <= direcao <= 30:
            externa = interna = magnitude * sinal
            esquerda, direita = externa, interna
        else:
            fator = (abs(direcao) - 30) / (254 - 30)
            externa = int(magnitude * (0.9 + 0.1 * fator)) * sinal
            interna = -int(magnitude * fator) * sinal   # roda de dentro gira ao contrario
            # direita (+): a roda esquerda e a externa
            esquerda, direita = (externa, interna) if direcao > 30 else (interna, externa)
        # anti-reversao: nao inverte uma roda que esta rapida
        atual = self.rodas
        novas = []
        for nova, velha in ((esquerda, atual[0]), (direita, atual[1])):
            novas.append(0 if nova * velha < 0 and abs(velha) > 20 else nova)
        self.rodas = (novas[0], novas[1])
        self.ultimo = agora

    def potencias(self, agora: float) -> tuple[int, int]:
        if agora - self.ultimo > 0.5:  # timeout do firmware
            self.rodas = (0, 0)
        return self.rodas


def colide(pose: Pose, segmentos: np.ndarray, corpo=CORPO) -> bool:
    """O retangulo do AGV encosta em alguma parede? (Liang-Barsky no referencial do AGV)"""
    inv = inverter(pose)
    c, s = math.cos(inv.theta), math.sin(inv.theta)
    pontos = segmentos.reshape(-1, 2) @ np.array([[c, -s], [s, c]]).T + np.array([inv.x, inv.y])
    a, b = pontos[0::2], pontos[1::2]
    xmin, xmax, ymin, ymax = -corpo.tras_m, corpo.frente_m, -corpo.largura_m / 2, corpo.largura_m / 2
    d = b - a
    t0, t1 = np.zeros(len(a)), np.ones(len(a))
    ok = np.ones(len(a), bool)
    for p, q in ((-d[:, 0], a[:, 0] - xmin), (d[:, 0], xmax - a[:, 0]), (-d[:, 1], a[:, 1] - ymin), (d[:, 1], ymax - a[:, 1])):
        paralelo = np.abs(p) < 1e-12
        ok &= ~(paralelo & (q < 0))
        with np.errstate(divide="ignore", invalid="ignore"):
            r = q / p
        t0 = np.where(~paralelo & (p < 0), np.maximum(t0, r), t0)
        t1 = np.where(~paralelo & (p > 0), np.minimum(t1, r), t1)
    return bool((ok & (t0 <= t1)).any())


def menor_folga(pose: Pose, segmentos: np.ndarray) -> float:
    """Menor distancia entre a borda do AGV e uma parede (aproximada por raios)."""
    pontos, _ = geometria.para_agv(np.arange(0, 360, 1.0), raios(np.array([pose.x, pose.y]),
                                   pose.theta - np.radians(np.arange(0, 360, 1.0)), segmentos),
                                   geometria.Montagem(min_m=0.0, max_m=50.0))
    folgas = [v for v in geometria.folgas(pontos, CORPO).values() if v is not None]
    return min(folgas) if folgas else math.inf


# ── Simulacao ───────────────────────────────────────────────────────
@dataclass
class Resultado:
    tempo_s: float = 0.0
    distancia_m: float = 0.0
    colisoes: int = 0
    menor_folga_m: float = math.inf
    res_s: float = 0.0                       # tempo dando re
    parado_s: float = 0.0
    motivos: dict = field(default_factory=dict)
    trajeto: list = field(default_factory=list)
    eventos: list = field(default_factory=list)


@dataclass
class Simulacao:
    segmentos: np.ndarray
    pose: Pose = Pose(0.0, 0.0, 0.0)          # eixo das rodas, no mundo
    com_lidar: bool = True
    com_kinect: bool = True
    semente: int = 0
    firmware: Firmware = field(default_factory=Firmware)
    agora: float = 1000.0

    def __post_init__(self):
        self.rng = np.random.default_rng(self.semente)
        self.prox = {"kinect": 0.0, "lidar": 0.0, "auto": 0.0, "controle": 0.0}
        self.alvo = (0, 0, "inicio")

    def pose_kinect(self) -> Pose:
        return compor(self.pose, Pose(AVANCO_KINECT_M, 0.0, 0.0))

    def pose_lidar(self) -> Pose:
        return compor(self.pose, Pose(LIDAR_XY[0], LIDAR_XY[1], 0.0))

    def contexto(self) -> ExitStack:
        pilha = ExitStack()
        pilha.enter_context(patch.object(util, "now", lambda: self.agora))
        pilha.enter_context(patch.object(rplidar, "MONTAGEM", geometria.Montagem(x_m=LIDAR_XY[0], y_m=LIDAR_XY[1])))
        pilha.enter_context(patch.object(rplidar, "CORPO", CORPO))
        return pilha

    def sensores(self) -> None:
        if self.com_kinect and self.agora >= self.prox["kinect"]:
            self.prox["kinect"] = self.agora + 1.0 / KINECT_HZ
            bruto = kinect_bruto(self.pose_kinect(), self.segmentos, self.rng)
            is_mm, dist, esq, cen, dire = profundidade.compute_depth_metrics(bruto)
            with estado.state_lock:
                estado.state.update(kinect_ok=True, depth=bruto, depth_mm=is_mm, depth_at=self.agora,
                                    distance_m=dist, left_clearance_m=esq, center_clearance_m=cen,
                                    right_clearance_m=dire, last_frame_at=self.agora)
        if self.com_lidar and self.agora >= self.prox["lidar"]:
            self.prox["lidar"] = self.agora + 1.0 / LIDAR_HZ
            angulos, distancias = lidar_volta(self.pose_lidar(), self.segmentos, self.rng)
            validos = distancias > 0
            rplidar.publicar(list(angulos[validos]), list(distancias[validos]), self.agora, LIDAR_HZ)

    def rodar(self, segundos: float, decidir: Optional[Callable[[], tuple[int, int, str]]] = None,
              a_cada: Optional[Callable[[float], None]] = None) -> Resultado:
        """Roda o laco. decidir: quem dirige (padrao: as regras do autonomo)."""
        decidir = decidir or controle.compute_autopilot_command
        r = Resultado()
        fim = self.agora + segundos
        with self.contexto():
            with estado.state_lock:
                estado.state["kinect_ok"] = self.com_kinect
            while self.agora < fim:
                self.sensores()
                if self.agora >= self.prox["auto"]:
                    self.prox["auto"] = self.agora + controle.AUTO_LOOP_SECONDS
                    self.alvo = decidir()
                    r.motivos[self.alvo[2]] = r.motivos.get(self.alvo[2], 0) + 1
                if self.agora >= self.prox["controle"]:
                    self.prox["controle"] = self.agora + controle.CONTROL_LOOP_SECONDS
                    velocidade, direcao, _ = self.alvo
                    with estado.control_lock:
                        estado.control_state.update(active_speed=velocidade, active_steering=direcao)
                    self.firmware.comando(*arduino.speed_steering_to_payload(velocidade, direcao), self.agora)
                if a_cada is not None:
                    a_cada(self.agora)

                esquerda, direita = self.firmware.potencias(self.agora)
                v_e, v_d = esquerda / 255 * VMAX_M_S, direita / 255 * VMAX_M_S
                v, w = (v_e + v_d) / 2, (v_d - v_e) / BITOLA_M
                nova = compor(self.pose, Pose(v * DT, 0.0, w * DT))
                if colide(nova, self.segmentos):
                    if not r.eventos or self.agora - r.eventos[-1][0] > 1.0:
                        r.colisoes += 1
                        r.eventos.append((self.agora, (round(self.pose.x, 2), round(self.pose.y, 2)), self.alvo[2]))
                else:
                    r.distancia_m += abs(v) * DT
                    self.pose = nova
                if v < -1e-3:
                    r.res_s += DT
                if abs(v) < 1e-3 and abs(w) < 1e-3:
                    r.parado_s += DT
                if int(self.agora / DT) % 10 == 0:
                    r.trajeto.append((self.pose.x, self.pose.y))
                    r.menor_folga_m = min(r.menor_folga_m, menor_folga(self.pose, self.segmentos))
                self.agora += DT
                r.tempo_s += DT
        return r
