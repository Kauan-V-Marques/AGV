import io
import os
import pickle
import tempfile
import unittest
from unittest.mock import patch

import cv2
import numpy as np
from apoio import EventoDeParada, rostos_isolado
from werkzeug.datastructures import FileStorage

from pc_agv import estado
from pc_agv.visao import rostos


def _vetor(**componentes) -> np.ndarray:
    """Descritor facial de 128 posicoes com alguns componentes definidos (ex.: e2=0.1)."""
    vetor = np.zeros(128, dtype=np.float32)
    for nome, valor in componentes.items():
        vetor[int(nome[1:])] = valor
    return vetor


def _png() -> bytes:
    ok, buffer = cv2.imencode(".png", np.full((60, 80, 3), 128, np.uint8))
    assert ok
    return buffer.tobytes()


class _BancoTemporario:
    """Aponta o banco de rostos e as fotos para uma pasta temporaria."""

    def __enter__(self):
        self._pasta = tempfile.TemporaryDirectory()
        raiz = self._pasta.name
        logs = os.path.join(raiz, "logs")
        self._patches = [
            patch.object(rostos, "PROJECT_ROOT", raiz),
            patch.object(rostos, "FACE_DB_DIR", logs),
            patch.object(rostos, "FACE_UPLOADS_DIR", os.path.join(logs, "faces")),
            patch.object(rostos, "ENCODINGS_FILE", os.path.join(logs, "encodings.pkl")),
        ]
        for p in self._patches:
            p.start()
        return raiz

    def __exit__(self, *exc):
        for p in reversed(self._patches):
            p.stop()
        self._pasta.cleanup()


class TestNomes(unittest.TestCase):
    def test_normaliza_espacos_e_limita_tamanho(self):
        self.assertEqual(rostos._normalize_face_name("  Ana    Maria  "), "Ana Maria")
        self.assertEqual(len(rostos._normalize_face_name("x" * 100)), 60)
        self.assertEqual(rostos._normalize_face_name(None), "")

    def test_slug_seguro_para_nome_de_arquivo(self):
        self.assertEqual(rostos._face_name_slug("Ana Maria"), "ana_maria")
        self.assertEqual(rostos._face_name_slug("../../etc"), "etc")
        self.assertEqual(rostos._face_name_slug("..."), "rosto")


class TestBancoDeRostos(unittest.TestCase):
    def test_salva_e_carrega(self):
        with _BancoTemporario(), rostos_isolado():
            rostos._save_face_database([_vetor(e1=1.0)], ["Ana"], ["logs/faces/ana.jpg"])
            encodings, nomes, caminhos = rostos._load_face_database()
        self.assertEqual(nomes, ["Ana"])
        self.assertEqual(caminhos, ["logs/faces/ana.jpg"])
        np.testing.assert_allclose(encodings[0], _vetor(e1=1.0))

    def test_formato_antigo_sem_caminhos(self):
        with _BancoTemporario(), rostos_isolado():
            os.makedirs(rostos.FACE_DB_DIR, exist_ok=True)
            with open(rostos.ENCODINGS_FILE, "wb") as f:
                pickle.dump(([_vetor()], ["Bia"]), f)
            _, nomes, caminhos = rostos._load_face_database()
        self.assertEqual((nomes, caminhos), (["Bia"], [None]))

    def test_formato_invalido(self):
        with _BancoTemporario(), rostos_isolado():
            os.makedirs(rostos.FACE_DB_DIR, exist_ok=True)
            for conteudo in (["lista"], ([_vetor()], ["A", "B"], [None])):
                with self.subTest(conteudo=type(conteudo).__name__):
                    with open(rostos.ENCODINGS_FILE, "wb") as f:
                        pickle.dump(conteudo, f)
                    with self.assertRaises(ValueError):
                        rostos._load_face_database()

    def test_sem_arquivo_fica_sem_cadastro(self):
        with _BancoTemporario(), rostos_isolado():
            rostos.load_known_faces()
            self.assertFalse(rostos.face_status_snapshot()["template_loaded"])

    def test_carregar_remove_selecao_de_quem_saiu_do_banco(self):
        with _BancoTemporario(), rostos_isolado():
            rostos._save_face_database([_vetor()], ["Ana"], [None])
            rostos._face_selected_name = "Carlos"
            rostos.load_known_faces()
            status = rostos.face_status_snapshot()
        self.assertEqual(status["names"], ["Ana"])
        self.assertEqual(status["selected_name"], "")
        self.assertTrue(status["template_loaded"])


class TestSelecao(unittest.TestCase):
    def test_selecionar(self):
        with rostos_isolado():
            rostos._face_known_names[:] = ["Ana"]
            self.assertEqual(rostos.set_selected_face("Carlos"), (False, "rosto nao encontrado"))
            self.assertEqual(rostos.set_selected_face("Ana"), (True, "rosto selecionado"))
            self.assertEqual(rostos.face_status_snapshot()["selected_name"], "Ana")
            self.assertEqual(rostos.set_selected_face(""), (True, "nenhum rosto selecionado"))


