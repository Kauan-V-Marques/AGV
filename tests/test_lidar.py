import math
import os
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from apoio import EventoDeParada, estado_isolado, mapeamento_isolado
from simulador_agv import raios
from simulador_kinect import quadro, sala

from pc_agv import arquivo_env, controle, estado, util
from pc_agv.aprendizado import politica
from pc_agv.hardware import arduino, rplidar, zed
from pc_agv.mapeamento import calibracao, varredura
from pc_agv.mapeamento import servico as servico_mapa
from pc_agv.mapeamento.geometria import Pose, compor
from pc_agv.visao import lidar as geometria


def no(angulo: float, distancia_m: float, inicio: bool = False, qualidade: int = 15) -> bytes:
    """Uma amostra de 5 bytes da varredura padrao do RPLIDAR."""
    angulo_q6 = int(round(angulo * 64))
    distancia_q2 = int(round(distancia_m * 4000))
    b0 = (qualidade << 2) | (1 if inicio else 2)
    return bytes([b0, ((angulo_q6 & 0x7F) << 1) | 1, angulo_q6 >> 7, distancia_q2 & 0xFF, distancia_q2 >> 8])


def volta(distancia_m: float = 2.0, passo_graus: float = 2.0, inicio: bool = True) -> bytes:
    dados = b""
    for i, angulo in enumerate(np.arange(0.0, 360.0, passo_graus)):
        dados += no(float(angulo), distancia_m, inicio=inicio and i == 0)
    return dados


CORPO = geometria.Corpo(frente_m=0.30, tras_m=0.30, largura_m=0.50)


def publicar_folgas(**folgas):
    """Coloca no estado uma volta recente do LIDAR com as folgas dadas (o resto livre)."""
    base = {setor: None for setor in geometria.SETORES}
    base.update(folgas)
    estado.lidar_state.update(ok=True, at=util.now(), pontos=np.zeros((30, 2)), folgas=base)


class TestProtocolo(unittest.TestCase):
    def test_comando_sem_e_com_dados(self):
        self.assertEqual(rplidar.comando(rplidar.CMD_SCAN), b"\xa5\x20")
        # A5 F0 02 94 02 e o XOR de todos os bytes
        self.assertEqual(rplidar.comando_pwm(660), bytes([0xA5, 0xF0, 0x02, 0x94, 0x02, 0xA5 ^ 0xF0 ^ 0x02 ^ 0x94 ^ 0x02]))
        self.assertEqual(rplidar.comando_pwm(5000)[3:5], bytes([0xFF, 0x03]))  # limitado a 1023

    def test_decodifica_amostras_e_guarda_o_resto(self):
        dados = no(0.0, 1.0, inicio=True) + no(90.5, 0.25) + b"\x3e\x01"
        amostras, resto = rplidar.decodificar(dados)
        self.assertEqual(len(amostras), 2)
        self.assertEqual(amostras[0], (True, 15, 0.0, 1.0))
        self.assertAlmostEqual(amostras[1][2], 90.5)
        self.assertAlmostEqual(amostras[1][3], 0.25)
        self.assertEqual(resto, b"\x3e\x01")

    def test_realinha_depois_de_lixo(self):
        amostras, _ = rplidar.decodificar(b"\x00\xff" + no(45.0, 2.0) + no(46.0, 2.0))
        self.assertEqual([round(a[2]) for a in amostras], [45, 46])

    def test_descarta_angulo_impossivel(self):
        invalida = bytearray(no(10.0, 1.0))
        invalida[2] = 0xFF  # angulo acima de 360 graus
        seguintes = b"".join(no(20.0 + i, 1.0) for i in range(8))
        amostras, _ = rplidar.decodificar(bytes(invalida) + seguintes)
        self.assertTrue(all(a[2] < 360.0 for a in amostras))
        # depois de perder o alinhamento, o fluxo volta a sair certo
        self.assertEqual([round(a[2]) for a in amostras[-3:]], [25, 26, 27])

    def test_info(self):
        dados = bytes([0x18, 24, 1, 5]) + bytes(range(16))
        self.assertEqual(rplidar.ler_info(dados)["firmware"], "1.24")
        self.assertEqual(rplidar.ler_info(dados)["hardware"], 5)
        self.assertIsNone(rplidar.ler_info(b"\x00"))


