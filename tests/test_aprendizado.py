import os
import threading
import unittest
from unittest.mock import patch

import numpy as np
from apoio import EventoDeParada, aprendizado_isolado, estado_isolado

from pc_agv import estado
from pc_agv.aprendizado import banco as banco_mod
from pc_agv.aprendizado import entrada, politica, recompensa, servico, treino
from pc_agv.aprendizado.banco import BancoAprendizado
from pc_agv.aprendizado.rede import RedeNeural
from pc_agv.visao import profundidade


# ── Cenas sinteticas (profundidade em mm, como o Kinect com is_mm) ──────────
COLUNAS_OBSTACULO = {"esq": (0, 220), "cen": (220, 420), "dir": (420, 640)}
# O "operador" das cenas: desvia para o lado livre e segue quando nao ha nada.
OPERADOR = {None: (100, 0), "cen": (0, 70), "esq": (40, 70), "dir": (40, -70)}


def cena(lado=None, distancia_mm=600, ruido=0, rng=None):
    depth = np.full((480, 640), 3500, np.int32)
    if lado:
        inicio, fim = COLUNAS_OBSTACULO[lado]
        depth[120:380, inicio:fim] = distancia_mm
    if ruido:
        depth = depth + (rng or np.random.default_rng(0)).integers(-ruido, ruido + 1, depth.shape)
    return depth.astype(np.uint16)


def colocar_cena(depth, agora, kinect_ok=True):
    _, distancia, esquerda, centro, direita = profundidade.compute_depth_metrics(depth)
    estado.state.update({
        "kinect_ok": kinect_ok,
        "depth": depth,
        "depth_mm": True,
        "depth_at": agora,
        "distance_m": distancia,
        "left_clearance_m": esquerda,
        "center_clearance_m": centro,
        "right_clearance_m": direita,
    })


def comandar(modo, velocidade, direcao, fonte):
    estado.control_state.update({
        "mode": modo, "active_speed": velocidade, "active_steering": direcao, "active_source": fonte,
    })


def dados_sinteticos(origens, velocidades=None, direcoes=None, frentes_mm=None, sessoes=None, tempos=None, eventos=None):
    n = len(origens)
    minis = []
    for i in range(n):
        distancia = (frentes_mm or [3500] * n)[i]
        minis.append(entrada.miniatura(cena("cen", distancia) if distancia < 3500 else cena(), True))
    return {
        "id": np.arange(1, n + 1),
        "sessao": np.array(sessoes or [1] * n),
        "tempo": np.array(tempos or [i * 0.2 for i in range(n)], dtype=float),
        "origem": np.array(origens, dtype=object),
        "velocidade": np.array(velocidades or [50] * n, dtype=float),
        "direcao": np.array(direcoes or [0] * n, dtype=float),
        "evento": np.array(eventos or [""] * n, dtype=object),
        "minis": np.stack(minis),
    }


