"""AGV virtual em malha fechada (tests/simulador_agv.py): o codigo real do autonomo e do
mapa dirigindo um AGV simulado, com o Kinect, o RPLIDAR e o firmware modelados."""

import math
import types
import unittest
from unittest.mock import patch

from apoio import estado_isolado, mapeamento_isolado

import simulador_agv as sim
from simulador_agv import Pose, Simulacao, armazem, beco, colide

from pc_agv import estado
from pc_agv.hardware import rplidar
from pc_agv.mapeamento import servico as mapa
from pc_agv.mapeamento.geometria import angulo_normalizado, compor, inverter


def _rodar(segmentos, pose, segundos, **opcoes):
    with estado_isolado():
        estado.lidar_state.update(ok=False, pontos=None, folgas={}, voltas=0)
        return Simulacao(segmentos, pose=pose, **opcoes).rodar(segundos)


class TestSimulador(unittest.TestCase):
    def test_colisao_do_retangulo(self):
        parede = armazem()
        self.assertFalse(colide(Pose(0.0, 0.0, 0.0), parede))
        self.assertTrue(colide(Pose(-0.8, 0.0, 0.0), parede))           # traseira na parede x = -1
        self.assertTrue(colide(Pose(1.2, 1.15, 0.0), parede))           # frente dentro da prateleira

    def test_firmware_zona_morta_e_curva(self):
        firmware = sim.Firmware()
        for _ in range(3):
            firmware.comando(200, 20, 0.0)                             # |direcao| <= 30: reto
        self.assertEqual(firmware.potencias(0.1), (200, 200))
        for _ in range(3):
            firmware.comando(200, 254, 0.0)                            # direita fechada: gira no lugar
        esquerda, direita = firmware.potencias(0.1)
        self.assertGreater(esquerda, 0)
        self.assertLess(direita, 0)
        self.assertEqual(firmware.potencias(1.0), (0, 0))              # timeout de 500 ms


class TestAutonomoSimulado(unittest.TestCase):
    def test_armazem_sem_colisoes_com_kinect_e_lidar(self):
        r = _rodar(armazem(), Pose(0.0, 0.0, 0.0), 60.0, semente=1)
        self.assertEqual(r.colisoes, 0, r.eventos)
        self.assertGreater(r.distancia_m, 8.0)

    def test_sai_do_beco_dando_re(self):
        r = _rodar(beco(), Pose(0.6, 0.0, 0.0), 30.0, semente=1)
        self.assertEqual(r.colisoes, 0, r.eventos)
        self.assertGreater(r.res_s, 3.0)
        self.assertIn("recuando", r.motivos)

    def test_caixa_na_zona_cega_do_kinect(self):
        # A caixa esta a 0,25 m do Kinect (abaixo do alcance dele); o LIDAR a ve.
        r = _rodar(armazem(), Pose(1.2, 2.25, math.pi), 10.0, semente=1)
        self.assertEqual(r.colisoes, 0, r.eventos)


class TestMapaSimulado(unittest.TestCase):
    def _mapear(self, segundos: float, com_lidar: bool = True):
        with estado_isolado(), mapeamento_isolado(tamanho_m=20.0):
            estado.lidar_state.update(ok=False, pontos=None, folgas={}, voltas=0)
            mapa._avanco_m = sim.AVANCO_KINECT_M
            mapa._odometria.avanco_m = sim.AVANCO_KINECT_M
            simulacao = Simulacao(armazem(), pose=Pose(0.0, 0.0, 0.0), semente=1)
            inicio = simulacao.pose_kinect()
            mapa.ligar()
            proximo = [0.0]

            def a_cada(agora):
                if agora >= proximo[0]:
                    proximo[0] = agora + 0.25
                    mapa.passo(agora)

            sem_lidar = types.SimpleNamespace(leitura_recente=lambda *a: None, MONTAGEM=rplidar.MONTAGEM)
            with patch.object(mapa, "rplidar", sem_lidar if not com_lidar else rplidar):
                r = simulacao.rodar(segundos, a_cada=a_cada)
            real = compor(inverter(inicio), simulacao.pose_kinect())
            estimada = mapa._odometria.pose
            # o mapa le a ultima volta; a pose real anda ate 0,25 s depois dela
            erro = math.hypot(real.x - estimada.x, real.y - estimada.y)
            giro = abs(math.degrees(angulo_normalizado(real.theta - estimada.theta)))
            return r, erro, giro, mapa.status_detalhado()

    def test_mapa_com_lidar_dirigindo_pelo_autonomo(self):
        r, erro, giro, status = self._mapear(60.0)
        self.assertEqual(status["fonte"], "lidar")
        self.assertEqual(status["falhas"], 0)
        self.assertLess(erro, 0.02 * r.distancia_m + 0.10)     # menos de 2% do caminho (+ ultimo quadro)
        self.assertLess(giro, 20.0)
        self.assertGreater(status["area_m2"], 25.0)


if __name__ == "__main__":
    unittest.main()
