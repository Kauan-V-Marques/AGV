import json
import math
import unittest
from unittest.mock import patch

import numpy as np
from apoio import EventoDeParada, estado_isolado, mapeamento_isolado
from simulador_kinect import caixa, giro, quadro, reta, sala

from pc_agv import estado
from pc_agv.mapeamento import geometria, servico, varredura
from pc_agv.mapeamento.banco import BancoMapas, trajetoria_de_bytes, trajetoria_para_bytes
from pc_agv.mapeamento.geometria import Pose, Restricao, compor, icp, icp_robusto, inverter, transformar
from pc_agv.mapeamento.grade import Grade
from pc_agv.mapeamento.odometria import Odometria


def erro_pose(a: Pose, b: Pose) -> tuple[float, float]:
    """Distancia (m) e diferenca de angulo (graus) entre duas poses."""
    dtheta = math.degrees(abs(geometria.angulo_normalizado(a.theta - b.theta)))
    return math.hypot(a.x - b.x, a.y - b.y), dtheta


def percorrer(poses, segmentos=None, avanco_m=0.0, ruido=True, em_movimento=True):
    segmentos = sala() if segmentos is None else segmentos
    rng = np.random.default_rng(5)
    odometria = Odometria(pose=poses[0], avanco_m=avanco_m)
    falhas = 0
    for pose in poses:
        falhas += not odometria.atualizar(varredura.varredura(quadro(pose, segmentos, ruido, rng), True), em_movimento)
    return odometria, falhas


# ── Geometria ───────────────────────────────────────────────────────
class TestGeometria(unittest.TestCase):
    def test_compor_e_inverter(self):
        a = Pose(1.0, 2.0, math.radians(30))
        b = Pose(-0.5, 0.2, math.radians(-75))
        volta = compor(compor(a, b), inverter(b))
        self.assertLess(erro_pose(volta, a)[0], 1e-9)
        self.assertLess(erro_pose(compor(a, inverter(a)), Pose())[1], 1e-9)

    def test_transformar_gira_e_desloca(self):
        np.testing.assert_allclose(transformar(np.array([[1.0, 0.0]]), Pose(2.0, 1.0, math.pi / 2)), [[2.0, 2.0]], atol=1e-12)

    def test_normais_de_uma_parede_e_ponto_isolado(self):
        parede = np.column_stack([np.full(10, 2.0), np.linspace(-0.5, 0.5, 10)])
        pontos = np.vstack([parede, [[4.0, 3.0]]])
        normais = geometria.normais(pontos)
        np.testing.assert_allclose(np.abs(normais[:10, 0]), 1.0, atol=1e-9)  # parede em x = 2: normal em x
        self.assertTrue(np.isnan(normais[10]).all())

    def test_vizinhos_igual_a_forca_bruta(self):
        rng = np.random.default_rng(0)
        a, b = rng.uniform(-3, 3, (40, 2)), rng.uniform(-3, 3, (55, 2))
        indice, distancia = geometria.vizinhos(a, b)
        bruta = np.linalg.norm(a[:, None, :] - b[None, :, :], axis=2)
        np.testing.assert_array_equal(indice, bruta.argmin(axis=1))
        np.testing.assert_allclose(distancia, bruta.min(axis=1), atol=1e-9)

    def test_icp_recupera_um_passo_pequeno(self):
        segmentos = sala()
        chave = varredura.varredura(quadro(Pose(), segmentos), True)
        real = Pose(0.12, 0.02, math.radians(4))
        resultado = icp(varredura.varredura(quadro(real, segmentos), True), chave)
        distancia, angulo = erro_pose(resultado.pose, real)
        self.assertTrue(resultado.ok)
        self.assertLess(distancia, 0.01)
        self.assertLess(angulo, 0.3)

    def test_icp_robusto_acha_um_giro_grande(self):
        segmentos = sala()
        chave = varredura.varredura(quadro(Pose(1.5, 0.0, 0.0), segmentos), True)
        real = Pose(1.6, 0.03, math.radians(14))
        relativo = compor(inverter(Pose(1.5, 0.0, 0.0)), real)
        resultado = icp_robusto(varredura.varredura(quadro(real, segmentos), True), chave)
        distancia, angulo = erro_pose(resultado.pose, relativo)
        self.assertLess(distancia, 0.02)
        self.assertLess(angulo, 0.5)

    def test_icp_com_poucos_pontos_falha(self):
        self.assertFalse(icp(np.zeros((3, 2)), np.zeros((50, 2))).ok)

    def test_restricao_tira_o_deslocamento_lateral(self):
        restricao = Restricao(Pose(), 0.0)
        sem_lateral = geometria._sem_lateral(Pose(0.10, 0.07, 0.0), restricao)
        self.assertAlmostEqual(sem_lateral.x, 0.10)
        self.assertAlmostEqual(sem_lateral.y, 0.0)

    def test_restricao_considera_o_kinect_a_frente_do_eixo(self):
        # Girando no lugar, o Kinect 20 cm a frente do eixo anda de lado; o eixo nao.
        kinect_girado = compor(Pose(0.0, 0.0, math.radians(10)), Pose(0.20, 0.0, 0.0))
        lateral, _ = geometria._lateral(kinect_girado, Restricao(Pose(0.20, 0.0, 0.0), 0.20))
        self.assertAlmostEqual(lateral, 0.0, places=9)