class TestGeometria(unittest.TestCase):
    def test_angulo_horario_vira_referencial_do_agv(self):
        # LIDAR montado com o 0 dele para a frente: 90 graus do LIDAR (horario) = direita do AGV.
        pontos, angulos = geometria.para_agv([0.0, 90.0, 180.0], [1.0, 1.0, 1.0])
        por_angulo = {round(math.degrees(a)): p for a, p in zip(angulos, pontos)}
        np.testing.assert_allclose(por_angulo[0], [1.0, 0.0], atol=1e-9)
        np.testing.assert_allclose(por_angulo[-90], [0.0, -1.0], atol=1e-9)
        np.testing.assert_allclose(por_angulo[180], [-1.0, 0.0], atol=1e-9)

    def test_montagem_girada_e_deslocada(self):
        montagem = geometria.Montagem(frente_graus=180.0, x_m=0.10)
        pontos, _ = geometria.para_agv([180.0], [1.0], montagem)
        np.testing.assert_allclose(pontos[0], [1.10, 0.0], atol=1e-9)

    def test_filtra_alcance_e_mascara(self):
        montagem = geometria.Montagem(min_m=0.15, max_m=5.0, mascara=geometria.ler_mascara("170:190, lixo, 350:10"))
        pontos, _ = geometria.para_agv([0.0, 5.0, 90.0, 180.0, 270.0, 270.0], [1.0, 1.0, 1.0, 1.0, 0.05, 9.0], montagem)
        # 0 e 5 graus caem na mascara que passa pelo zero; 180 na outra; os dois ultimos fora do alcance
        np.testing.assert_allclose(pontos, [[0.0, -1.0]], atol=1e-9)

    def test_saida_ordenada_por_angulo(self):
        _, angulos = geometria.para_agv([300.0, 10.0, 200.0, 90.0], [1.0] * 4)
        graus = np.degrees(angulos) % 360
        self.assertTrue(np.all(np.diff(graus) > 0))

    def test_folgas_medidas_da_borda_do_chassi(self):
        pontos = np.array([
            [1.30, 0.00],    # frente: 1,00 m alem do para-choque
            [0.00, 0.45],    # esquerda: 0,20 m alem da lateral
            [0.00, -0.35],   # direita: 0,10 m
            [-0.80, 0.10],   # tras: 0,50 m
            [0.60, 0.55],    # diagonal frente-esquerda: hipot(0,3; 0,3)
        ])
        folgas = geometria.folgas(pontos, CORPO)
        self.assertAlmostEqual(folgas["frente"], 1.0)
        self.assertAlmostEqual(folgas["esquerda"], 0.20)
        self.assertAlmostEqual(folgas["direita"], 0.10)
        self.assertAlmostEqual(folgas["tras"], 0.50)
        self.assertAlmostEqual(folgas["frente_esq"], round(math.hypot(0.3, 0.3), 3))
        self.assertIsNone(folgas["tras_dir"])

    def test_ponto_dentro_do_chassi_e_ignorado(self):
        self.assertTrue(all(v is None for v in geometria.folgas(np.array([[0.1, 0.1]]), CORPO).values()))


