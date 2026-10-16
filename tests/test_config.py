import os
import sys
import unittest
from unittest.mock import patch

from pc_agv import config


class PastaDeDadosTest(unittest.TestCase):
    def test_codigo_fonte_usa_logs_do_projeto(self):
        with patch.dict(os.environ, {"AGV_DADOS_DIR": ""}), patch.object(sys, "frozen", False, create=True):
            self.assertEqual(config._pasta_de_dados(), os.path.join(config.PROJECT_ROOT, "logs"))

    def test_executavel_guarda_os_dados_ao_lado_do_binario(self):
        # No onefile o codigo roda numa pasta temporaria apagada ao fechar o programa.
        binario = os.path.abspath(os.path.join("dist", "launch_production"))
        with (
            patch.dict(os.environ, {"AGV_DADOS_DIR": ""}),
            patch.object(sys, "frozen", True, create=True),
            patch.object(sys, "executable", binario),
        ):
            self.assertEqual(config._pasta_de_dados(), os.path.join(os.path.dirname(binario), "logs"))

    def test_variavel_de_ambiente_tem_prioridade(self):
        pasta = os.path.abspath("dados-agv")
        with patch.dict(os.environ, {"AGV_DADOS_DIR": pasta}), patch.object(sys, "frozen", True, create=True):
            self.assertEqual(config._pasta_de_dados(), pasta)

    def test_rostos_ficam_na_pasta_de_dados(self):
        self.assertEqual(config.FACE_DB_DIR, config.DADOS_DIR)
        self.assertEqual(os.path.dirname(config.ENCODINGS_FILE), config.DADOS_DIR)


if __name__ == "__main__":
    unittest.main()
