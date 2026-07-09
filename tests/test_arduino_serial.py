import sys
import unittest
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

from pc_agv import iniciar_sistema as sistema


class FakeSerial:
    def __init__(self, incoming=None):
        self.incoming = list(incoming or [])
        self.written = []
        self.is_open = True

    @property
    def in_waiting(self):
        return sum(len(item) for item in self.incoming)

    def readline(self):
        if not self.incoming:
            return b""
        return self.incoming.pop(0)

    def write(self, data):
        self.written.append(data)
        return len(data)


class TestArduinoSerial(unittest.TestCase):
    def setUp(self):
        sistema._arduino_state["last_error"] = "erro antigo"

    def test_drain_le_feedback_do_arduino(self):
        serial = FakeSerial([
            b"ARDUINO:READY:v2.0\n",
            b"ARDUINO:OK:140,0\n",
            b"ARDUINO:STATUS:RUN\n",
        ])

        lines = sistema._drain_arduino_input_locked(serial)

        self.assertEqual(lines, [
            "ARDUINO:READY:v2.0",
            "ARDUINO:OK:140,0",
            "ARDUINO:STATUS:RUN",
        ])
        self.assertEqual(serial.in_waiting, 0)
        self.assertIsNone(sistema._arduino_state["last_error"])

    def test_linha_status_limpa_erro_anterior(self):
        serial = FakeSerial([
            b"STATUS:0,0,OK,E:0\n",
        ])

        lines = sistema._drain_arduino_input_locked(serial)

        self.assertEqual(lines, ["STATUS:0,0,OK,E:0"])
        self.assertIsNone(sistema._arduino_state["last_error"])

    def test_busca_porta_arduino_quando_glob_vazio(self):
        fake_list_ports = ModuleType("serial.tools.list_ports")
        fake_list_ports.comports = lambda: [SimpleNamespace(device="/dev/ttyACM0")]

        fake_tools = ModuleType("serial.tools")
        fake_tools.list_ports = fake_list_ports

        fake_serial = ModuleType("serial")
        fake_serial.tools = fake_tools

        with patch.object(sistema.glob, "glob", return_value=[]):
            with patch.dict(
                sys.modules,
                {
                    "serial": fake_serial,
                    "serial.tools": fake_tools,
                    "serial.tools.list_ports": fake_list_ports,
                },
                clear=False,
            ):
                ports = sistema._candidate_arduino_ports()

        self.assertEqual(ports, ["/dev/ttyACM0"])

    def test_payload_reversa_envia_valor_negativo_para_o_arduino(self):
        accel, dir_val = sistema._speed_steering_to_payload(-50, 0)

        self.assertEqual(accel, -170)
        self.assertEqual(dir_val, 0)


if __name__ == "__main__":
    unittest.main()