class TestDeteccao(unittest.TestCase):
    """Regra: o rosto so e marcado se for o mais parecido com a pessoa escolhida,
    com folga sobre as outras pessoas cadastradas e dentro do limiar estrito."""

    def _detectar(self, rosto_no_quadro, conhecidos):
        nomes = list(conhecidos)
        encodings = [conhecidos[n] for n in nomes]
        quadro = np.zeros((100, 100, 3), np.uint8)
        with patch.object(rostos.face_recognition, "face_locations", return_value=[(10, 60, 60, 10)]), \
                patch.object(rostos.face_recognition, "face_encodings", return_value=[rosto_no_quadro]):
            return rostos._detect_selected_face_on_frame(quadro, "Ana", nomes, encodings)

    def test_reconhece_a_pessoa_escolhida(self):
        known, label, distance, bbox = self._detectar(_vetor(e2=0.1), {"Ana": _vetor(), "Bia": _vetor(e1=1.0)})
        self.assertTrue(known)
        self.assertEqual(label, "Ana")
        self.assertAlmostEqual(distance, 0.1, places=5)
        self.assertEqual(bbox, (10, 10, 50, 50))

    def test_nao_marca_se_outra_pessoa_e_mais_parecida(self):
        known, *_ = self._detectar(_vetor(e2=0.1), {"Ana": _vetor(), "Bia": _vetor(e2=0.11)})
        self.assertFalse(known)

    def test_nao_marca_se_estiver_longe_demais(self):
        known, *_ = self._detectar(_vetor(e2=0.6), {"Ana": _vetor(), "Bia": _vetor(e1=1.0)})
        self.assertFalse(known)


class TestFilaDeQuadros(unittest.TestCase):
    def test_enfileira_um_quadro_por_vez(self):
        quadro = np.zeros((10, 10, 3), np.uint8)
        with rostos_isolado():
            rostos._zed_face_state.update({"pending": False, "next_scan_at": 0.0, "known": True,
                                           "label": "Ana", "bbox": (1, 2, 3, 4)})
            self.assertEqual(rostos.offer_frame(quadro, now=10.0), (True, "Ana", (1, 2, 3, 4)))
            self.assertTrue(rostos._zed_face_state["pending"])
            rostos._zed_face_state["frame"] = "ja na fila"
            rostos.offer_frame(quadro, now=11.0)
            self.assertEqual(rostos._zed_face_state["frame"], "ja na fila")

    def test_respeita_o_intervalo_entre_buscas(self):
        with rostos_isolado():
            rostos._zed_face_state.update({"pending": False, "next_scan_at": 50.0})
            rostos.offer_frame(np.zeros((10, 10, 3), np.uint8), now=10.0)
            self.assertFalse(rostos._zed_face_state["pending"])

    def test_worker_processa_o_quadro_pendente(self):
        with rostos_isolado(), patch.object(estado, "stop_event", EventoDeParada(1)), \
                patch.object(rostos, "_detect_selected_face_on_frame", return_value=(True, "Ana", 0.1, (1, 2, 3, 4))):
            rostos._face_known_names[:] = ["Ana"]
            rostos._face_known_encodings[:] = [_vetor()]
            rostos.set_selected_face("Ana")
            rostos._zed_face_state.update({"pending": True, "frame": np.zeros((10, 10, 3), np.uint8)})
            rostos._zed_face_event.set()
            rostos.face_worker_loop()
            self.assertFalse(rostos._zed_face_state["pending"])
            self.assertEqual(rostos._zed_face_state["bbox"], (1, 2, 3, 4))
            self.assertTrue(rostos.face_status_snapshot()["known"])


class TestCadastro(unittest.TestCase):
    def _foto(self, nome_arquivo="foto.png", conteudo=None):
        return FileStorage(stream=io.BytesIO(_png() if conteudo is None else conteudo), filename=nome_arquivo)

    def test_cadastra_salva_foto_e_seleciona(self):
        with _BancoTemporario() as raiz, rostos_isolado(), \
                patch.object(rostos.face_recognition, "face_encodings", return_value=[_vetor(e3=0.5)]):
            payload, status = rostos.register_face("  Ana  ", self._foto())
            self.assertEqual(status, 200)
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["name"], "Ana")
            self.assertEqual(payload["image_path"], os.path.join("logs", "faces", "ana.png"))
            self.assertTrue(os.path.exists(os.path.join(raiz, payload["image_path"])))
            self.assertEqual(payload["face"]["selected_name"], "Ana")
            self.assertEqual(rostos._load_face_database()[1], ["Ana"])

    def test_recadastrar_substitui_sem_duplicar(self):
        with _BancoTemporario(), rostos_isolado(), \
                patch.object(rostos.face_recognition, "face_encodings", return_value=[_vetor(e3=0.5)]):
            rostos.register_face("Ana", self._foto())
            rostos.register_face("ANA", self._foto("nova.jpg"))
            encodings, nomes, caminhos = rostos._load_face_database()
        self.assertEqual(nomes, ["ANA"])
        self.assertEqual(len(encodings), 1)
        self.assertTrue(caminhos[0].endswith("ana.jpg"))

    def test_extensao_desconhecida_vira_jpg(self):
        with _BancoTemporario(), rostos_isolado(), \
                patch.object(rostos.face_recognition, "face_encodings", return_value=[_vetor()]):
            payload, _ = rostos.register_face("Bia", self._foto("foto.gif"))
        self.assertTrue(payload["image_path"].endswith("bia.jpg"))

    def test_falha_ao_gravar_a_foto(self):
        with _BancoTemporario(), rostos_isolado(), \
                patch.object(rostos.face_recognition, "face_encodings", return_value=[_vetor()]), \
                patch.object(rostos.cv2, "imwrite", return_value=False):
            payload, status = rostos.register_face("Bia", self._foto())
        self.assertEqual((status, payload["error"]), (500, "failed_to_save_image"))


if __name__ == "__main__":
    unittest.main()