class TestThreadDoLidar(unittest.TestCase):
    def test_le_voltas_e_publica_no_estado(self):
        class SerialDeVoltas:
            def __init__(self, blocos):
                self.blocos = list(blocos)
                self.in_waiting = 0

            def read(self, _tamanho):
                return self.blocos.pop(0) if self.blocos else b""

        blocos = [volta(2.0), volta(1.0), no(0.0, 1.0, inicio=True)]
        with estado_isolado(), patch.object(estado, "stop_event", EventoDeParada(voltas=3)):
            estado.lidar_state.update(ok=False, voltas=0)
            rplidar._ler_voltas(SerialDeVoltas(blocos))
            self.assertTrue(estado.lidar_state["ok"])
            self.assertEqual(estado.lidar_state["voltas"], 2)
            leitura = rplidar.leitura_recente()
            self.assertIsNotNone(leitura)
            self.assertEqual(len(leitura["pontos"]), 180)
            # a ultima volta tem tudo a 1 m do laser (no eixo das rodas): a folga a frente e a
            # do ponto mais perto dentro do corredor da largura do AGV
            corpo = rplidar.CORPO
            meio_angulo = math.asin(corpo.largura_m / 2.0)
            esperado = math.cos(meio_angulo) - corpo.frente_m
            self.assertAlmostEqual(leitura["folgas"]["frente"], esperado, delta=0.02)

    def test_sem_dados_reconecta(self):
        class SerialMuda:
            in_waiting = 0

            def read(self, _tamanho):
                return b""

        relogio = iter([0.0, 0.0, 5.0])
        with estado_isolado(), patch.object(estado, "stop_event", EventoDeParada(voltas=5)), \
                patch.object(util, "now", side_effect=lambda: next(relogio)):
            with self.assertRaises(RuntimeError):
                rplidar._ler_voltas(SerialMuda())

    def test_volta_velha_nao_vale(self):
        with estado_isolado():
            publicar_folgas(frente=1.0)
            estado.lidar_state["at"] = util.now() - 10.0
            self.assertIsNone(rplidar.leitura_recente())

    def test_status_sem_lidar(self):
        with estado_isolado():
            estado.lidar_state.update(ok=False, erro="nenhum RPLIDAR encontrado", pontos=None)
            resumo = rplidar.status_snapshot()
            self.assertFalse(resumo["online"])
            self.assertEqual(resumo["pontos"], 0)
            self.assertEqual(rplidar.varredura_publica()["lista"], [])


class SerialDoLidar:
    """Responde ao GET_INFO, ao GET_HEALTH e ao SCAN como um RPLIDAR A1."""

    def __init__(self, saude: int = 0, confirma_scan: bool = True):
        self.respostas = [
            b"\xa5\x5a\x14\x00\x00\x00\x04", bytes([0x18, 24, 1, 5]) + bytes(16),
            b"\xa5\x5a\x03\x00\x00\x00\x06", bytes([saude, 0, 0]),
            b"\xa5\x5a\x05\x00\x00\x40" + (b"\x81" if confirma_scan else b"\x00"),
        ]
        self.escrito = []
        self.dtr = []
        self.fechada = False

    def read(self, _tamanho):
        return self.respostas.pop(0) if self.respostas else b""

    def write(self, dados):
        self.escrito.append(bytes(dados))

    def flush(self):
        pass

    def reset_input_buffer(self):
        pass

    def setDTR(self, valor):  # noqa: N802 - mesmo nome da API do pyserial
        self.dtr.append(valor)

    def close(self):
        self.fechada = True


class TestAbrirLidar(unittest.TestCase):
    def _abrir(self, falsa):
        with patch("serial.Serial", return_value=falsa), patch.object(rplidar.time, "sleep"):
            return rplidar._abrir("/dev/rplidar")

    def test_identifica_liga_o_motor_e_comeca_a_varredura(self):
        falsa = SerialDoLidar()
        ser, info = self._abrir(falsa)
        self.assertIs(ser, falsa)
        self.assertEqual((info["firmware"], info["hardware"], info["saude"]), ("1.24", 5, "ok"))
        self.assertIn(False, falsa.dtr)                       # A1: DTR baixo liga o motor
        self.assertIn(rplidar.comando_pwm(rplidar.LIDAR_MOTOR_PWM), falsa.escrito)  # A2/A3
        self.assertEqual(falsa.escrito[-1], rplidar.comando(rplidar.CMD_SCAN))

    def test_saude_com_erro_reinicia_o_lidar(self):
        falsa = SerialDoLidar(saude=2)
        self._abrir(falsa)
        self.assertIn(rplidar.comando(rplidar.CMD_RESET), falsa.escrito)

    def test_sem_confirmacao_da_varredura_desliga_e_fecha(self):
        falsa = SerialDoLidar(confirma_scan=False)
        with self.assertRaises(RuntimeError):
            self._abrir(falsa)
        self.assertTrue(falsa.fechada)
        self.assertEqual(falsa.dtr[-1], True)                 # motor desligado

    def test_sem_resposta_nao_e_lidar(self):
        falsa = SerialDoLidar()
        falsa.respostas = []
        with self.assertRaises(RuntimeError):
            self._abrir(falsa)