# ── Entrada ─────────────────────────────────────────────────────────
class TestEntrada(unittest.TestCase):
    def test_metros_do_quadro_bruto_seguem_a_formula_do_kinect(self):
        bruto = np.array([[600, 50, 2047]], np.uint16)
        metros = profundidade.depth_to_meters(bruto, is_mm=False)
        self.assertAlmostEqual(metros[0, 0], 1.0 / (600 * -0.0030711016 + 3.3309495161), places=4)
        self.assertTrue(np.isnan(metros[0, 1]) and np.isnan(metros[0, 2]))

    def test_miniatura_marca_o_obstaculo_e_as_regioes_sem_leitura(self):
        depth = cena("esq", 600)
        depth[:, 600:] = 0  # sem leitura
        mini = entrada.miniatura(depth, True)
        self.assertEqual((mini.shape, mini.dtype), ((entrada.LINHAS, entrada.COLUNAS), np.uint8))
        metros = entrada.metros_da_miniatura(mini)
        self.assertAlmostEqual(float(metros[12, 3]), 0.6, delta=0.03)   # obstaculo a esquerda
        self.assertAlmostEqual(float(metros[12, 20]), 3.5, delta=0.03)  # fundo
        self.assertTrue(np.isnan(metros[12, 31]))                      # coluna sem leitura

    def test_miniatura_ignora_pixel_isolado_de_ruido(self):
        depth = cena()
        depth[200, 300] = 300  # um pixel "perto" sozinho
        metros = entrada.metros_da_miniatura(entrada.miniatura(depth, True))
        self.assertAlmostEqual(float(np.nanmin(metros)), 3.5, delta=0.03)

    def test_caracteristicas_mostram_de_que_lado_esta_o_obstaculo(self):
        x = entrada.extrair_entradas(np.stack([entrada.miniatura(cena("esq"), True), entrada.miniatura(cena("dir"), True)]))
        self.assertEqual(x.shape, (2, entrada.TAMANHO_ENTRADA))
        self.assertTrue(((x >= 0) & (x <= 1)).all())
        meio = slice(16, 24)  # distancias da faixa do meio (colunas 0..7)
        self.assertLess(x[0, meio][0], x[0, meio][7])  # obstaculo a esquerda: coluna 0 mais perto
        self.assertGreater(x[1, meio][0], x[1, meio][7])

    def test_distancia_frente(self):
        self.assertAlmostEqual(float(entrada.distancia_frente(entrada.miniatura(cena("cen", 500), True))[0]), 0.5, delta=0.03)
        self.assertTrue(np.isnan(entrada.distancia_frente(np.zeros((24, 32), np.uint8))[0]))

    def test_bytes_da_miniatura(self):
        mini = entrada.miniatura(cena("dir"), True)
        np.testing.assert_array_equal(entrada.miniatura_de_bytes(mini.tobytes()), mini)
        self.assertIsNone(entrada.miniatura_de_bytes(b"curto"))


# ── Rede neural ─────────────────────────────────────────────────────
class TestRede(unittest.TestCase):
    def test_gradiente_confere_com_a_derivada_numerica(self):
        rng = np.random.default_rng(1)
        rede = RedeNeural(5, (4, 3), semente=2)
        x, y, w = rng.normal(size=(7, 5)), rng.uniform(-0.9, 0.9, (7, 2)), rng.uniform(0.5, 2.0, 7)
        _, grad_pesos, _ = rede.gradientes(x, y, w, l2=0.01)
        passo = 1e-6
        for camada in range(len(rede.pesos)):
            i, j = 1, 0
            original = rede.pesos[camada][i, j]
            rede.pesos[camada][i, j] = original + passo
            mais = rede.gradientes(x, y, w, l2=0.01)[0]
            rede.pesos[camada][i, j] = original - passo
            menos = rede.gradientes(x, y, w, l2=0.01)[0]
            rede.pesos[camada][i, j] = original
            self.assertAlmostEqual(grad_pesos[camada][i, j], (mais - menos) / (2 * passo), places=6)

    def test_treino_aprende_uma_regra_simples(self):
        rng = np.random.default_rng(0)
        x = rng.uniform(-1, 1, (400, 3))
        y = np.stack([0.8 * np.tanh(2 * x[:, 0]), -0.6 * x[:, 1]], axis=1)
        w = np.ones(400)
        rede = RedeNeural(3, (16,), semente=0)
        antes = rede.perda(x[300:], y[300:], w[300:])
        historico = rede.treinar(x[:300], y[:300], w[:300], x[300:], y[300:], w[300:], max_epocas=150)
        self.assertLess(rede.perda(x[300:], y[300:], w[300:]), antes / 10)
        self.assertEqual(historico[0]["epoca"], 1)

    def test_salvar_e_carregar_preserva_as_previsoes(self):
        rede = RedeNeural(entrada.TAMANHO_ENTRADA, semente=3)
        rede.ajustar_normalizacao(np.random.default_rng(0).uniform(0, 1, (20, entrada.TAMANHO_ENTRADA)))
        copia = RedeNeural.de_bytes(rede.para_bytes())
        x = np.random.default_rng(1).uniform(0, 1, (5, entrada.TAMANHO_ENTRADA))
        np.testing.assert_allclose(copia.prever(x), rede.prever(x))

    def test_arquivo_de_pesos_invalido_e_recusado(self):
        for dados in (b"isto nao e um modelo", b"PK\x03\x04 zip quebrado"):
            with self.subTest(dados=dados), self.assertRaises(ValueError):
                RedeNeural.de_bytes(dados)


