"""Camada de seguranca do modo IA (funcao pura).

A rede nunca fala direto com os motores: o comando dela passa por aqui, que limita
a velocidade, a re e o avanco perto de obstaculos, com os mesmos limiares do
modo autonomo.
"""

from typing import Optional

from pc_agv import util


PERIGO_M = 0.35
ALERTA_M = 0.60
VELOCIDADE_PERTO = 25  # % maximo a frente quando o obstaculo esta na zona de alerta
RE_MAX = 30            # nao ha sensor atras: a re da IA e sempre lenta
ZONA_MORTA = 6         # comandos menores que isso viram 0 (os motores nem arrancam)


def aplicar_seguranca(
    velocidade: int,
    direcao: int,
    frente_m: Optional[float],
    velocidade_max: int,
    motivo: str = "ia",
) -> tuple[int, int, str]:
    limite = util.clamp(velocidade_max, 0, 100)
    velocidade = util.clamp(velocidade, -min(RE_MAX, limite), limite)
    direcao = util.clamp(direcao, -100, 100)
    if abs(velocidade) < ZONA_MORTA:
        velocidade = 0
    if abs(direcao) < ZONA_MORTA:
        direcao = 0

    if frente_m is None:
        return 0, 0, "ia-sem-distancia"
    if frente_m < PERIGO_M and velocidade > 0:
        return 0, direcao, "ia-bloqueio-frente"
    if frente_m < ALERTA_M and velocidade > VELOCIDADE_PERTO:
        return VELOCIDADE_PERTO, direcao, "ia-devagar-perto"
    return velocidade, direcao, motivo