class TestPortaDoArduino(unittest.TestCase):
    def test_arduino_nao_abre_a_porta_do_lidar(self):
        with patch.object(rplidar, "porta_reservada", return_value=os.path.realpath("/dev/ttyUSB1")), \
                patch.dict(os.environ, {"AGV_ARDUINO_PORT": ""}), \
                patch("glob.glob", side_effect=lambda padrao: ["/dev/ttyUSB0", "/dev/ttyUSB1"] if "ttyUSB" in padrao else []), \
                patch("serial.tools.list_ports.comports", return_value=[]):
            self.assertEqual(arduino._candidate_arduino_ports(), ["/dev/ttyUSB0"])

    def test_arduino_nunca_tenta_as_portas_da_placa_mae(self):
        info = type("Porta", (), {"device": "/dev/ttyS9"})()
        with patch.object(rplidar, "porta_reservada", return_value=None), \
                patch.dict(os.environ, {"AGV_ARDUINO_PORT": ""}), \
                patch("glob.glob", side_effect=lambda padrao: ["/dev/ttyUSB0"] if "ttyUSB" in padrao else []), \
                patch("serial.tools.list_ports.comports", return_value=[info]):
            self.assertEqual(arduino._candidate_arduino_ports(), ["/dev/ttyUSB0"])


class TestAutonomoComLidar(unittest.TestCase):
    def _comando(self, folgas, **kinect):
        base = {"kinect_ok": True, "distance_m": 2.0, "left_clearance_m": 2.0,
                "center_clearance_m": 2.0, "right_clearance_m": 2.0}
        base.update(kinect)
        with estado_isolado():
            estado.state.update(base)
            publicar_folgas(**folgas)
            return controle.compute_autopilot_command()

    def test_lidar_cobre_a_zona_cega_do_kinect(self):
        # Kinect sem leitura (objeto a menos de 0,5 m): o LIDAR ve a 0,20 m.
        speed, _, reason = self._comando({"frente": 0.20}, center_clearance_m=None, distance_m=None)
        self.assertEqual((speed, reason), (0, "obstaculo-muito-perto"))

    def test_setor_vazio_do_lidar_e_livre(self):
        self.assertEqual(self._comando({}), (48, 0, "livre"))

    def test_nao_gira_para_o_lado_colado_na_parede(self):
        # A diagonal direita parece mais livre, mas o chassi esta encostado a direita.
        speed, steering, _ = self._comando({"frente": 0.20, "frente_esq": 0.40, "frente_dir": 1.50, "direita": 0.05})
        self.assertEqual((speed, steering), (0, -70))

    def test_encurralado_da_re_se_a_traseira_esta_livre(self):
        folgas = {"frente": 0.20, "frente_esq": 0.25, "frente_dir": 0.25, "tras": 1.0}
        self.assertEqual(self._comando(folgas), (-controle.RE_VELOCIDADE, 0, "re-encurralado"))

    def test_encurralado_com_algo_atras_nao_da_re(self):
        folgas = {"frente": 0.20, "frente_esq": 0.25, "frente_dir": 0.25, "tras": 0.10}
        speed, _, reason = self._comando(folgas)
        self.assertEqual((speed, reason), (0, "obstaculo-muito-perto"))

    def test_rente_a_parede_segue_reto_e_devagar(self):
        # virar para longe da parede faria a traseira varre-la (curva fechada no firmware)
        self.assertEqual(self._comando({"esquerda": 0.05}), (30, 0, "rente-a-esquerda"))
        self.assertEqual(self._comando({"direita": 0.05}), (30, 0, "rente-a-direita"))

    def test_sem_kinect_para_mesmo_com_lidar(self):
        self.assertEqual(self._comando({}, kinect_ok=False), (0, 0, "sem-kinect"))