# ── Recompensa ──────────────────────────────────────────────────────
class TestRecompensa(unittest.TestCase):
    def _r(self, origens, frentes, velocidades=None, direcoes=None, eventos=None, sessoes=None, tempos=None):
        n = len(origens)
        return recompensa.recompensas(
            np.array(sessoes or [1] * n), np.array(tempos or [i * 0.2 for i in range(n)], dtype=float),
            np.array(origens, dtype=object), np.array(velocidades or [50] * n), np.array(direcoes or [0] * n),
            np.array(frentes, dtype=float), np.array(eventos or [""] * n, dtype=object),
        )

    def test_avancar_com_caminho_livre_e_premiado(self):
        r = self._r(["ia", "ia"], [3.0, 3.0], velocidades=[100, 100])
        self.assertAlmostEqual(r[0], recompensa.PREMIO_AVANCO)

    def test_chegar_perto_demais_e_punido(self):
        livre, alerta, perigo = (self._r(["ia", "ia"], [3.0, d])[0] for d in (3.0, 0.5, 0.3))
        self.assertGreater(livre, alerta)
        self.assertGreater(alerta, perigo)

    def test_correcao_do_operador_e_parada_sao_punidas(self):
        normal = self._r(["ia", "ia"], [3.0, 3.0])[0]
        corrigido = self._r(["ia", "correcao"], [3.0, 3.0])[0]
        parada = self._r(["ia"], [3.0], eventos=["parada"])[0]
        self.assertAlmostEqual(normal - corrigido, recompensa.PENALIDADE_CORRECAO)
        self.assertAlmostEqual(normal - parada, recompensa.PENALIDADE_PARADA)

    def test_amostras_do_operador_nao_tem_recompensa(self):
        self.assertTrue(np.isnan(self._r(["operador", "ia"], [3.0, 3.0])[0]))

    def test_retorno_soma_os_passos_seguintes_da_mesma_sessao(self):
        origens = np.array(["ia", "ia", "ia", "ia"], dtype=object)
        sessoes = np.array([1, 1, 1, 2])
        tempos = np.array([0.0, 0.2, 0.4, 0.6])
        g = recompensa.retornos(sessoes, tempos, origens, np.array([1.0, 1.0, 1.0, 5.0]))
        gama = recompensa.GAMA
        self.assertAlmostEqual(g[2], 1.0)          # ultima da sessao 1 nao enxerga a sessao 2
        self.assertAlmostEqual(g[1], 1.0 + gama)
        self.assertAlmostEqual(g[0], 1.0 + gama + gama ** 2)
        self.assertAlmostEqual(g[3], 5.0)

    def test_retorno_para_numa_pausa_longa(self):
        g = recompensa.retornos(np.array([1, 1]), np.array([0.0, 3.0]), np.array(["ia", "ia"], dtype=object), np.array([1.0, 1.0]))
        self.assertAlmostEqual(g[0], 1.0)

    def test_pesos_tem_media_um_e_seguem_o_retorno(self):
        pesos = recompensa.pesos_por_retorno(np.array([-2.0, 0.0, 1.0, 3.0]))
        self.assertAlmostEqual(float(pesos.mean()), 1.0)
        self.assertTrue(np.all(np.diff(pesos) > 0))
        np.testing.assert_allclose(recompensa.pesos_por_retorno(np.array([2.0, 2.0])), [1.0, 1.0])


