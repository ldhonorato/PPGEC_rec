"""Cartão "Alunos irregulares" na página inicial."""

from datetime import date
from unittest import mock

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils.html import escape

from acompanhamento.models import MarcoCalendario
from acompanhamento.tests.test_fundacao import PERFIS_COM_GESTAO
from acompanhamento.tests.test_linha_de_base import criar_perfis
from acompanhamento.tests.test_lista_alunos import criar_aluno
from processos.models import TrajetoriaAcademica

TEXTO = "Alunos irregulares"
FUNCAO = "acompanhamento.lista_alunos.cartao_alunos_irregulares"


def numero_do_cartao(total):
    """Padrão do número logo acima do rótulo do cartão (e não de outro cartão)."""
    return rf'metric-value">{total}</p>\s*<p class="metric-label">{TEXTO}'


@override_settings(SECURE_SSL_REDIRECT=False)
class CartaoAlunosIrregularesTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.perfis = criar_perfis()
        cls.inicio = reverse("home")
        MarcoCalendario.objects.create(
            semestre="2025.1",
            tipo=MarcoCalendario.TipoMarco.PROJETO_DISSERTACAO,
            data_limite=date(2025, 6, 1),
        )
        criar_aluno(
            "Atrasado Cartao",
            prazo_qualificacao="2025.1",
            data_limite_qualificacao=date(2025, 6, 1),
        )

    def abrir(self, perfil="servidor"):
        self.client.force_login(self.perfis[perfil])
        return self.client.get(self.inicio)

    @override_settings(ACOMPANHAMENTO_ATIVO=False)
    def test_chave_desligada_nao_mostra_o_cartao_nem_chama_a_funcao(self):
        with mock.patch(FUNCAO) as funcao:
            resposta = self.abrir()

        self.assertEqual(resposta.status_code, 200)
        self.assertNotContains(resposta, TEXTO)
        funcao.assert_not_called()

    @override_settings(ACOMPANHAMENTO_ATIVO=True)
    def test_todos_com_acesso_de_gestao_veem_o_cartao_em_primeiro(self):
        for perfil in sorted(PERFIS_COM_GESTAO):
            with self.subTest(perfil=perfil):
                grade = self.abrir(perfil).content.decode().split('class="metric-grid"')[1]
                primeiro_cartao = grade.split('<a class="card metric-card')[1]
                self.assertIn(TEXTO, primeiro_cartao)

    @override_settings(ACOMPANHAMENTO_ATIVO=True)
    def test_quem_nao_tem_acesso_de_gestao_nao_ve_o_cartao(self):
        for perfil in ("aluno", "docente"):
            with self.subTest(perfil=perfil):
                resposta = self.abrir(perfil)
                self.assertEqual(resposta.status_code, 200)
                self.assertNotContains(resposta, TEXTO)

    @override_settings(ACOMPANHAMENTO_ATIVO=True)
    def test_numero_do_cartao_e_o_total_da_lista_aberta_pelo_link(self):
        resposta = self.abrir()
        cartao = resposta.context["cartao_irregulares"]

        self.assertGreaterEqual(cartao["total"], 1)
        self.assertRegex(resposta.content.decode(), numero_do_cartao(cartao["total"]))
        self.assertContains(resposta, escape(cartao["endereco"]))

        lista = self.client.get(cartao["endereco"])
        self.assertEqual(lista.status_code, 200)
        self.assertEqual(lista.context["pagina"].paginator.count, cartao["total"])

    @override_settings(ACOMPANHAMENTO_ATIVO=True)
    def test_erro_na_conta_nao_derruba_a_pagina_inicial(self):
        with mock.patch(
            "acompanhamento.lista_alunos.listar_alunos_por_marco",
            side_effect=RuntimeError("falha de teste"),
        ), self.assertLogs("acompanhamento.lista_alunos", level="ERROR"):
            resposta = self.abrir()

        self.assertEqual(resposta.status_code, 200)
        self.assertNotContains(resposta, TEXTO)
        self.assertContains(resposta, "Alunos com trajetória ativa")

    @override_settings(ACOMPANHAMENTO_ATIVO=True)
    def test_sem_irregulares_o_cartao_mostra_zero(self):
        # As migrations criam o "Aluno Mock 1", já irregular na defesa.
        TrajetoriaAcademica.objects.update(status=TrajetoriaAcademica.Status.DESLIGADA)

        resposta = self.abrir()

        self.assertEqual(resposta.context["cartao_irregulares"]["total"], 0)
        self.assertContains(resposta, TEXTO)
        self.assertRegex(resposta.content.decode(), numero_do_cartao(0))
