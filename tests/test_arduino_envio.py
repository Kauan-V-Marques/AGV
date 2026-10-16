import os
import sys
import unittest
from types import ModuleType
from unittest.mock import patch

from apoio import SerialFalsa, arduino_isolado, estado_isolado

from pc_agv import estado
from pc_agv.hardware import arduino


class TestProtocoloDoArduino(unittest.TestCase):
    def test_tabela_de_conversao(self):
        casos = {
            (0, 0): (0, 0),
            (100, 0): (255, 0),
            (50, 0): (170, 0),       # abaixo do PWM minimo para andar -> sobe para 170
            (-100, 0): (-255, 0),    # re
            (0, -70): (140, -124),   # gira parado: PWM minimo de giro e direcao atenuada (ganho 0,7)
            (0, 10): (0, 25),        # direcao pequena parado nao liga o motor
            (100, 70): (255, 124),   # andando, a direcao e atenuada (ganho 0,7)
            (0, 3): (0, 0),          # zona morta da direcao
            (500, -500): (255, -177),  # entradas fora da faixa sao limitadas
        }
        for (speed, steering), esperado in casos.items():
            with self.subTest(speed=speed, steering=steering):
                self.assertEqual(arduino.speed_steering_to_payload(speed, steering), esperado)


class TestEnvioPelaSerial(unittest.TestCase):
    def _enviar(self, speed, steering, serial=None, limite=100, habilitado=True):
        with arduino_isolado(), estado_isolado():
            arduino._arduino_state["enabled"] = habilitado
            estado.settings["speed_limit_pct"] = limite
            arduino._arduino_serial = serial
            ok = arduino.send_to_arduino(speed, steering, "manual", "teste")
            return ok, dict(arduino._arduino_state), arduino._arduino_serial

    def test_serial_desativada_nao_envia(self):
        ok, final, _ = self._enviar(100, 0, habilitado=False)
        self.assertFalse(ok)
        self.assertEqual(final["last_error"], "serial desativada por ambiente")

    def test_envia_linha_no_protocolo(self):
        serial = SerialFalsa()
        ok, final, _ = self._enviar(100, 0, serial=serial)
        self.assertTrue(ok)
        self.assertEqual(serial.escrito, [b"255,0\n"])
        self.assertEqual(final["last_command"], "255,0")
        self.assertTrue(final["connected"])

    def test_limite_de_velocidade_reduz_o_pwm(self):
        serial = SerialFalsa()
        self._enviar(100, 0, serial=serial, limite=50)
        self._enviar(-100, 0, serial=serial, limite=50)
        self.assertEqual(serial.escrito, [b"127,0\n", b"-127,0\n"])

    def test_falha_de_escrita_marca_desconectado_e_fecha_a_porta(self):
        serial = SerialFalsa(falhar_escrita=True)
        ok, final, porta = self._enviar(100, 0, serial=serial)
        self.assertFalse(ok)
        self.assertFalse(final["connected"])
        self.assertIn("cabo desconectado", final["last_error"])
        self.assertTrue(serial.fechada)
        self.assertIsNone(porta)


def _modulo_serial_falso(fabrica):
    modulo = ModuleType("serial")
    modulo.Serial = fabrica
    return modulo


class TestConexaoComOArduino(unittest.TestCase):
    def _conectar(self, portas, fabrica=None, habilitado=True):
        modulos = {"serial": _modulo_serial_falso(fabrica)} if fabrica else {}
        with arduino_isolado(), \
                patch.object(arduino, "_candidate_arduino_ports", return_value=portas), \
                patch.object(arduino.time, "sleep"), \
                patch.dict(sys.modules, modulos):
            arduino._arduino_state["enabled"] = habilitado
            ok = arduino._connect_arduino()
            return ok, dict(arduino._arduino_state), arduino._arduino_serial

    def test_desativada(self):
        ok, final, _ = self._conectar(["/dev/ttyACM0"], habilitado=False)
        self.assertFalse(ok)
        self.assertEqual(final["last_error"], "serial desativada por ambiente")

    def test_sem_pyserial(self):
        with patch.dict(sys.modules, {"serial": None}):
            ok, final, _ = self._conectar(["/dev/ttyACM0"])
        self.assertFalse(ok)
        self.assertIn("pyserial ausente", final["last_error"])

    def test_nenhuma_porta(self):
        ok, final, _ = self._conectar([], fabrica=lambda **kw: SerialFalsa())
        self.assertFalse(ok)
        self.assertEqual(final["last_error"], "nenhuma porta serial encontrada")

    def test_conecta_e_identifica_o_firmware(self):
        serial = SerialFalsa([b"ARDUINO:READY:v2.0\n"])
        ok, final, porta = self._conectar(["/dev/ttyACM0"], fabrica=lambda **kw: serial)
        self.assertTrue(ok)
        self.assertTrue(final["connected"])
        self.assertEqual(final["port"], os.path.realpath("/dev/ttyACM0"))
        self.assertIsNone(final["last_error"])
        self.assertIs(porta, serial)
        self.assertEqual(serial.escrito, [])  # READY ja identificou, sem PING

    def test_porta_sem_resposta_avisa_mas_mantem_conectado(self):
        serial = SerialFalsa()
        ok, final, _ = self._conectar(["/dev/ttyACM0"], fabrica=lambda **kw: serial)
        self.assertTrue(ok)
        self.assertEqual(serial.escrito, [b"PING\n"])
        self.assertEqual(final["last_error"], "porta abriu, mas sem resposta do firmware")

    def test_sem_permissao_explica_o_grupo_dialout(self):
        def negar(**kw):
            raise PermissionError("[Errno 13] Permission denied")

        ok, final, _ = self._conectar(["/dev/ttyACM0"], fabrica=negar)
        self.assertFalse(ok)
        self.assertIn("dialout", final["last_error"])


class TestEstadoDoLink(unittest.TestCase):
    def test_snapshot_e_copia(self):
        with arduino_isolado():
            copia = arduino.status_snapshot()
            copia["connected"] = "alterado"
            self.assertNotEqual(arduino._arduino_state["connected"], "alterado")

    def test_ultimo_erro(self):
        with arduino_isolado():
            arduino._arduino_state["last_error"] = "falha x"
            self.assertEqual(arduino.last_error(), "falha x")

    def test_fechar_desconecta(self):
        serial = SerialFalsa()
        with arduino_isolado():
            arduino._arduino_serial = serial
            arduino._arduino_state["connected"] = True
            arduino.close()
            self.assertTrue(serial.fechada)
            self.assertIsNone(arduino._arduino_serial)
            self.assertFalse(arduino._arduino_state["connected"])


if __name__ == "__main__":
    unittest.main()