# ── Varredura ───────────────────────────────────────────────────────
class TestVarredura(unittest.TestCase):
    def test_parede_a_frente(self):
        depth = np.full((480, 640), 2000, np.uint16)  # parede a 2 m
        pontos = varredura.varredura(depth, True)
        self.assertEqual(len(pontos), varredura.RAIOS)
        np.testing.assert_allclose(pontos[:, 0], 2.0, atol=1e-6)
        self.assertGreater(pontos[0, 1], 0.9)       # coluna da esquerda da imagem: y positivo
        self.assertLess(pontos[-1, 1], -0.9)
        self.assertAlmostEqual(float(np.abs(pontos[:, 1]).min()), 0.0, delta=0.02)

    def test_fora_do_alcance_e_descartado(self):
        depth = np.full((480, 640), 2000, np.uint16)
        depth[:, :320] = 300    # perto demais para o Kinect
        depth[:, 320:400] = 6000  # longe demais
        pontos = varredura.varredura(depth, True)
        self.assertTrue(np.all(pontos[:, 1] < 0))  # so sobrou a parte da direita da imagem
        self.assertEqual(len(pontos), (640 - 400) // 4)

    def test_pixel_de_ruido_nao_vira_obstaculo(self):
        depth = np.full((480, 640), 2000, np.uint16)
        depth[230, 300] = 800
        np.testing.assert_allclose(varredura.varredura(depth, True)[:, 0], 2.0, atol=1e-6)

    def test_quadro_reduzido_usa_a_mesma_geometria(self):
        cheio = varredura.varredura(quadro(Pose(), sala()), True)
        metade = varredura.varredura(quadro(Pose(), sala(), largura=320, altura=240), True)
        self.assertLess(abs(np.median(cheio[:, 1]) - np.median(metade[:, 1])), 0.05)


# ── Mapa de ocupacao ────────────────────────────────────────────────
class TestGrade(unittest.TestCase):
    def test_parede_ocupada_caminho_livre_e_atras_desconhecido(self):
        grade = Grade(6.0, 0.05)
        parede = np.column_stack([np.full(40, 2.0), np.linspace(-0.5, 0.5, 40)])
        for _ in range(3):
            grade.integrar(Pose(), parede)

        def celula(x, y):
            linha, coluna, _ = grade.celulas(np.array([[x, y]]))
            return grade.logodds[linha[0], coluna[0]]

        self.assertGreater(celula(2.0, 0.0), 1.0)    # parede
        self.assertLess(celula(1.0, 0.0), -0.5)      # entre o AGV e a parede
        self.assertEqual(celula(2.5, 0.0), 0.0)      # atras da parede: nunca visto
        self.assertGreater(grade.area_conhecida_m2(), 1.0)
        self.assertEqual(grade.versao, 3)

    def test_recorte_imagem_e_persistencia(self):
        grade = Grade(6.0, 0.05)
        self.assertIsNone(grade.recorte())
        grade.integrar(Pose(), np.column_stack([np.full(20, 1.5), np.linspace(-0.4, 0.4, 20)]))
        recorte = grade.recorte(margem_m=0.5)
        self.assertLessEqual(recorte["x_min"], -0.4)
        imagem = grade.imagem_rgba(recorte)
        self.assertEqual(imagem.shape, (recorte["linha1"] - recorte["linha0"], recorte["coluna1"] - recorte["coluna0"], 4))
        self.assertEqual(imagem[0, 0, 3], 0)  # canto nunca visto: transparente
        json.dumps(recorte)  # vai para a API: so tipos nativos
        copia = Grade.de_bytes(grade.para_bytes())
        np.testing.assert_allclose(copia.logodds, grade.logodds, atol=1e-2)
        with self.assertRaises(ValueError):
            Grade.de_bytes(b"lixo")


# ── Odometria visual (sala simulada com ruido do Kinect) ────────────
class TestOdometria(unittest.TestCase):
    def test_parado_nao_acumula_erro(self):
        odometria, falhas = percorrer([Pose(1.0, 0.2, 0.3)] * 25)
        self.assertEqual(falhas, 0)
        self.assertLess(erro_pose(odometria.pose, Pose(1.0, 0.2, 0.3))[0], 0.01)
        self.assertLess(odometria.distancia_m, 0.05)

    def test_reta_e_giro(self):
        caminho = [Pose()] + reta(Pose(), 20, 0.075)
        caminho += giro(caminho[-1], 12, 7.5)
        caminho += reta(caminho[-1], 12, 0.075)
        odometria, falhas = percorrer(caminho)
        distancia, angulo = erro_pose(odometria.pose, caminho[-1])
        self.assertLess(falhas, 3)
        self.assertLess(distancia, 0.08)
        self.assertLess(angulo, 3.0)
        self.assertAlmostEqual(odometria.distancia_m, 2.4, delta=0.15)

    def test_curvas_com_kinect_a_frente_do_eixo(self):
        segmentos = np.array(caixa(-3.0, -2.5, 3.0, 2.5) + caixa(-0.3, 1.9, 0.4, 2.3)
                             + caixa(1.8, 1.2, 2.2, 1.8) + caixa(-2.2, -1.9, -1.6, -1.5), dtype=float)
        eixos = []
        for k in range(48):  # um circulo de raio 1,2 m
            a = 2 * math.pi * k / 48
            eixos.append(Pose(1.2 - 1.2 * math.cos(a), 1.2 * math.sin(a), math.pi / 2 - a))
        kinect = [compor(e, Pose(0.20, 0.0, 0.0)) for e in eixos]
        odometria, falhas = percorrer(kinect, segmentos, avanco_m=0.20)
        distancia, angulo = erro_pose(odometria.pose, kinect[-1])
        self.assertEqual(falhas, 0)
        self.assertLess(distancia, 0.10)
        self.assertLess(angulo, 2.0)

    def test_vista_tapada_em_movimento_segue_pelo_ultimo_movimento(self):
        segmentos = sala()
        caminho = [Pose(1.5, 0.0, 0.0)] + giro(Pose(1.5, 0.0, 0.0), 6, 7.5)
        odometria, _ = percorrer(caminho, segmentos)
        antes = odometria.pose
        for _ in range(3):  # tres quadros sem leitura (algo colado no Kinect)
            self.assertFalse(odometria.atualizar(np.zeros((0, 2)), em_movimento=True))
        self.assertAlmostEqual(math.degrees(odometria.pose.theta - antes.theta), 22.5, delta=2.0)
        self.assertTrue(odometria.prevendo)

    def test_vista_tapada_parado_mantem_a_pose(self):
        odometria, _ = percorrer([Pose()] + reta(Pose(), 4, 0.075))
        antes = odometria.pose
        for _ in range(3):
            odometria.atualizar(np.zeros((0, 2)), em_movimento=False)
        self.assertEqual(odometria.pose, antes)

    def test_salto_impossivel_e_recusado(self):
        odometria, _ = percorrer([Pose()] + reta(Pose(), 3, 0.075))
        antes = odometria.pose
        longe = varredura.varredura(quadro(Pose(3.0, -1.0, math.radians(120)), sala()), True)
        self.assertFalse(odometria.atualizar(longe, em_movimento=False))
        self.assertEqual(odometria.pose, antes)


# ── Banco dos mapas ─────────────────────────────────────────────────
class TestBancoMapas(unittest.TestCase):
    def test_salvar_atualizar_listar_carregar_apagar(self):
        with mapeamento_isolado(abrir=False) as caminho:
            banco = BancoMapas(caminho)
            try:
                trajetoria = trajetoria_para_bytes([(0.0, 0.0), (0.5, 0.1)])
                mapa_id = banco.salvar("sala", b"grade", trajetoria, 0.05, 0.5, 3.2)
                self.assertEqual(banco.salvar("sala 2", b"grade2", trajetoria, 0.05, 0.9, 4.0, mapa_id=mapa_id), mapa_id)
                self.assertEqual([m["nome"] for m in banco.listar()], ["sala 2"])
                mapa = banco.carregar(mapa_id)
                self.assertEqual(mapa["grade"], b"grade2")
                np.testing.assert_allclose(trajetoria_de_bytes(mapa["trajetoria"]), [[0.0, 0.0], [0.5, 0.1]], atol=1e-6)
                # id que nao existe mais vira um mapa novo
                self.assertNotEqual(banco.salvar("outro", b"g", trajetoria, 0.05, 0, 0, mapa_id=999), 999)
                self.assertTrue(banco.apagar(mapa_id))
                self.assertFalse(banco.apagar(mapa_id))
                self.assertIsNone(banco.carregar(mapa_id))
                banco.gravar_preferencia("avanco_cm", 18)
                self.assertEqual(banco.ler_preferencia("avanco_cm", 0), 18)
            finally:
                banco.fechar()


# ── Servico ─────────────────────────────────────────────────────────
class TestServicoMapa(unittest.TestCase):
    def _mapear(self, poses, agora=100.0, segmentos=None):
        segmentos = sala() if segmentos is None else segmentos
        rng = np.random.default_rng(2)
        for pose in poses:
            estado.state.update({"kinect_ok": True, "depth": quadro(pose, segmentos, True, rng),
                                 "depth_mm": True, "depth_at": agora})
            estado.control_state.update({"active_speed": 40, "active_steering": 0})
            servico.passo(agora)
            agora += 0.25

    def test_so_mapeia_ligado(self):
        with estado_isolado(), mapeamento_isolado():
            self._mapear([Pose()])
            self.assertEqual(servico.status_detalhado()["leituras"], 0)
            self.assertEqual(servico.ligar(), (True, "ligado"))
            self._mapear([Pose()] + reta(Pose(), 8, 0.075))
            status = servico.status_detalhado()
            self.assertEqual(status["leituras"], 9)
            self.assertGreater(status["area_m2"], 3.0)
            self.assertAlmostEqual(status["pose"]["x"], 0.6, delta=0.05)
            self.assertGreaterEqual(len(status["trajetoria"]), 8)
            self.assertTrue(status["varredura"])
            json.dumps(status)
            json.dumps(servico.resumo())

    def test_quadro_velho_ou_sem_kinect_nao_entra(self):
        with estado_isolado(), mapeamento_isolado():
            servico.ligar()
            estado.state.update({"kinect_ok": True, "depth": quadro(Pose(), sala()), "depth_mm": True, "depth_at": 10.0})
            self.assertFalse(servico.passo(20.0))
            estado.state["kinect_ok"] = False
            self.assertFalse(servico.passo(10.0))
            self.assertEqual(servico.status_detalhado()["leituras"], 0)

    def test_salvar_abrir_e_novo(self):
        with estado_isolado(), mapeamento_isolado():
            self.assertEqual(servico.salvar(""), (False, "nome_obrigatorio"))
            self.assertEqual(servico.salvar("vazio"), (False, "mapa_vazio"))
            servico.ligar()
            self._mapear([Pose()] + reta(Pose(), 6, 0.075))
            ok, mapa_id = servico.salvar("  Laboratorio  ")
            self.assertTrue(ok)
            self.assertEqual(servico.salvar("Laboratorio 2"), (True, mapa_id))  # mesmo mapa: atualiza
            self.assertEqual([m["nome"] for m in servico.listar()], ["Laboratorio 2"])
            area = servico.status_detalhado()["area_m2"]

            servico.novo()
            self.assertEqual(servico.status_detalhado()["area_m2"], 0.0)
            self.assertEqual(servico.abrir(mapa_id), (True, mapa_id))
            status = servico.status_detalhado()
            self.assertTrue(status["somente_leitura"])
            self.assertAlmostEqual(status["area_m2"], area, delta=0.05)
            self.assertEqual(servico.ligar(), (False, "mapa_somente_leitura"))
            self.assertEqual(servico.abrir(999), (False, "mapa_inexistente"))

            self.assertEqual(servico.apagar(mapa_id), (True, mapa_id))
            self.assertIsNone(servico.status_detalhado()["mapa_id"])
            self.assertEqual(servico.apagar(mapa_id), (False, "mapa_inexistente"))

    def test_avanco_do_kinect_validado_e_lembrado(self):
        with mapeamento_isolado() as caminho:
            self.assertEqual(servico.configurar({"avanco_cm": "dez"}), (False, "valor_invalido"))
            self.assertEqual(servico.configurar({"avanco_cm": 250}), (True, {"avanco_cm": 100.0}))
            self.assertEqual(servico.configurar({"avanco_cm": 18}), (True, {"avanco_cm": 18.0}))
            servico.encerrar()
            servico.iniciar(caminho)
            self.assertEqual(servico.status_detalhado()["avanco_cm"], 18.0)

    def test_imagem_png(self):
        with estado_isolado(), mapeamento_isolado():
            self.assertIsNone(servico.imagem_png())
            servico.ligar()
            self._mapear([Pose(), Pose()])
            png = servico.imagem_png()
            self.assertTrue(png.startswith(b"\x89PNG"))
            self.assertIs(servico.imagem_png(), png)  # sem mudanca no mapa: reaproveita

    def test_sem_banco_mapeia_mas_nao_salva(self):
        with estado_isolado(), mapeamento_isolado(abrir=False):
            servico.ligar()
            self._mapear([Pose()])
            self.assertEqual(servico.salvar("x"), (False, "banco_indisponivel"))
            self.assertEqual(servico.listar(), [])
            self.assertEqual(servico.abrir(1), (False, "banco_indisponivel"))

    def test_banco_corrompido_e_preservado(self):
        with mapeamento_isolado(abrir=False) as caminho:
            conteudo = b"nao e sqlite " * 200
            with open(caminho, "wb") as arquivo:
                arquivo.write(conteudo)
            self.assertFalse(servico.iniciar(caminho))
            self.assertTrue(servico.status_detalhado()["erro"])
            with open(caminho, "rb") as arquivo:
                self.assertEqual(arquivo.read(), conteudo)

    def test_loop_mapeia_e_sobrevive_a_erro(self):
        with estado_isolado(), mapeamento_isolado(), patch.object(estado, "stop_event", EventoDeParada(2)):
            servico.ligar()
            estado.state.update({"kinect_ok": True, "depth": quadro(Pose(), sala()), "depth_mm": True,
                                 "depth_at": servico.util.now() + 60})
            servico.mapeamento_loop()
            self.assertEqual(servico.status_detalhado()["leituras"], 2)
        with mapeamento_isolado(), patch.object(estado, "stop_event", EventoDeParada(2)), \
                patch.object(servico, "passo", side_effect=RuntimeError("falha")) as passo:
            servico.mapeamento_loop()
        self.assertEqual(passo.call_count, 2)


if __name__ == "__main__":
    unittest.main()
