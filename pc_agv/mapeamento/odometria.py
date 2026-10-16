"""Odometria visual: estima o movimento do AGV casando varreduras do Kinect.

O AGV nao tem encoders, entao a posicao sai da propria visao: cada varredura nova
e casada (ICP) com a varredura-chave mais recente, e nao com a anterior, para o AGV
parado nao acumular erro. Quando ele anda ou gira o bastante, ou a vista muda muito,
a varredura atual vira a nova chave.

Se o casamento falha com o AGV em movimento (ex.: um objeto colado no Kinect tapa a
vista), a pose segue pelo ultimo movimento medido (velocidade constante) ate a vista
voltar; parado, ela fica onde esta.
"""

import math
from typing import Optional

import numpy as np

from pc_agv.config import KINECT_AVANCO_M
from pc_agv.mapeamento.geometria import ORIGEM, Pose, Restricao, ResultadoIcp, compor, icp_robusto, inverter


MINIMO_PONTOS = 20
NOVA_CHAVE_M = 0.20
NOVA_CHAVE_RAD = math.radians(10)
ERRO_MAX_M = 0.04          # distancia media ponto-parede acima disso: casamento descartado
CASADOS_MIN = 0.50         # pelo menos metade dos pontos precisa achar par
# Entre dois quadros (4 por segundo) o AGV nao anda nem gira mais que isso: um salto
# maior e casamento errado (ex.: deslizou ao longo de uma parede lisa).
SALTO_MAX_M = 0.30
SALTO_MAX_RAD = math.radians(30)
FALHAS_PARA_RECOMECAR = 8  # depois disso a chave e trocada (ex.: alguem passou na frente)
SOBREPOSICAO_MIN = 0.70    # abaixo disso a vista mudou bastante: a varredura vira a nova chave


class Odometria:
    def __init__(self, pose: Pose = ORIGEM, avanco_m: float = KINECT_AVANCO_M):
        self.avanco_m = avanco_m
        self.pose = Pose(*pose)
        self.pose_chave = Pose(*pose)
        self.chave: Optional[np.ndarray] = None
        self.relativo = Pose()
        self.falhas_seguidas = 0
        self.distancia_m = 0.0
        self.ultimo: Optional[ResultadoIcp] = None
        self.incremento = Pose()   # movimento por quadro no ultimo casamento bom
        self.prevendo = False      # a pose atual veio da previsao, nao da visao

    def _nova_chave(self, pontos: np.ndarray) -> None:
        self.chave = pontos
        self.pose_chave = self.pose
        self.relativo = Pose()

    def _prever(self, em_movimento: bool) -> None:
        """Sem casamento: avanca a pose pelo ultimo movimento medido, se o AGV esta andando."""
        if not em_movimento or self.chave is None:
            return
        self.relativo = compor(self.relativo, self.incremento)
        self.pose = compor(self.pose_chave, self.relativo)
        self.prevendo = True

    def atualizar(self, pontos: np.ndarray, em_movimento: bool = True) -> bool:
        """Processa uma varredura. Devolve True se a pose foi atualizada com confianca.

        em_movimento: o AGV esta recebendo comando para andar/girar (usado so quando o
        casamento falha, para decidir entre prever o movimento ou ficar parado).
        """
        if len(pontos) < MINIMO_PONTOS:
            self.ultimo = None
            self._prever(em_movimento)
            return False
        if self.chave is None:
            self._nova_chave(pontos)
            return True

        resultado = icp_robusto(pontos, self.chave, chute=self.relativo,
                                restricao=Restricao(self.relativo, self.avanco_m))
        self.ultimo = resultado
        salto = compor(inverter(self.relativo), resultado.pose)
        confiavel = (
            resultado.ok
            and resultado.erro_m <= ERRO_MAX_M
            and resultado.casados >= CASADOS_MIN
            and math.hypot(salto.x, salto.y) <= SALTO_MAX_M
            and abs(salto.theta) <= SALTO_MAX_RAD
        )
        if not confiavel:
            self.falhas_seguidas += 1
            self._prever(em_movimento)
            if self.falhas_seguidas >= FALHAS_PARA_RECOMECAR:
                # Perdeu a referencia: recomeca da pose atual (aceita um pouco de deriva).
                self._nova_chave(pontos)
                self.falhas_seguidas = 0
            return False

        self.falhas_seguidas = 0
        self.prevendo = False
        self.incremento = salto
        nova = compor(self.pose_chave, resultado.pose)
        self.distancia_m += math.hypot(nova.x - self.pose.x, nova.y - self.pose.y)
        self.pose = nova
        self.relativo = resultado.pose
        if (
            math.hypot(resultado.pose.x, resultado.pose.y) > NOVA_CHAVE_M
            or abs(resultado.pose.theta) > NOVA_CHAVE_RAD
            or resultado.casados < SOBREPOSICAO_MIN
        ):
            self._nova_chave(pontos)
        return True
