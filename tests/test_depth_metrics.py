import unittest
from unittest.mock import patch

import numpy as np

from pc_agv import iniciar_sistema as sistema


def _raw_para_metros(raw: int) -> float:
    return 1.0 / (raw * -0.0030711016 + 3.3309495161)


class TestDepthSegmentDistance(unittest.TestCase):
    def test_raw_retorna_obstaculo_mais_proximo(self):
        # Obstaculo (raw 500 ~ 0,56 m) na frente de uma parede (raw 1000 ~ 3,85 m).
        janela = np.full((10, 10), 1000, dtype=np.uint16)
        janela[3:7, 3:7] = 500

        distancia = sistema._depth_segment_distance(janela, is_mm=False)

        self.assertAlmostEqual(distancia, _raw_para_metros(500), places=3)

    def test_fundo_alem_de_10m_nao_descarta_a_janela(self):
        # Fundo muito distante (raw 1080 ~ 70 m) nao pode apagar o obstaculo a ~0,67 m.
        janela = np.full((10, 10), 1080, dtype=np.uint16)
        janela[0:2, 0:2] = 600

        distancia = sistema._depth_segment_distance(janela, is_mm=False)

        self.assertAlmostEqual(distancia, _raw_para_metros(600), places=3)

    def test_raw_invalido_e_ignorado(self):
        janela = np.full((10, 10), 2047, dtype=np.uint16)

        self.assertIsNone(sistema._depth_segment_distance(janela, is_mm=False))

    def test_milimetros_retorna_obstaculo_mais_proximo(self):
        janela = np.full((10, 10), 3000, dtype=np.uint16)
        janela[5, 5] = 800

        self.assertAlmostEqual(sistema._depth_segment_distance(janela, is_mm=True), 0.8)


class TestDepthMetrics(unittest.TestCase):
    def test_obstaculo_no_centro_aparece_so_na_janela_central(self):
        quadro = np.full((480, 640), 1000, dtype=np.uint16)
        quadro[200:260, 300:340] = 500  # dentro da janela central (40-60% da largura)

        is_mm, distancia, esquerda, centro, direita = sistema._compute_depth_metrics(quadro)

        self.assertFalse(is_mm)
        self.assertAlmostEqual(centro, _raw_para_metros(500), places=3)
        self.assertAlmostEqual(distancia, centro)
        self.assertAlmostEqual(esquerda, _raw_para_metros(1000), places=3)
        self.assertAlmostEqual(direita, _raw_para_metros(1000), places=3)

    def test_autonomo_desvia_do_obstaculo_proximo(self):
        quadro = np.full((480, 640), 1000, dtype=np.uint16)
        quadro[200:260, 300:340] = 500
        _, distancia, esquerda, centro, direita = sistema._compute_depth_metrics(quadro)

        estado = {
            "kinect_ok": True,
            "distance_m": distancia,
            "left_clearance_m": esquerda,
            "center_clearance_m": centro,
            "right_clearance_m": direita,
        }
        with patch.dict(sistema._state, estado):
            speed, steering, reason = sistema._compute_autopilot_command()

        self.assertEqual(reason, "desvio-curto")
        self.assertEqual(speed, 18)
        self.assertNotEqual(steering, 0)


if __name__ == "__main__":
    unittest.main()
