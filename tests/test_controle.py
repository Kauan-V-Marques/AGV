import unittest
from unittest.mock import patch

from apoio import EventoDeParada, estado_isolado

from pc_agv import controle, estado, util


def _leituras(**valores):
    base = {
        "kinect_ok": True,
        "distance_m": 2.0,
        "left_clearance_m": 2.0,
        "center_clearance_m": 2.0,
        "right_clearance_m": 2.0,
    }
    base.update(valores)
    return base


class TestRegrasDoAutonomo(unittest.TestCase):
    def _comando(self, **valores):
        with estado_isolado():
            estado.state.update(_leituras(**valores))
            return controle.compute_autopilot_command()

    def test_sem_kinect_para(self):
        self.assertEqual(self._comando(kinect_ok=False), (0, 0, "sem-kinect"))

    def test_sem_profundidade_para(self):
        self.assertEqual(self._comando(center_clearance_m=None, distance_m=None), (0, 0, "sem-depth"))

    def test_sem_centro_usa_distancia_frontal(self):
        speed, _, reason = self._comando(center_clearance_m=None, distance_m=0.30)
        self.assertEqual((speed, reason), (0, "obstaculo-muito-perto"))

    def test_obstaculo_muito_perto_gira_para_o_lado_mais_livre(self):
        self.assertEqual(self._comando(center_clearance_m=0.30, left_clearance_m=0.5, right_clearance_m=1.5),
                         (0, 70, "obstaculo-muito-perto"))
        self.assertEqual(self._comando(center_clearance_m=0.30, left_clearance_m=1.5, right_clearance_m=0.5),
                         (0, -70, "obstaculo-muito-perto"))

    def test_desvio_curto(self):
        self.assertEqual(self._comando(center_clearance_m=0.50, left_clearance_m=1.0, right_clearance_m=0.4),
                         (18, -65, "desvio-curto"))

    def test_desvio_suave(self):
        self.assertEqual(self._comando(center_clearance_m=0.80, left_clearance_m=0.4, right_clearance_m=1.0),
                         (32, 45, "desvio-suave"))

    def test_livre_corrige_pela_diferenca_entre_os_lados(self):
        # diferenca 0,5 m * 70 = 35 (limite maximo)
        self.assertEqual(self._comando(left_clearance_m=1.0, right_clearance_m=1.5), (48, 35, "livre"))
        # diferenca grande e limitada a -35
        self.assertEqual(self._comando(left_clearance_m=3.0, right_clearance_m=1.0), (48, -35, "livre"))

    def test_livre_ignora_diferenca_pequena(self):
        # 0,05 m * 70 = 3,5 -> abaixo da zona morta de 8
        self.assertEqual(self._comando(left_clearance_m=2.0, right_clearance_m=2.05), (48, 0, "livre"))

    def test_livre_sem_um_dos_lados_vai_reto(self):
        self.assertEqual(self._comando(left_clearance_m=None), (48, 0, "livre"))


class TestLoopDeControle(unittest.TestCase):
    def _uma_volta(self, envio_ok=True, ultimo_erro=None, **controle_state):
        with estado_isolado(), \
                patch.object(estado, "stop_event", EventoDeParada(1)), \
                patch("pc_agv.hardware.arduino.send_to_arduino", return_value=envio_ok) as envio, \
                patch("pc_agv.hardware.arduino.last_error", return_value=ultimo_erro):
            estado.control_state.update(controle_state)
            controle.control_loop()
            return envio, dict(estado.control_state)

    def test_manual_recente_envia_o_comando_do_operador(self):
        envio, final = self._uma_volta(mode="manual", manual_speed=40, manual_steering=10,
                                       last_source="site-keyboard", updated_at=util.now())
        envio.assert_called_once_with(40, 10, "manual", "site-keyboard")
        self.assertEqual((final["active_speed"], final["active_steering"]), (40, 10))
        self.assertTrue(final["arduino_sent"])
        self.assertIsNone(final["arduino_error"])

    def test_watchdog_para_o_agv_sem_comando_novo(self):
        envio, final = self._uma_volta(mode="manual", manual_speed=100, manual_steering=0,
                                       last_source="site-keyboard",
                                       updated_at=util.now() - (controle.WATCHDOG_TIMEOUT_S + 1.0))
        envio.assert_called_once_with(0, 0, "manual", "watchdog")
        self.assertEqual(final["active_speed"], 0)

    def test_watchdog_desligado_mantem_o_comando(self):
        with patch.object(controle, "WATCHDOG_TIMEOUT_S", 0):
            envio, _ = self._uma_volta(mode="manual", manual_speed=100, manual_steering=0,
                                       last_source="site-keyboard", updated_at=util.now() - 60)
        envio.assert_called_once_with(100, 0, "manual", "site-keyboard")

    def test_autonomo_envia_o_alvo_do_autopiloto(self):
        with patch.dict(estado.autopilot_state, {"target_speed": 32, "target_steering": -45}):
            envio, final = self._uma_volta(mode="auto", manual_speed=100, manual_steering=0,
                                           updated_at=util.now() - 60)
        envio.assert_called_once_with(32, -45, "auto", "autopilot")
        self.assertEqual((final["active_speed"], final["active_steering"]), (32, -45))

    def test_falha_no_envio_registra_o_erro_do_arduino(self):
        _, final = self._uma_volta(envio_ok=False, ultimo_erro="nenhuma porta serial encontrada",
                                   mode="manual", manual_speed=0, manual_steering=0, updated_at=util.now())
        self.assertFalse(final["arduino_sent"])
        self.assertEqual(final["arduino_error"], "nenhuma porta serial encontrada")


class TestLoopDoAutopiloto(unittest.TestCase):
    def _uma_volta(self, modo):
        with estado_isolado(), patch.dict(estado.autopilot_state), \
                patch.object(estado, "stop_event", EventoDeParada(1)), \
                patch.object(controle, "compute_autopilot_command", return_value=(20, 5, "teste")):
            estado.control_state["mode"] = modo
            controle.autopilot_loop()
            return dict(estado.autopilot_state)

    def test_no_autonomo_calcula_o_alvo(self):
        final = self._uma_volta("auto")
        self.assertTrue(final["running"])
        self.assertEqual((final["target_speed"], final["target_steering"], final["last_reason"]), (20, 5, "teste"))

    def test_no_manual_zera_o_alvo(self):
        final = self._uma_volta("manual")
        self.assertFalse(final["running"])
        self.assertEqual((final["target_speed"], final["target_steering"], final["last_reason"]), (0, 0, "manual"))


if __name__ == "__main__":
    unittest.main()
