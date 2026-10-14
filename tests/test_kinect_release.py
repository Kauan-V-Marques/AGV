import unittest
from unittest.mock import patch, call

from pc_agv import util
from pc_agv.hardware import kinect
from pc_agv.web.rotas import app


class TestKinectRelease(unittest.TestCase):
    @staticmethod
    def _sudo(*args):
        return call(
            ["sudo", "-n", *args],
            stdout=kinect.subprocess.DEVNULL,
            stderr=kinect.subprocess.DEVNULL,
            check=False,
        )

    @patch("pc_agv.hardware.kinect.subprocess.run")
    def test_libera_modulos_kernel_na_ordem_certa(self, run_mock):
        run_mock.return_value.returncode = 0

        with patch.object(kinect, "KINECT_RELEASE_UVCVIDEO", False):
            kinect.release_kinect_usb_claims()

        expected = [
            self._sudo("modprobe", "-r", "gspca_kinect", "gspca_main", "snd_usb_audio"),
            self._sudo("rmmod", "gspca_kinect"),
            self._sudo("rmmod", "gspca_main"),
            self._sudo("rmmod", "snd_usb_audio"),
        ]

        self.assertEqual(run_mock.call_args_list, expected)

    @patch("pc_agv.hardware.kinect.subprocess.run")
    def test_libera_uvcvideo_quando_habilitado(self, run_mock):
        run_mock.return_value.returncode = 0

        with patch.object(kinect, "KINECT_RELEASE_UVCVIDEO", True):
            kinect.release_kinect_usb_claims()

        expected = [
            self._sudo(
                "modprobe", "-r",
                "gspca_kinect", "gspca_main", "snd_usb_audio",
                "uvcvideo", "videobuf2_v4l2", "videobuf2_vmalloc", "videobuf2_common", "videodev", "mc",
            ),
            self._sudo("rmmod", "gspca_kinect"),
            self._sudo("rmmod", "gspca_main"),
            self._sudo("rmmod", "snd_usb_audio"),
            self._sudo("rmmod", "uvcvideo"),
        ]

        self.assertEqual(run_mock.call_args_list, expected)

    @patch("pc_agv.hardware.kinect.release_kinect_usb_claims")
    def test_rota_reconectar_chama_liberacao_usb(self, release_mock):
        client = app.test_client()

        response = client.post("/api/kinect/reconnect")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["ok"])
        release_mock.assert_called_once_with()


class TestKinectTilt(unittest.TestCase):
    def setUp(self):
        kinect._tilt_last_set_at = 0.0
        kinect._tilt_last_target = 0.0

    @patch("pc_agv.hardware.kinect.command_kinect_tilt")
    def test_rota_tilt_envia_angulo_correto(self, tilt_mock):
        tilt_mock.return_value = (True, "ok", 10.0)
        client = app.test_client()

        response = client.post("/api/kinect/tilt", json={"angle": 10})

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["ok"])
        tilt_mock.assert_called_once_with(10.0)

    def test_comando_tilt_sobe_e_desce_imediato(self):
        sync_mock = unittest.mock.Mock()

        with patch.object(kinect, "freenect") as freenect_mock:
            freenect_mock.sync_set_tilt_degs = sync_mock

            ok_up, msg_up, applied_up = kinect.command_kinect_tilt(12)
            ok_down, msg_down, applied_down = kinect.command_kinect_tilt(-12)

        self.assertTrue(ok_up)
        self.assertTrue(ok_down)
        self.assertEqual(msg_up, "tilt aplicado")
        self.assertEqual(msg_down, "tilt aplicado")
        self.assertEqual(applied_up, 12.0)
        self.assertEqual(applied_down, -12.0)
        self.assertEqual(sync_mock.call_args_list, [call(12), call(-12)])

    def test_correcao_tilt_pelo_acelerometro(self):
        fake_state = object()

        with patch.object(kinect, "KINECT_STABILIZATION_ENABLED", True), \
             patch.object(kinect, "KINECT_TILT_INTERVAL", 0.0), \
             patch.object(kinect, "KINECT_TILT_DEADBAND", 0.1), \
             patch.object(kinect, "KINECT_TILT_MIN", -18.0), \
             patch.object(kinect, "KINECT_TILT_MAX", 18.0), \
             patch.object(kinect, "freenect") as freenect_mock:
            freenect_mock.get_tilt_state.return_value = fake_state
            freenect_mock.get_tilt_degs.return_value = 0.0
            freenect_mock.get_mks_accel.return_value = (4.0, 0.0, 8.0)
            set_tilt = freenect_mock.set_tilt_degs
            kinect._do_tilt_body(object())

        set_tilt.assert_called_once()
        requested = float(set_tilt.call_args.args[1])
        self.assertNotEqual(requested, 0.0)

    def test_tilt_manual_funciona_com_estabilizacao_off(self):
        fake_state = object()
        kinect._tilt_manual_until = util.now() + 2.0
        kinect._tilt_manual_target = 12.0
        kinect._tilt_last_set_at = 0.0

        with patch.object(kinect, "KINECT_STABILIZATION_ENABLED", False), \
             patch.object(kinect, "KINECT_TILT_INTERVAL", 0.0), \
             patch.object(kinect, "KINECT_TILT_DEADBAND", 0.1), \
             patch.object(kinect, "freenect") as freenect_mock:
            freenect_mock.get_tilt_state.return_value = fake_state
            freenect_mock.get_tilt_degs.return_value = 0.0
            freenect_mock.get_mks_accel.return_value = (0.0, 0.0, 0.0)
            set_tilt = freenect_mock.set_tilt_degs
            kinect._do_tilt_body(object())

        set_tilt.assert_called_once()

    def test_rota_stabilization_get_retorna_estado(self):
        client = app.test_client()

        with patch("pc_agv.hardware.kinect.get_kinect_stabilization", return_value=True):
            response = client.get("/api/kinect/stabilization")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["enabled"])

    @patch("pc_agv.hardware.kinect.set_kinect_stabilization")
    def test_rota_stabilization_post_atualiza_estado(self, set_mock):
        set_mock.return_value = True
        client = app.test_client()

        response = client.post("/api/kinect/stabilization", json={"enabled": True})

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["enabled"])
        set_mock.assert_called_once_with(True)


if __name__ == "__main__":
    unittest.main()
