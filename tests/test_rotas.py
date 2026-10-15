import io
import unittest
from unittest.mock import patch

from apoio import estado_isolado, rostos_isolado

from pc_agv import estado, util
from pc_agv.web import rotas


class _Base(unittest.TestCase):
    def setUp(self):
        self.client = rotas.app.test_client()
        self._isolar = estado_isolado()
        self._isolar.__enter__()

    def tearDown(self):
        self._isolar.__exit__(None, None, None)


class TestPainel(_Base):
    def test_pagina_principal_sem_cache(self):
        resposta = self.client.get("/")
        self.assertEqual(resposta.status_code, 200)
        self.assertIn("no-store", resposta.headers["Cache-Control"])
        self.assertIn(b'/static/painel.js', resposta.data)
        self.assertIn(b'/static/painel.css', resposta.data)
        resposta.close()

    def test_arquivos_estaticos(self):
        for caminho, tipo in (("/static/painel.css", "text/css"), ("/static/painel.js", "javascript")):
            with self.subTest(caminho=caminho):
                resposta = self.client.get(caminho)
                self.assertEqual(resposta.status_code, 200)
                self.assertIn(tipo, resposta.headers["Content-Type"])
                resposta.close()


class TestStreams(_Base):
    def test_cada_stream_entrega_um_quadro_jpeg(self):
        for caminho in ("/video", "/depth_map", "/zed", "/api/video", "/api/depth_map", "/api/zed"):
            with self.subTest(caminho=caminho):
                resposta = self.client.get(caminho, buffered=False)
                self.assertTrue(resposta.mimetype.startswith("multipart/x-mixed-replace"))
                primeiro = next(iter(resposta.response))
                resposta.close()
                self.assertTrue(primeiro.startswith(b"--frame\r\nContent-Type: image/jpeg"))


class TestStatus(_Base):
    def test_status_tem_todas_as_secoes(self):
        for caminho in ("/api/status", "/status"):
            with self.subTest(caminho=caminho):
                dados = self.client.get(caminho).get_json()
                self.assertTrue(dados["ok"])
                for chave in ("state", "agv", "autopilot", "arduino", "kinect", "zed", "face", "settings", "runtime"):
                    self.assertIn(chave, dados)

    def test_link_de_acesso_usa_o_ip_da_rede(self):
        with patch.object(rotas, "_detect_lan_ip", return_value="10.0.0.5"):
            dados = self.client.get("/api/access_link").get_json()
        self.assertEqual(dados["ip"], "10.0.0.5")
        self.assertTrue(dados["url"].startswith("http://10.0.0.5:"))


class TestControle(_Base):
    def _enviar(self, corpo):
        resposta = self.client.post("/api/control", json=corpo)
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta.headers.get("Connection"), "close")
        return dict(estado.control_state)

    def test_comando_valido(self):
        final = self._enviar({"mode": "manual", "speed": 40, "steering": -20, "source": "teste"})
        self.assertEqual((final["mode"], final["manual_speed"], final["manual_steering"], final["last_source"]),
                         ("manual", 40, -20, "teste"))
        self.assertAlmostEqual(final["updated_at"], util.now(), delta=2)

    def test_modo_desconhecido_vira_manual(self):
        self.assertEqual(self._enviar({"mode": "turbo"})["mode"], "manual")

    def test_valores_invalidos_viram_zero(self):
        final = self._enviar({"mode": "manual", "speed": "abc", "steering": None})
        self.assertEqual((final["manual_speed"], final["manual_steering"]), (0, 0))

    def test_valores_fora_da_faixa_sao_limitados(self):
        final = self._enviar({"mode": "manual", "speed": 500, "steering": -500})
        self.assertEqual((final["manual_speed"], final["manual_steering"]), (100, -100))

    def test_origem_padrao_e_web(self):
        self.assertEqual(self._enviar({"mode": "auto"})["last_source"], "web")

    def test_parada_total(self):
        estado.control_state.update({"mode": "auto", "manual_speed": 80, "active_speed": 80})
        with patch("pc_agv.hardware.arduino.send_to_arduino", return_value=True) as envio:
            resposta = self.client.post("/api/stop")
        self.assertEqual(resposta.status_code, 200)
        envio.assert_called_once_with(0, 0, "manual", "stop")
        final = estado.control_state
        self.assertEqual((final["mode"], final["manual_speed"], final["active_speed"], final["last_source"]),
                         ("manual", 0, 0, "stop"))