# ── Treino ──────────────────────────────────────────────────────────
class TestTreino(unittest.TestCase):
    def test_pesos_por_origem_e_fase(self):
        # Sessao 2: a IA segue com caminho livre. Sessao 3: a acao da IA termina perto do obstaculo.
        dados = dados_sinteticos(
            ["operador", "correcao", "ia", "ia", "ia", "ia"],
            frentes_mm=[3500, 3500, 3500, 3500, 3500, 400],
            sessoes=[1, 1, 2, 2, 3, 3],
        )
        conjunto = treino.montar_conjunto(dados)
        self.assertEqual(list(conjunto["w"][:2]), [1.0, treino.PESO_CORRECAO])
        self.assertGreater(conjunto["w"][2], conjunto["w"][4])  # a acao que levou ao obstaculo pesa menos
        self.assertEqual(conjunto["fase"], "recompensa")
        self.assertEqual(treino.montar_conjunto(dados_sinteticos(["operador"] * 3))["fase"], "imitacao")

    def test_alvo_e_comando_sao_inversos(self):
        comandos = np.array([-100.0, -40.0, 0.0, 70.0, 100.0])
        np.testing.assert_allclose(treino.para_comando(treino.para_alvo(comandos)), comandos)

    def test_validacao_e_o_final_da_gravacao(self):
        treino_idx, validacao = treino.separar_validacao(10)
        self.assertEqual(list(treino_idx), list(range(8)))
        self.assertEqual(list(validacao), [8, 9])

    def test_poucas_amostras_do_operador(self):
        with self.assertRaises(treino.DadosInsuficientes):
            treino.treinar(dados_sinteticos(["operador"] * 5 + ["ia"] * 50), minimo_humanas=10)

    def test_avaliacao_de_previsao_perfeita(self):
        rede = RedeNeural(2, (3,))
        y = np.array([[0.5, -0.5], [0.0, 0.9]])
        with patch.object(rede, "prever", return_value=y):
            metricas = treino.avaliar(rede, np.zeros((2, 2)), y)
        self.assertEqual((metricas["acerto_direcao"], metricas["acerto_movimento"]), (1.0, 1.0))
        self.assertEqual((metricas["erro_velocidade"], metricas["erro_direcao"]), (0.0, 0.0))


# ── Seguranca do modo IA ────────────────────────────────────────────
class TestSeguranca(unittest.TestCase):
    def test_limita_velocidade_e_re(self):
        self.assertEqual(politica.aplicar_seguranca(100, 0, 3.0, 50), (50, 0, "ia"))
        self.assertEqual(politica.aplicar_seguranca(-100, 0, 3.0, 80), (-politica.RE_MAX, 0, "ia"))

    def test_obstaculo_bloqueia_o_avanco_mas_deixa_girar(self):
        self.assertEqual(politica.aplicar_seguranca(60, 70, 0.30, 100), (0, 70, "ia-bloqueio-frente"))
        self.assertEqual(politica.aplicar_seguranca(-20, 0, 0.30, 100), (-20, 0, "ia"))

    def test_perto_anda_devagar(self):
        self.assertEqual(politica.aplicar_seguranca(60, -10, 0.50, 100), (politica.VELOCIDADE_PERTO, -10, "ia-devagar-perto"))

    def test_sem_distancia_para(self):
        self.assertEqual(politica.aplicar_seguranca(60, 30, None, 100), (0, 0, "ia-sem-distancia"))

    def test_zona_morta(self):
        self.assertEqual(politica.aplicar_seguranca(4, -5, 3.0, 100), (0, 0, "ia"))


