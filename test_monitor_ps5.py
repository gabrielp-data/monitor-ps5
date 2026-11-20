import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import monitor_ps5 as m


class MonitorTest(unittest.TestCase):
    def test_titulos(self):
        for titulo in ["Console PS5 Slim + 2 controles", "PlayStation®5 Edição Digital",
                       "Bundle GTA VI + PlayStation 5 Slim Disk", "PS5 + GTA VI"]:
            with self.subTest(titulo=titulo):
                self.assertTrue(m.parece_console(titulo, 2499))
        for titulo in ["Console PlayStation 4 Pro", "Controle PS5 Slim", "GTA VI para PS5",
                       "FIFA 23 PS5", "Capa para console PS5 Slim", "PlayStation Portal"]:
            with self.subTest(titulo=titulo):
                self.assertFalse(m.parece_console(titulo, 3500))

    def test_valor_do_post(self):
        self.assertEqual(m.preco_do_post("De R$ 4.500 por R$ 2.999,90"), 2999.90)
        self.assertEqual(m.preco_do_post("R$ 3.000 ou 10x de R$ 330"), 3000)
        self.assertIsNone(m.preco_do_post("12x de R$ 250"))
        self.assertIsNone(m.preco_do_post("R$ 3.200 | R$ 4.300"))

    def setUp(self):
        stack = self.enterContext(contextlib.ExitStack())
        pasta = Path(stack.enter_context(tempfile.TemporaryDirectory()))
        for campo, arquivo in [("FILA", "fila.json"), ("HISTORICO", "historico.csv"),
                               ("TG_VISTOS", "telegram.txt"), ("PELANDO_VISTOS", "pelando.txt")]:
            stack.enter_context(patch.object(m, campo, pasta / arquivo))
        self.oferta = dict(loja="KaBuM!", titulo="Console PS5 Slim", preco=3500,
                           link="https://example.com/console")
        stack.enter_context(patch.object(m, "kabum", return_value=[self.oferta]))
        for nome in ["amazon", "magalu_via_zoom", "pelando", "canais_telegram"]:
            stack.enter_context(patch.object(m, nome, return_value=[]))
        self.envio = stack.enter_context(patch.object(m, "notificar", return_value=False))
        stack.enter_context(contextlib.redirect_stdout(io.StringIO()))

    def test_reenvia_apos_falha_e_para_apos_entrega(self):
        m.checar()
        self.assertTrue(json.loads(m.FILA.read_text(encoding="utf-8")))
        self.envio.return_value = True
        m.checar()
        m.checar()
        self.assertEqual(self.envio.call_count, 2)
        self.assertEqual(json.loads(m.FILA.read_text(encoding="utf-8")), {})

    def test_cancela_pendente_acima_do_teto(self):
        m.checar()
        self.oferta["preco"] = 4500
        m.checar()
        self.assertEqual(self.envio.call_count, 1)
        self.assertEqual(json.loads(m.FILA.read_text(encoding="utf-8")), {})

    def test_pelando_reenvia_mesmo_depois_de_marcar_visto(self):
        oferta = dict(id="1", titulo="PS5 Slim", preco=2499, loja="Loja", link="",
                      pelando="https://example.com/post", foto="")
        with patch.object(m, "pelando", return_value=[oferta]):
            m.checar()
        self.assertIn("1", m.PELANDO_VISTOS.read_text())
        self.envio.reset_mock()
        self.envio.return_value = True
        m.checar()
        self.assertEqual(self.envio.call_count, 2)


if __name__ == "__main__":
    unittest.main()