class TestConfiguracoes(_Base):
    def _limite(self, valor):
        return self.client.post("/api/settings", json={"speed_limit_pct": valor})

    def test_le_e_altera_o_limite(self):
        self.assertEqual(self._limite(40).get_json()["settings"]["speed_limit_pct"], 40)
        self.assertEqual(self.client.get("/api/settings").get_json()["settings"]["speed_limit_pct"], 40)

    def test_limite_fica_entre_0_e_100(self):
        self.assertEqual(self._limite(150).get_json()["settings"]["speed_limit_pct"], 100)
        self.assertEqual(self._limite(-5).get_json()["settings"]["speed_limit_pct"], 0)

    def test_limite_invalido(self):
        resposta = self._limite("abc")
        self.assertEqual(resposta.status_code, 400)
        self.assertFalse(resposta.get_json()["ok"])


class TestKinect(_Base):
    def test_tilt_valido(self):
        with patch("pc_agv.hardware.kinect.command_kinect_tilt", return_value=(True, "ok", 12.0)) as tilt:
            resposta = self.client.post("/api/kinect/tilt", json={"angle": "12"})
        self.assertEqual(resposta.status_code, 200)
        tilt.assert_called_once_with(12.0)

    def test_tilt_invalido_responde_400(self):
        with patch("pc_agv.hardware.kinect.command_kinect_tilt") as tilt:
            for angulo in ("abc", None, [1], "nan", "inf"):
                with self.subTest(angulo=angulo):
                    resposta = self.client.post("/api/kinect/tilt", json={"angle": angulo})
                    self.assertEqual(resposta.status_code, 400)
                    self.assertEqual(resposta.get_json()["error"], "invalid_angle")
        tilt.assert_not_called()

    def test_reconectar(self):
        with patch("pc_agv.hardware.kinect.request_reconnect") as reconectar:
            dados = self.client.post("/api/kinect/reconnect").get_json()
        reconectar.assert_called_once_with()
        self.assertTrue(dados["requested"])


class TestRostos(_Base):
    def test_status(self):
        with rostos_isolado():
            dados = self.client.get("/api/face/status").get_json()
        self.assertTrue(dados["ok"])
        self.assertIn("names", dados["face"])

    def test_selecionar_pessoa_desconhecida(self):
        with rostos_isolado():
            resposta = self.client.post("/api/face/select", json={"name": "Carlos"})
        self.assertEqual(resposta.status_code, 404)

    def test_limpar_selecao(self):
        with rostos_isolado():
            resposta = self.client.post("/api/face/select", json={"name": ""})
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta.get_json()["message"], "nenhum rosto selecionado")

    def test_cadastro_sem_nome(self):
        resposta = self.client.post("/api/face/register_face", data={"name": ""})
        self.assertEqual((resposta.status_code, resposta.get_json()["error"]), (400, "name_required"))

    def test_upload_grande_demais_responde_413(self):
        arquivo = (io.BytesIO(b"x" * 4096), "foto.jpg")
        with patch.dict(rotas.app.config, {"MAX_CONTENT_LENGTH": 1024}), \
                patch("pc_agv.visao.rostos.register_face") as cadastrar:
            resposta = self.client.post("/api/face/register_face", data={"name": "Ana", "image": arquivo},
                                        content_type="multipart/form-data")
        self.assertEqual(resposta.status_code, 413)
        self.assertEqual(resposta.get_json()["error"], "file_too_large")
        cadastrar.assert_not_called()

    def test_limite_padrao_de_upload(self):
        self.assertEqual(rotas.app.config["MAX_CONTENT_LENGTH"], int(rotas.MAX_UPLOAD_MB * 1024 * 1024))


if __name__ == "__main__":
    unittest.main()