# ── Banco SQLite ────────────────────────────────────────────────────
class TestBanco(unittest.TestCase):
    def setUp(self):
        import tempfile
        self._pasta = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.caminho = os.path.join(self._pasta.name, "aprendizado.db")
        self.banco = BancoAprendizado(self.caminho)

    def tearDown(self):
        self.banco.fechar()
        self._pasta.cleanup()

    def _gravar(self, n, origem="operador", sessao=None):
        sessao = sessao or self.banco.nova_sessao("manual", 0.0)
        mini = entrada.miniatura(cena("cen"), True)
        return [self.banco.gravar_amostra(sessao, origem, 50, -20, mini, float(i)) for i in range(n)]

    def test_amostras_voltam_como_arrays(self):
        self._gravar(3)
        self._gravar(2, origem="ia")
        dados = self.banco.carregar_amostras()
        self.assertEqual(dados["minis"].shape, (5, entrada.LINHAS, entrada.COLUNAS))
        self.assertEqual(list(dados["origem"]), ["operador"] * 3 + ["ia"] * 2)
        self.assertEqual(self.banco.contagem(), {"operador": 3, "correcao": 0, "ia": 2, "total": 5})

    def test_tudo_continua_la_depois_de_reabrir(self):
        self._gravar(4)
        modelo_id = self.banco.salvar_modelo("imitacao", 1, {"x": 1}, b"pesos")
        self.banco.gravar_preferencia("velocidade_max", 35)
        self.banco.fechar()
        self.banco = BancoAprendizado(self.caminho)
        self.assertEqual(self.banco.contagem()["total"], 4)
        resumo, pesos = self.banco.modelo_ativo()
        self.assertEqual((resumo["id"], pesos), (modelo_id, b"pesos"))
        self.assertEqual(self.banco.ler_preferencia("velocidade_max", 50), 35)

    def test_guarda_so_os_ultimos_modelos_e_troca_o_ativo(self):
        ids = [self.banco.salvar_modelo("imitacao", 1, {}, b"p") for _ in range(banco_mod.MODELOS_GUARDADOS + 3)]
        modelos = self.banco.listar_modelos()
        self.assertEqual(len(modelos), banco_mod.MODELOS_GUARDADOS)
        self.assertEqual([m["id"] for m in modelos if m["ativo"]], [ids[-1]])
        resumo, _ = self.banco.ativar_modelo(ids[-4])
        self.assertTrue(resumo["ativo"])
        self.assertEqual(self.banco.modelo_ativo()[0]["id"], ids[-4])
        self.assertIsNone(self.banco.ativar_modelo(9999))

    def test_poda_as_amostras_mais_antigas(self):
        ids = self._gravar(10)
        self.assertEqual(self.banco.limitar_amostras(6), 4)
        self.assertEqual(list(self.banco.carregar_amostras()["id"]), ids[4:])
        self.assertEqual(self.banco.limitar_amostras(6), 0)

    def test_ultimo_id_nunca_volta_atras(self):
        ids = self._gravar(3)
        self.banco.apagar_amostras()
        self.assertEqual(self.banco.ultimo_id(), ids[-1])
        self.assertEqual(self.banco.contagem()["total"], 0)

    def test_miniatura_de_tamanho_errado_e_recusada(self):
        sessao = self.banco.nova_sessao("manual")
        with self.assertRaises(ValueError):
            self.banco.gravar_amostra(sessao, "operador", 0, 0, np.zeros((5, 5), np.uint8), 0.0)


