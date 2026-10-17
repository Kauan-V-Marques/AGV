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
RE_MAX = 30            # a traseira so e vista pelo LIDAR: a re da IA e sempre lenta
ZONA_MORTA = 6         # comandos menores que isso viram 0 (os motores nem arrancam)


def aplicar_seguranca(
    velocidade: int,
    direcao: int,
    frente_m: Optional[float],
    velocidade_max: int,
    motivo: str = "ia",
    tras_m: Optional[float] = None,
    pode_girar: bool = True,
) -> tuple[int, int, str]:
    """frente_m: menor folga a frente (Kinect e LIDAR); tras_m: folga atras (so LIDAR,
    None quando ele nao esta disponivel); pode_girar: o LIDAR ve espaco para os cantos
    do chassi varrerem numa curva (sem LIDAR, True)."""
    limite = util.clamp(velocidade_max, 0, 100)
    velocidade = util.clamp(velocidade, -min(RE_MAX, limite), limite)
    direcao = util.clamp(direcao, -100, 100)
    if abs(velocidade) < ZONA_MORTA:
        velocidade = 0
    if abs(direcao) < ZONA_MORTA:
        direcao = 0

    if frente_m is None:
        return 0, 0, "ia-sem-distancia"
    if not pode_girar and direcao != 0:
        # Qualquer direcao acima de ~17% e curva fechada no firmware: a traseira varreria
        # o obstaculo do lado. Sem espaco, a IA so anda reto (frente e re conferidas abaixo).
        direcao, motivo = 0, "ia-sem-espaco-para-girar"
    if frente_m < PERIGO_M and velocidade > 0:
        return 0, direcao, "ia-bloqueio-frente"
    if tras_m is not None and tras_m < PERIGO_M and velocidade < 0:
        return 0, direcao, "ia-bloqueio-tras"
    if frente_m < ALERTA_M and velocidade > VELOCIDADE_PERTO:
        return VELOCIDADE_PERTO, direcao, "ia-devagar-perto"
    return velocidade, direcao, motivo