class TestEspacoDeGiro(unittest.TestCase):
    """Os cantos do chassi descrevem um circulo ao girar no lugar (raio ~0,39 m)."""

    def _comando(self, pontos, folgas, **kinect):
        base = {"kinect_ok": True, "distance_m": 2.0, "left_clearance_m": 2.0,
                "center_clearance_m": 2.0, "right_clearance_m": 2.0}
        base.update(kinect)
        estado.state.update(base)
        estado.lidar_state.update(ok=True, at=util.now(), pontos=np.array(pontos, dtype=float),
                                  folgas={**{s: None for s in geometria.SETORES}, **folgas})
        return controle.compute_autopilot_command()

    def test_espaco_de_giro_ignora_o_proprio_chassi(self):
        raio = math.hypot(0.30, 0.25)
        self.assertIsNone(geometria.espaco_de_giro(np.array([[0.1, 0.1]]), CORPO))
        self.assertAlmostEqual(geometria.espaco_de_giro(np.array([[0.1, 0.1], [0.0, 0.60]]), CORPO), 0.60 - raio)
        with patch.object(rplidar, "CORPO", CORPO):
            self.assertTrue(rplidar.pode_girar(np.array([[0.0, 0.60]])))
            self.assertFalse(rplidar.pode_girar(np.array([[0.0, 0.40]])))

    def test_sem_espaco_para_girar_recua_e_continua_recuando(self):
        parede_lateral = [[x, 0.33] for x in np.arange(-1.0, 1.0, 0.05)]
        with estado_isolado(), patch.object(rplidar, "CORPO", CORPO):
            # frente fechada e parede a 8 cm da lateral: girar rasparia o canto
            self.assertEqual(self._comando(parede_lateral + [[0.5, 0.0]], {"frente": 0.20, "esquerda": 0.08, "tras": 2.0}),
                             (-controle.RE_VELOCIDADE, 0, "re-sem-espaco-para-girar"))
            # a frente "abriu" com a re, mas ainda nao ha espaco: continua recuando
            self.assertEqual(self._comando(parede_lateral, {"esquerda": 0.08, "tras": 2.0}),
                             (-controle.RE_VELOCIDADE, 0, "recuando"))
            # saiu do aperto: volta a dirigir
            self.assertEqual(self._comando([[0.0, 1.5]], {"tras": 2.0})[2], "livre")

    def test_sem_espaco_e_sem_re_para(self):
        apertado = [[0.0, 0.33], [0.0, -0.33], [0.5, 0.0], [-0.5, 0.0]]
        with estado_isolado(), patch.object(rplidar, "CORPO", CORPO):
            self.assertEqual(self._comando(apertado, {"frente": 0.20, "tras": 0.20}), (0, 0, "sem-saida"))

    def test_corredor_estreito_segue_reto(self):
        corredor = [[x, y] for x in np.arange(-1.0, 1.5, 0.05) for y in (0.36, -0.36)]
        with estado_isolado(), patch.object(rplidar, "CORPO", CORPO):
            self.assertEqual(self._comando(corredor, {"frente": 0.80, "frente_esq": 0.40, "frente_dir": 1.0}),
                             (18, 0, "estreito-em-frente"))

    def test_re_ignora_as_paredes_laterais_do_corredor(self):
        # Num beco, as diagonais traseiras veem as paredes do corredor; a re so precisa do corredor atras.
        folgas = {"frente": 0.20, "frente_esq": 0.20, "frente_dir": 0.20, "tras": 1.5, "tras_esq": 0.10, "tras_dir": 0.10}
        with estado_isolado():
            publicar_folgas(**folgas)
            estado.state.update(kinect_ok=True, distance_m=2.0, left_clearance_m=2.0, center_clearance_m=2.0,
                                right_clearance_m=2.0)
            self.assertEqual(controle.compute_autopilot_command()[0], -controle.RE_VELOCIDADE)