# ── Servico (gravacao, treino, decisao) ─────────────────────────────
class TestGravacao(unittest.TestCase):
    def _coletar(self, modo, velocidade, direcao, fonte, agora=100.0, depth_at=None, **prefs):
        colocar_cena(cena("cen"), agora if depth_at is None else depth_at)
        comandar(modo, velocidade, direcao, fonte)
        servico._prefs.update(prefs)
        return servico.coletar(agora)

    def test_grava_o_operador_dirigindo(self):
        with estado_isolado(), aprendizado_isolado():
            self.assertEqual(self._coletar("manual", 100, 0, "site-keepalive"), "operador")
            self.assertEqual(servico.status_snapshot()["amostras"]["operador"], 1)

    def test_grava_a_decisao_de_parar_so_logo_apos_o_movimento(self):
        with estado_isolado(), aprendizado_isolado():
            self._coletar("manual", 100, 0, "site-manual", agora=100.0)
            self.assertEqual(self._coletar("manual", 0, 0, "site-manual", agora=101.0), "operador")
            self.assertIsNone(self._coletar("manual", 0, 0, "site-manual", agora=104.0))

    def test_parada_depois_do_autonomo_nao_vira_exemplo_do_operador(self):
        # O autonomo nao e professor da IA: a parada logo depois dele tambem nao.
        with estado_isolado(), aprendizado_isolado():
            self.assertIsNone(self._coletar("auto", 48, 0, "autopilot", agora=100.0))
            self.assertIsNone(self._coletar("manual", 0, 0, "site-manual", agora=100.5))
            self.assertEqual(servico.status_snapshot()["amostras"]["total"], 0)

    def test_nao_grava_watchdog_autonomo_desligado_ou_quadro_velho(self):
        with estado_isolado(), aprendizado_isolado():
            self.assertIsNone(self._coletar("manual", 0, 0, "watchdog"))
            self.assertIsNone(self._coletar("auto", 48, 0, "autopilot"))
            self.assertIsNone(self._coletar("manual", 100, 0, "site-manual", depth_at=90.0))
            self.assertIsNone(self._coletar("manual", 100, 0, "site-manual", gravando=False))
            self.assertEqual(servico.status_snapshot()["amostras"]["total"], 0)

    def test_modo_ia_grava_acao_da_ia_e_correcao(self):
        with estado_isolado(), aprendizado_isolado():
            self.assertEqual(self._coletar("ia", 40, 0, "ia"), "ia")
            self.assertEqual(self._coletar("ia", 0, 70, "correcao", agora=100.2), "correcao")

    def test_parada_de_emergencia_marca_a_ultima_acao_da_ia(self):
        with estado_isolado(), aprendizado_isolado():
            self._coletar("ia", 40, 0, "ia", agora=100.0)
            self.assertTrue(servico.registrar_parada(agora=100.5))
            self.assertEqual(list(servico._banco.carregar_amostras()["evento"]), ["parada"])
            self.assertFalse(servico.registrar_parada(agora=200.0))  # muito depois: nao culpa a IA

    def test_sem_banco_nao_grava(self):
        with estado_isolado(), aprendizado_isolado(abrir=False):
            self.assertIsNone(self._coletar("manual", 100, 0, "site-manual"))


class TestAprendizadoDePontaAPonta(unittest.TestCase):
    def _demonstrar(self, voltas=60):
        rng = np.random.default_rng(7)
        # Alguns quadros com ruido por cena, reaproveitados (sortear 480x640 a cada amostra e lento).
        variacoes = {lado: [cena(lado, ruido=40, rng=rng) for _ in range(6)] for lado in OPERADOR}
        agora = 1000.0
        for volta in range(voltas):
            for lado, (velocidade, direcao) in OPERADOR.items():
                colocar_cena(variacoes[lado][volta % 6], agora)
                comandar("manual", velocidade, direcao, "site-keepalive")
                self.assertEqual(servico.coletar(agora), "operador")
                agora += 0.2
        return agora

    def _decisao(self, lado, agora):
        colocar_cena(cena(lado), agora)
        return servico.decidir(agora)

    def test_imita_o_operador_e_lembra_depois_de_reiniciar(self):
        with estado_isolado(), aprendizado_isolado() as caminho:
            agora = self._demonstrar()
            self.assertEqual(servico.iniciar_treino(em_segundo_plano=False), (True, "iniciado"))
            status = servico.status_snapshot()
            self.assertTrue(status["tem_modelo"])
            self.assertEqual(status["modelo"]["fase"], "imitacao")
            self.assertGreaterEqual(status["modelo"]["info"]["validacao"]["acerto_direcao"], 0.9)

            def conferir():
                velocidade, direcao, _ = self._decisao(None, agora)
                self.assertGreaterEqual(velocidade, 30)          # caminho livre: segue em frente
                self.assertLess(abs(direcao), 25)
                self.assertGreater(self._decisao("cen", agora)[1], 30)   # obstaculo no meio: vira
                self.assertGreater(self._decisao("esq", agora)[1], 30)   # a esquerda: vai para a direita
                self.assertLess(self._decisao("dir", agora)[1], -30)     # a direita: vai para a esquerda

            conferir()
            # Desliga e liga o sistema: o modelo e as amostras continuam no banco.
            servico.encerrar()
            self.assertFalse(servico.tem_modelo())
            self.assertTrue(servico.iniciar(caminho))
            self.assertTrue(servico.tem_modelo())
            self.assertEqual(servico.status_snapshot()["amostras"]["operador"], 4 * 60)
            conferir()

    def test_decisao_sem_modelo_ou_sem_profundidade(self):
        with estado_isolado(), aprendizado_isolado():
            self.assertEqual(self._decisao(None, 10.0), (0, 0, "ia-sem-modelo"))
            self._demonstrar(voltas=40)
            servico.iniciar_treino(em_segundo_plano=False)
            colocar_cena(cena(), 10.0)
            self.assertEqual(servico.decidir(20.0), (0, 0, "sem-depth"))
            colocar_cena(cena(), 10.0, kinect_ok=False)
            self.assertEqual(servico.decidir(10.0), (0, 0, "sem-kinect"))

    def test_obstaculo_colado_bloqueia_mesmo_se_a_rede_mandar_seguir(self):
        with estado_isolado(), aprendizado_isolado():
            self._demonstrar(voltas=40)
            servico.iniciar_treino(em_segundo_plano=False)
            colocar_cena(cena("cen", 300), 50.0)
            with patch.object(servico._rede, "prever", return_value=np.array([[0.95, 0.0]])):
                self.assertEqual(servico.decidir(50.0), (0, 0, "ia-bloqueio-frente"))

    def test_treino_automatico_espera_amostras_novas_e_agv_parado(self):
        with estado_isolado(), aprendizado_isolado(), \
                patch.object(servico, "IA_TREINO_AUTOMATICO", 100), \
                patch.object(servico, "iniciar_treino") as treinar:
            agora = self._demonstrar(voltas=30)
            estado.control_state["mode"] = "manual"
            servico._manutencao(agora)  # acabou de dirigir
            treinar.assert_not_called()
            estado.control_state["mode"] = "ia"
            servico._manutencao(agora + 10)  # dirigindo pela IA
            treinar.assert_not_called()
            estado.control_state["mode"] = "manual"
            servico._manutencao(agora + 10)
            treinar.assert_called_once_with(automatico=True)


