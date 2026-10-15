import unittest
from unittest.mock import patch

import cv2
import numpy as np
from apoio import estado_isolado

from pc_agv import estado, runtime, telemetria, util
from pc_agv.web import mjpeg


def _decodificar(parte: bytes) -> np.ndarray:
    jpeg = parte.split(b"\r\n\r\n", 1)[1].rstrip(b"\r\n")
    return cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)


class TestTelemetria(unittest.TestCase):
    def test_idade_do_quadro_e_secoes(self):
        with estado_isolado(), \
                patch("pc_agv.hardware.arduino.status_snapshot", return_value={"connected": True}), \
                patch("pc_agv.visao.rostos.face_status_snapshot", return_value={"known": False}), \
                patch("pc_agv.hardware.kinect.get_kinect_stabilization", return_value=True):
            estado.state.update({"last_frame_at": util.now() - 1.5, "zed_last_frame_at": 0.0,
                                 "distance_m": 0.8, "zed_ok": True})
            estado.settings["speed_limit_pct"] = 70
            dados = telemetria.status_snapshot()
        self.assertAlmostEqual(dados["state"]["frame_age_s"], 1.5, delta=0.2)
        self.assertEqual(dados["state"]["distance_m"], 0.8)
        self.assertIsNone(dados["zed"]["frame_age_s"])
        self.assertTrue(dados["zed"]["online"])
        self.assertEqual(dados["arduino"], {"connected": True})
        self.assertEqual(dados["face"], {"known": False})
        self.assertTrue(dados["kinect"]["stabilization_enabled"])
        self.assertEqual(dados["settings"]["speed_limit_pct"], 70)

    def test_sem_quadro_ainda(self):
        with estado_isolado():
            estado.state["last_frame_at"] = 0.0
            self.assertIsNone(telemetria.status_snapshot()["state"]["frame_age_s"])


class TestMjpeg(unittest.TestCase):
    def _primeiro_quadro(self, tipo, **estado_inicial):
        with estado_isolado():
            estado.state.update(estado_inicial)
            return _decodificar(next(mjpeg.mjpeg_generator(tipo, fps=10)))

    def test_sem_camera_mostra_quadro_de_espera(self):
        for tipo, chave in (("rgb", "rgb"), ("depth", "depth"), ("zed", "zed")):
            with self.subTest(tipo=tipo):
                quadro = self._primeiro_quadro(tipo, **{chave: None, "kinect_error": "sem kinect",
                                                        "zed_error": "sem zed"})
                self.assertEqual(quadro.shape, (480, 640, 3))

    def test_rgb_ao_vivo(self):
        quadro = self._primeiro_quadro("rgb", rgb=np.full((120, 160, 3), 200, np.uint8))
        self.assertEqual(quadro.shape, (120, 160, 3))

    def test_profundidade_vira_mapa_colorido(self):
        profundidade = np.full((120, 160), 600, np.uint16)
        quadro = self._primeiro_quadro("depth", depth=profundidade, depth_mm=False)
        self.assertEqual(quadro.shape, (120, 160, 3))

    def test_zed_larga_e_reduzida(self):
        with patch.object(mjpeg, "ZED_STREAM_MAX_WIDTH", 320):
            quadro = self._primeiro_quadro("zed", zed=np.zeros((360, 640, 3), np.uint8))
        self.assertEqual(quadro.shape, (180, 320, 3))


class TestRuntime(unittest.TestCase):
    def tearDown(self):
        runtime.stop_runtime()
        estado.stop_event.clear()

    def test_sobe_as_seis_threads_uma_vez_e_encerra(self):
        chamadas = []

        def alvo(nome):
            def _rodar():
                chamadas.append(nome)
            return _rodar

        with patch("pc_agv.hardware.kinect.capture_loop", alvo("kinect")), \
                patch("pc_agv.hardware.zed.capture_loop", alvo("zed")), \
                patch("pc_agv.visao.rostos.face_worker_loop", alvo("rostos")), \
                patch("pc_agv.controle.autopilot_loop", alvo("autopiloto")), \
                patch("pc_agv.controle.control_loop", alvo("controle")), \
                patch("pc_agv.aprendizado.servico.aprendizado_loop", alvo("aprendizado")), \
                patch("pc_agv.aprendizado.servico.iniciar") as abrir_banco, \
                patch("pc_agv.aprendizado.servico.encerrar") as fechar_banco, \
                patch("pc_agv.visao.rostos.load_known_faces") as carregar, \
                patch("pc_agv.hardware.arduino.close") as fechar:
            runtime.start_runtime()
            runtime.start_runtime()  # segunda chamada nao duplica as threads
            nomes = sorted(t.name for t in runtime._threads)
            runtime.stop_runtime()

        self.assertEqual(
            nomes, ["agv-aprendizado", "agv-autopilot", "agv-control", "agv-kinect", "agv-zed", "agv-zed-face"],
        )
        self.assertEqual(sorted(chamadas), ["aprendizado", "autopiloto", "controle", "kinect", "rostos", "zed"])
        carregar.assert_called_once_with()
        abrir_banco.assert_called_once_with()
        fechar_banco.assert_called_once_with()
        fechar.assert_called_once_with()
        self.assertTrue(estado.stop_event.is_set())
        self.assertEqual(runtime._threads, [])


if __name__ == "__main__":
    unittest.main()