class TestSegurancaDaIaComLidar(unittest.TestCase):
    def test_re_bloqueada_com_obstaculo_atras(self):
        self.assertEqual(politica.aplicar_seguranca(-20, 10, 3.0, 100, tras_m=0.20), (0, 10, "ia-bloqueio-tras"))
        self.assertEqual(politica.aplicar_seguranca(-20, 10, 3.0, 100, tras_m=1.0), (-20, 10, "ia"))
        # sem LIDAR a re continua liberada (lenta)
        self.assertEqual(politica.aplicar_seguranca(-20, 10, 3.0, 100), (-20, 10, "ia"))

    def test_sem_espaco_para_girar_a_ia_anda_reto(self):
        self.assertEqual(politica.aplicar_seguranca(-20, 60, 3.0, 100, tras_m=1.0, pode_girar=False),
                         (-20, 0, "ia-sem-espaco-para-girar"))
        self.assertEqual(politica.aplicar_seguranca(0, 60, 3.0, 100, pode_girar=False),
                         (0, 0, "ia-sem-espaco-para-girar"))
        # e a re reta continua conferida
        self.assertEqual(politica.aplicar_seguranca(-20, 60, 3.0, 100, tras_m=0.1, pode_girar=False),
                         (0, 0, "ia-bloqueio-tras"))


class TestMapaComLidar(unittest.TestCase):
    def _sala(self, deslocamento_x: float = 0.0):
        """Volta do laser numa sala retangular de 4 x 3 m (o AGV na posicao dada)."""
        angulos, distancias = [], []
        for graus in np.arange(0.0, 360.0, 1.5):
            rad = math.radians(-graus)
            dx, dy = math.cos(rad), math.sin(rad)
            alcance = []
            for parede, comp in ((2.0 - deslocamento_x, dx), (-2.0 - deslocamento_x, dx)):
                if comp != 0 and parede / comp > 0:
                    alcance.append(parede / comp)
            for parede, comp in ((1.5, dy), (-1.5, dy)):
                if comp != 0 and parede / comp > 0:
                    alcance.append(parede / comp)
            angulos.append(graus)
            distancias.append(min(alcance))
        pontos, _ = geometria.para_agv(angulos, distancias)
        return pontos

    def test_mapeia_com_o_lidar_e_volta_ao_kinect_sem_ele(self):
        with mapeamento_isolado(), estado_isolado():
            estado.state.update(kinect_ok=False, depth=None)
            servico_mapa.ligar()
            for i, x in enumerate((0.0, 0.05, 0.10)):
                estado.lidar_state.update(ok=True, at=100.0 + i, pontos=self._sala(x), folgas={})
                self.assertTrue(servico_mapa.passo(agora=100.0 + i))
            detalhe = servico_mapa.status_detalhado()
            self.assertEqual(detalhe["fonte"], "lidar")
            self.assertGreater(detalhe["area_m2"], 5.0)
            self.assertAlmostEqual(detalhe["pose"]["x"], 0.10, delta=0.03)
            # a mesma volta nao e processada duas vezes
            self.assertFalse(servico_mapa.passo(agora=102.1))


class TestWebcamGenerica(unittest.TestCase):
    def test_usa_a_webcam_do_notebook_sem_zed_e_nunca_o_kinect(self):
        with tempfile.TemporaryDirectory() as raiz:
            for node, nome, indice in (("video0", "gspca main driver", 0),
                                       ("video1", "Integrated_Webcam_HD: Integrate", 0),
                                       ("video2", "Integrated_Webcam_HD: Integrate", 1)):
                os.makedirs(os.path.join(raiz, node))
                with open(os.path.join(raiz, node, "name"), "w") as f:
                    f.write(nome)
                with open(os.path.join(raiz, node, "index"), "w") as f:
                    f.write(str(indice))
            with patch.object(zed.glob, "glob", return_value=sorted(
                    os.path.join(raiz, n, "name") for n in ("video0", "video1", "video2"))), \
                    patch.object(zed.os.path, "exists", return_value=True):
                self.assertEqual(zed._zed_nodes_from_sysfs(), ["/dev/video1"])
                with patch.object(zed, "ZED_WEBCAM_GENERICA", False):
                    self.assertEqual(zed._zed_nodes_from_sysfs(), [])


