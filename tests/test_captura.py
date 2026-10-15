import threading
import time
import unittest
from unittest.mock import MagicMock, patch

import cv2
import numpy as np
from apoio import EventoDeParada, estado_isolado

from pc_agv import estado
from pc_agv.hardware import kinect, zed


def _raw_para_metros(raw: int) -> float:
    return 1.0 / (raw * -0.0030711016 + 3.3309495161)


def _jpeg(altura: int, largura: int) -> bytes:
    ok, buffer = cv2.imencode(".jpg", np.zeros((altura, largura, 3), np.uint8))
    assert ok
    return buffer.tobytes()


class TestKinectDisponibilidade(unittest.TestCase):
    def _verificar(self, freenect):
        with patch.object(kinect, "freenect", freenect):
            return kinect._kinect_device_available()

    def test_sem_biblioteca(self):
        self.assertEqual(self._verificar(None), (False, kinect.SEM_DRIVER))

    def test_falha_ao_iniciar(self):
        fake = MagicMock()
        fake.init.return_value = None
        self.assertEqual(self._verificar(fake), (False, "falha ao iniciar libfreenect"))

    def test_kinect_desconectado(self):
        fake = MagicMock()
        fake.num_devices.return_value = 0
        self.assertEqual(self._verificar(fake), (False, "Kinect nao detectado no USB"))
        fake.shutdown.assert_called_once()

    def test_kinect_conectado(self):
        fake = MagicMock()
        fake.num_devices.return_value = 1
        self.assertEqual(self._verificar(fake), (True, None))

    def test_erro_da_biblioteca(self):
        fake = MagicMock()
        fake.init.side_effect = RuntimeError("LIBUSB_ERROR_BUSY")
        ok, mensagem = self._verificar(fake)
        self.assertFalse(ok)
        self.assertIn("LIBUSB_ERROR_BUSY", mensagem)


class TestKinectComandos(unittest.TestCase):
    def test_reconectar_para_a_captura_e_libera_o_usb(self):
        fake = MagicMock()
        with patch.object(kinect, "freenect", fake), patch.object(kinect, "release_kinect_usb_claims") as liberar:
            kinect.request_reconnect()
        fake.sync_stop.assert_called_once_with()
        liberar.assert_called_once_with()

    def test_reconectar_sem_biblioteca_so_libera_o_usb(self):
        with patch.object(kinect, "freenect", None), patch.object(kinect, "release_kinect_usb_claims") as liberar:
            kinect.request_reconnect()
        liberar.assert_called_once_with()

    def test_tilt_e_limitado_a_faixa_do_motor(self):
        with estado_isolado(), patch.object(kinect, "freenect", None):
            ok, mensagem, aplicado = kinect.command_kinect_tilt(30)
            self.assertEqual((ok, mensagem, aplicado), (True, "comando enviado", kinect.KINECT_TILT_MAX))
            self.assertEqual(estado.state["tilt_target_deg"], kinect.KINECT_TILT_MAX)

    def test_alvo_do_tilt_pelo_acelerometro(self):
        # sem gravidade medida: volta para a posicao neutra
        self.assertEqual(kinect._compute_tilt_target(0.0, 0.0, 0.0), kinect.KINECT_TILT_NEUTRAL)
        # inclinado para frente: corrige no sentido oposto, ate o limite do motor
        self.assertEqual(kinect._compute_tilt_target(4.0, 0.0, 8.0), kinect.KINECT_TILT_MIN)
        # inclinacao minima fica dentro da zona morta
        self.assertEqual(kinect._compute_tilt_target(0.01, 0.0, 9.8), kinect.KINECT_TILT_NEUTRAL)

    def test_liga_e_desliga_a_estabilizacao(self):
        original = kinect.get_kinect_stabilization()
        try:
            self.assertFalse(kinect.set_kinect_stabilization(False))
            self.assertFalse(kinect.get_kinect_stabilization())
            self.assertTrue(kinect.set_kinect_stabilization(True))
        finally:
            kinect.set_kinect_stabilization(original)


