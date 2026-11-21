"""Tela do painel de processos: acesso, filtros e estados vazios."""

from datetime import date
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse

from acompanhamento.tests.test_fundacao import PERFIS_COM_GESTAO
from acompanhamento.tests.test_linha_de_base import criar_perfis


@override_settings(ACOMPANHAMENTO_ATIVO=True, SECURE_SSL_REDIRECT=False)
class TelaPainelProcessosTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.perfis = criar_perfis()
        cls.endereco = reverse("acompanhamento:painel_processos")

    def test_acesso_por_perfil(self):
        for nome, usuario in self.perfis.items():
            with self.subTest(perfil=nome):
                self.client.force_login(usuario)
                esperado = 200 if nome in PERFIS_COM_GESTAO else 403
                self.assertEqual(self.client.get(self.endereco).status_code, esperado)

    @override_settings(ACOMPANHAMENTO_ATIVO=False)
    def test_chave_desligada_responde_como_pagina_inexistente(self):
        self.client.force_login(self.perfis["membro_secretaria"])
        self.assertEqual(self.client.get(self.endereco).status_code, 404)

    def test_semestre_escolhido_aparece_selecionado(self):
        self.client.force_login(self.perfis["membro_secretaria"])
        resposta = self.client.get(self.endereco, {"semestre": "2001.1"})
        self.assertContains(resposta, "Processos abertos de 01/01/2001 a 30/06/2001.")
        self.assertContains(resposta, '<option value="2001.1" selected>', html=False)

    def test_semestre_invalido_avisa_e_mostra_o_semestre_atual(self):
        self.client.force_login(self.perfis["membro_secretaria"])
        resposta = self.client.get(self.endereco, {"semestre": "2026-2"}, follow=True)
        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, "Semestre inválido. Use o formato AAAA.1 ou AAAA.2.")

    def test_intervalo_de_datas_e_grafico_por_tipo_e_mes(self):
        self.client.force_login(self.perfis["membro_secretaria"])
        contexto = {
            "semestre": "2025.1",
            "data_inicio": date(2025, 1, 1),
            "data_fim": date(2025, 2, 28),
            "total_processos": 2,
            "total_atrasados": 0,
            "tempo_medio_dias": None,
            "por_tipo": {"Outro": 2},
            "por_tipo_por_mes": [
                {"mes": "2025-01", "tipo": "Outro", "total": 1},
                {"mes": "2025-02", "tipo": "Outro", "total": 1},
            ],
            "por_status": {"Em análise": 2},
            "abertos_por_mes": {"2025-01": 1, "2025-02": 1},
        }

        with patch(
            "acompanhamento.views.calcular_painel_processos",
            return_value=contexto,
        ) as calcular:
            resposta = self.client.get(
                self.endereco,
                {
                    "data_inicio": "2025-01-01",
                    "data_fim": "2025-02-28",
                },
            )

        calcular.assert_called_once_with(
            semestre=None,
            data_inicio=date(2025, 1, 1),
            data_fim=date(2025, 2, 28),
        )
        self.assertContains(resposta, "Processos abertos de 01/01/2025 a 28/02/2025.")
        self.assertContains(resposta, "Por tipo e mês")
        self.assertContains(resposta, "01/2025 — Outro")
        self.assertContains(resposta, "02/2025 — Outro")

    def test_intervalo_invertido_mostra_erro(self):
        self.client.force_login(self.perfis["membro_secretaria"])
        resposta = self.client.get(
            self.endereco,
            {"data_inicio": "2025-02-01", "data_fim": "2025-01-01"},
            follow=True,
        )
        self.assertEqual(resposta.status_code, 200)
        self.assertContains(
            resposta,
            "A data inicial deve ser anterior ou igual à data final.",
        )

    def test_data_incompleta_mantem_semestre_escolhido_e_seus_indicadores(self):
        self.client.force_login(self.perfis["membro_secretaria"])
        resposta = self.client.get(
            self.endereco,
            {"semestre": "2025.1", "data_inicio": "2025-02-01"},
            follow=True,
        )

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, "Informe as datas inicial e final.")
        self.assertContains(resposta, "Processos abertos de 01/01/2025 a 30/06/2025.")
        self.assertContains(resposta, '<option value="2025.1" selected>', html=False)

    def test_intervalo_invertido_mantem_semestre_escolhido(self):
        self.client.force_login(self.perfis["membro_secretaria"])
        resposta = self.client.get(
            self.endereco,
            {
                "semestre": "2025.1",
                "data_inicio": "2025-02-01",
                "data_fim": "2025-01-01",
            },
            follow=True,
        )

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, "Processos abertos de 01/01/2025 a 30/06/2025.")
        self.assertContains(resposta, '<option value="2025.1" selected>', html=False)

    def test_intervalo_longo_mantem_semestre_escolhido(self):
        self.client.force_login(self.perfis["membro_secretaria"])
        resposta = self.client.get(
            self.endereco,
            {
                "semestre": "2025.1",
                "data_inicio": "0202-01-01",
                "data_fim": "2026-12-31",
            },
            follow=True,
        )

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, "O intervalo pode ter no máximo 10 anos.")
        self.assertContains(resposta, "Processos abertos de 01/01/2025 a 30/06/2025.")
        self.assertContains(resposta, '<option value="2025.1" selected>', html=False)

    def test_semestre_sem_processos_mostra_estados_vazios(self):
        self.client.force_login(self.perfis["membro_secretaria"])
        resposta = self.client.get(self.endereco, {"semestre": "2001.1"})
        self.assertContains(resposta, "Nenhum processo finalizado no período")
        self.assertContains(resposta, "Nenhum processo aberto no período.")
        self.assertContains(resposta, "01/2001")

    def test_tela_listada_na_pagina_inicial_do_modulo(self):
        self.client.force_login(self.perfis["membro_secretaria"])
        resposta = self.client.get(reverse("acompanhamento:acompanhamento_inicio"))
        self.assertContains(resposta, self.endereco)