class TestArquivoEnv(unittest.TestCase):
    def test_le_e_nao_sobrescreve_o_ambiente(self):
        with tempfile.TemporaryDirectory() as pasta:
            caminho = os.path.join(pasta, "agv.env")
            with open(caminho, "w", encoding="utf-8") as f:
                f.write("# comentario\nAGV_TESTE_A=1\nexport AGV_TESTE_B=\"com espaco\"\nlinha invalida\nAGV_TESTE_C=3\n")
            with patch.dict(os.environ, {"AGV_TESTE_C": "do ambiente"}):
                os.environ.pop("AGV_TESTE_A", None)
                os.environ.pop("AGV_TESTE_B", None)
                aplicadas = arquivo_env.carregar(caminho)
                self.assertEqual(sorted(aplicadas), ["AGV_TESTE_A", "AGV_TESTE_B"])
                self.assertEqual(os.environ["AGV_TESTE_B"], "com espaco")
                self.assertEqual(os.environ["AGV_TESTE_C"], "do ambiente")
            self.assertEqual(arquivo_env.carregar(os.path.join(pasta, "nao-existe.env")), [])



class TestCalibracaoDaMontagem(unittest.TestCase):
    """Kinect e laser simulados na mesma sala, com o laser montado girado e deslocado."""

    def _varreduras(self, frente_graus: float, lidar_xy, pose_kinect):
        segmentos = sala()
        kinect = varredura.varredura(quadro(pose_kinect, segmentos), True)
        # Com AGV_LIDAR_FRENTE_GRAUS = F, o angulo F do laser (horario) aponta para a
        # frente: o laser esta girado +F (anti-horario) em relacao ao AGV.
        pose_laser = compor(pose_kinect, Pose(lidar_xy[0], lidar_xy[1], math.radians(frente_graus)))
        rng = np.random.default_rng(0)
        voltas = []
        for inicio in (0.0, 0.75, 1.5, 2.25):  # como a ferramenta: 4 voltas, cada uma comeca num angulo
            angulos = np.arange(inicio, 360.0, 3.0)
            distancias = raios(np.array([pose_laser.x, pose_laser.y]), pose_laser.theta - np.radians(angulos), segmentos)
            voltas.append(geometria.para_agv(angulos, distancias + rng.normal(0.0, 0.01, distancias.shape))[0])
        return kinect, np.vstack(voltas)

    def test_recupera_angulo_e_posicao_do_laser(self):
        kinect, laser = self._varreduras(120.0, (-0.25, 0.05), Pose(0.0, -0.3, math.radians(10)))
        r = calibracao.calibrar(kinect, laser)
        self.assertTrue(r.confiavel, r)
        self.assertLess(abs((r.frente_graus - 120.0 + 180) % 360 - 180), 2.0)
        self.assertAlmostEqual(r.lidar_x_m, -0.25, delta=0.04)
        self.assertAlmostEqual(r.lidar_y_m, 0.05, delta=0.04)

    def test_parede_reta_e_ambigua(self):
        _, laser = self._varreduras(0.0, (0.0, 0.0), Pose(0.0, 0.0, 0.0))
        parede = np.column_stack([np.full(40, 2.0), np.linspace(-0.6, 0.6, 40)])
        self.assertFalse(calibracao.calibrar(parede, laser).confiavel)


class TestRotaLidar(unittest.TestCase):
    def test_radar_e_status(self):
        from pc_agv.web import rotas

        with estado_isolado():
            estado.lidar_state.update(ok=True, at=util.now(), hz=7.0, pontos=np.array([[1.0, 0.0], [0.0, -2.0]]),
                                      folgas={"frente": 0.7})
            cliente = rotas.app.test_client()
            radar = cliente.get("/api/lidar").get_json()["lidar"]
            self.assertTrue(radar["online"])
            self.assertEqual(radar["lista"], [[1.0, 0.0], [0.0, -2.0]])
            self.assertIn("largura_m", radar["corpo"])
            status = cliente.get("/api/status").get_json()
            self.assertTrue(status["lidar"]["online"])
            self.assertEqual(status["lidar"]["folgas"]["frente"], 0.7)


if __name__ == "__main__":
    unittest.main()