class TestKinectCaptura(unittest.TestCase):
    def test_sem_biblioteca_marca_offline(self):
        with estado_isolado(), patch.object(kinect, "freenect", None), \
                patch.object(estado, "stop_event", EventoDeParada(1)):
            kinect.capture_loop()
            self.assertFalse(estado.state["kinect_ok"])
            self.assertEqual(estado.state["kinect_error"], kinect.SEM_DRIVER)

    def test_callbacks_viram_video_e_distancias_no_estado(self):
        parar = threading.Event()
        quadro_rgb = np.zeros((480, 640, 3), np.uint8)
        quadro_rgb[..., 0] = 255  # canal vermelho no formato RGB do Kinect
        quadro_profundidade = np.full((480, 640), 1000, np.uint16)  # parede a ~3,85 m
        quadro_profundidade[200:260, 300:340] = 500  # obstaculo a ~0,56 m no centro

        def runloop(depth=None, video=None, body=None):
            video(None, quadro_rgb, 0)
            depth(None, quadro_profundidade, 0)
            prazo = time.time() + 3.0
            while time.time() < prazo and estado.state.get("center_clearance_m") is None:
                time.sleep(0.02)
            parar.set()

        freenect = MagicMock()
        freenect.runloop.side_effect = runloop
        with estado_isolado(), patch.object(estado, "stop_event", parar), \
                patch.object(kinect, "freenect", freenect), \
                patch.object(kinect, "_kinect_device_available", return_value=(True, None)), \
                patch.object(kinect, "release_kinect_usb_claims"), \
                patch.object(kinect, "_do_tilt_body"):
            estado.state.update({"center_clearance_m": None, "rgb": None})
            kinect.capture_loop()
            for thread in threading.enumerate():
                if thread.name == "agv-kinect-proc":
                    thread.join(timeout=2.0)

            self.assertTrue(estado.state["kinect_ok"])
            self.assertEqual(int(estado.state["rgb"][0, 0, 2]), 255)  # convertido para BGR
            self.assertAlmostEqual(estado.state["center_clearance_m"], _raw_para_metros(500), places=3)
            self.assertAlmostEqual(estado.state["left_clearance_m"], _raw_para_metros(1000), places=3)


class TestZedFontes(unittest.TestCase):
    def test_valor_da_fonte(self):
        self.assertEqual(zed._zed_source_value("2"), 2)
        self.assertEqual(zed._zed_source_value(""), 0)
        self.assertEqual(zed._zed_source_value("/dev/video1"), "/dev/video1")

    def test_candidatos_sem_repeticao(self):
        with patch.object(zed, "ZED_DEVICE", "/dev/video0"), \
                patch.object(zed, "_zed_nodes_from_sysfs", return_value=["/dev/video2", "/dev/video0"]):
            with patch.object(zed, "ZED_STRICT_DEVICE", True):
                self.assertEqual(zed._zed_candidate_sources(), ["/dev/video0", "/dev/video2"])
            with patch.object(zed, "ZED_STRICT_DEVICE", False), \
                    patch.object(zed.glob, "glob", return_value=["/dev/video0", "/dev/video4"]):
                self.assertEqual(zed._zed_candidate_sources(), ["/dev/video0", "/dev/video2", "/dev/video4"])


class _PipeFalso:
    def __init__(self, dados: bytes, morto: bool = False):
        self.stdout = MagicMock()
        self.stdout.read.side_effect = [dados, b""]
        self._morto = morto

    def poll(self):
        return 1 if self._morto else None


class TestZedFfmpeg(unittest.TestCase):
    def test_pega_o_quadro_mais_recente_do_pipe(self):
        dados = _jpeg(10, 10) + _jpeg(20, 30)
        processo = _PipeFalso(dados)
        with patch.object(zed.select, "select", return_value=([processo.stdout], [], [])):
            quadro, resto = zed._read_frame_from_ffmpeg(processo, b"")
        self.assertEqual(quadro.shape, (20, 30, 3))
        self.assertEqual(resto, b"")

    def test_processo_encerrado(self):
        self.assertEqual(zed._read_frame_from_ffmpeg(_PipeFalso(b"", morto=True), b"x"), (None, b""))


class _CameraFalsa:
    def __init__(self, quadro):
        self.quadro = quadro
        self.liberada = False

    def read(self):
        return True, self.quadro.copy()

    def release(self):
        self.liberada = True


class TestZedCaptura(unittest.TestCase):
    def test_um_quadro_com_rosto_marcado(self):
        camera = _CameraFalsa(np.zeros((120, 160, 3), np.uint8))
        with estado_isolado(), patch.object(estado, "stop_event", EventoDeParada(2)), \
                patch.object(zed, "_zed_candidate_sources", return_value=["/dev/video9"]), \
                patch.object(zed, "_open_zed_cv_capture", return_value=(camera, None)), \
                patch("pc_agv.visao.rostos.offer_frame", return_value=(True, "Ana", (5, 5, 20, 20))):
            zed.capture_loop()
            self.assertTrue(estado.state["zed_ok"])
            self.assertEqual(estado.state["zed_device"], "/dev/video9")
            self.assertEqual(tuple(int(c) for c in estado.state["zed"][5, 5]), (80, 230, 120))
        self.assertTrue(camera.liberada)

    def test_sem_camera_avisa_no_estado(self):
        with estado_isolado(), patch.object(estado, "stop_event", EventoDeParada(1)), \
                patch.object(zed, "_zed_candidate_sources", return_value=[]):
            zed.capture_loop()
            self.assertFalse(estado.state["zed_ok"])
            self.assertIn("nao abriu webcam ZED", estado.state["zed_error"])


if __name__ == "__main__":
    unittest.main()