class TestServicoTreinoEModelos(unittest.TestCase):
    def test_treino_em_segundo_plano_e_volta_a_um_modelo_anterior(self):
        with estado_isolado(), aprendizado_isolado():
            TestAprendizadoDePontaAPonta._demonstrar(self, voltas=40)
            servico.iniciar_treino(em_segundo_plano=False)
            primeiro = servico.status_snapshot()["modelo"]["id"]
            self.assertEqual(servico.iniciar_treino(), (True, "iniciado"))
            for thread in [t for t in threading.enumerate() if t.name == "agv-treino"]:
                thread.join(timeout=30)
            self.assertFalse(servico.status_snapshot()["treino"]["rodando"])
            modelos = servico.listar_modelos()
            self.assertEqual(len(modelos), 2)
            self.assertIn("historico", modelos[0]["info"])        # a lista traz a curva de treino
            self.assertNotIn("historico", servico.status_snapshot()["modelo"]["info"])  # o status nao
            ok, resumo = servico.ativar_modelo(primeiro)
            self.assertTrue(ok)
            self.assertEqual(servico.status_snapshot()["modelo"]["id"], primeiro)
            self.assertEqual(servico.ativar_modelo(999), (False, "modelo_inexistente"))

    def test_falha_no_treino_fica_registrada(self):
        with estado_isolado(), aprendizado_isolado():
            TestAprendizadoDePontaAPonta._demonstrar(self, voltas=40)
            with patch.object(servico.treino, "treinar", side_effect=RuntimeError("sem memoria")):
                servico.iniciar_treino(em_segundo_plano=False)
            treino_status = servico.status_snapshot()["treino"]
            self.assertEqual((treino_status["rodando"], treino_status["erro"]), (False, "sem memoria"))
            self.assertFalse(servico.tem_modelo())

    def test_treino_simultaneo_e_recusado(self):
        with aprendizado_isolado():
            servico._treino["rodando"] = True
            self.assertEqual(servico.iniciar_treino(), (False, "treino_em_andamento"))
            self.assertEqual(servico.apagar("tudo"), (False, "treino_em_andamento"))

    def test_modelo_de_outra_versao_da_entrada_nao_e_carregado(self):
        with aprendizado_isolado() as caminho:
            rede = RedeNeural(entrada.TAMANHO_ENTRADA)
            servico._banco.salvar_modelo("imitacao", entrada.VERSAO_ENTRADA + 1, {}, rede.para_bytes())
            servico.encerrar()
            servico.iniciar(caminho)
            self.assertFalse(servico.tem_modelo())

    def test_loop_grava_e_faz_a_manutencao(self):
        with estado_isolado(), aprendizado_isolado(), \
                patch.object(estado, "stop_event", EventoDeParada(2)), \
                patch.object(servico, "_manutencao") as manutencao:
            colocar_cena(cena("dir"), servico.util.now())
            comandar("manual", 100, -70, "site-manual")
            servico.aprendizado_loop()
            self.assertEqual(servico.status_snapshot()["amostras"]["operador"], 2)
            manutencao.assert_called_once()

    def test_loop_sobrevive_a_um_erro(self):
        with estado_isolado(), aprendizado_isolado(), \
                patch.object(estado, "stop_event", EventoDeParada(2)), \
                patch.object(servico, "coletar", side_effect=RuntimeError("disco cheio")) as coletar:
            servico.aprendizado_loop()
        self.assertEqual(coletar.call_count, 2)

    def test_poda_atualiza_a_contagem(self):
        with estado_isolado(), aprendizado_isolado(), patch.object(servico, "IA_MAX_AMOSTRAS", 10):
            TestAprendizadoDePontaAPonta._demonstrar(self, voltas=5)
            servico._manutencao(0.0)
            self.assertEqual(servico.status_snapshot()["amostras"]["total"], 10)


