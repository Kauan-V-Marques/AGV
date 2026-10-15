import json
import unittest
import urllib.request
from unittest.mock import patch

from pc_agv import iniciar_sistema


class TestServidorHttp(unittest.TestCase):
    def test_sobe_responde_e_para(self):
        with patch.object(iniciar_sistema, "start_runtime") as iniciar, \
                patch.object(iniciar_sistema, "stop_runtime") as parar:
            servidor = iniciar_sistema.create_server(host="127.0.0.1", port=0)  # porta livre qualquer
            porta = servidor._server.server_port
            servidor.start()
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{porta}/api/status", timeout=5) as resposta:
                    self.assertEqual(resposta.status, 200)
                    self.assertTrue(json.loads(resposta.read())["ok"])
            finally:
                servidor.stop()
        iniciar.assert_called_once_with()
        parar.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