class TestServicoConfiguracao(unittest.TestCase):
    def test_preferencias_validadas_e_persistidas(self):
        with aprendizado_isolado() as caminho:
            self.assertEqual(servico.configurar({"gravando": "sim"}), (False, "valor_invalido"))
            ok, prefs = servico.configurar({"explorar": True, "velocidade_max": 140})
            self.assertTrue(ok)
            self.assertEqual((prefs["explorar"], prefs["velocidade_max"]), (True, 100))
            servico.encerrar()
            servico.iniciar(caminho)
            self.assertEqual(servico.status_snapshot()["preferencias"]["velocidade_max"], 100)

    def test_treino_recusado_sem_amostras_e_sem_banco(self):
        with aprendizado_isolado():
            self.assertEqual(servico.iniciar_treino(), (False, "amostras_insuficientes"))
        with aprendizado_isolado(abrir=False):
            self.assertEqual(servico.iniciar_treino(), (False, "banco_indisponivel"))

    def test_apagar_tudo_remove_o_modelo(self):
        with estado_isolado(), aprendizado_isolado():
            TestAprendizadoDePontaAPonta._demonstrar(self, voltas=40)
            servico.iniciar_treino(em_segundo_plano=False)
            self.assertEqual(servico.apagar("lixo"), (False, "alvo_invalido"))
            self.assertEqual(servico.apagar("amostras"), (True, "amostras"))
            self.assertTrue(servico.tem_modelo())
            self.assertEqual(servico.apagar("tudo"), (True, "tudo"))
            self.assertFalse(servico.tem_modelo())
            self.assertEqual(servico.listar_modelos(), [])

    def test_banco_corrompido_desliga_o_aprendizado_sem_apagar_o_arquivo(self):
        with aprendizado_isolado(abrir=False) as caminho:
            conteudo = b"isto nao e um banco sqlite " * 100
            with open(caminho, "wb") as arquivo:
                arquivo.write(conteudo)
            self.assertFalse(servico.iniciar(caminho))
            status = servico.status_snapshot()
            self.assertFalse(status["disponivel"])
            self.assertTrue(status["erro"])
            with open(caminho, "rb") as arquivo:
                self.assertEqual(arquivo.read(), conteudo)


if __name__ == "__main__":
    unittest.main()
