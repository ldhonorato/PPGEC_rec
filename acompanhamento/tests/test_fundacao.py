"""Testes da fundação do módulo: chave, regra de acesso e página inicial."""

from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from acompanhamento.permissoes import tem_acesso_gestao
from acompanhamento.tests import test_linha_de_base as linha_de_base
from acompanhamento.tests.test_linha_de_base import criar_perfis
from processos import context_processors
from processos import views as processos_views

from unittest.mock import patch

PERFIS_COM_GESTAO = {
    "docente_coordenador",
    "servidor",
    "bolsista",
    "membro_secretaria",
    "membro_coordenacao",
}


class ChavePadraoTests(SimpleTestCase):
    def test_chave_desligada_quando_a_variavel_nao_existe(self):
        self.assertIs(settings.ACOMPANHAMENTO_ATIVO, False)


@override_settings(SECURE_SSL_REDIRECT=False)
class PaginaInicialTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.perfis = criar_perfis()
        cls.endereco = reverse("acompanhamento:acompanhamento_inicio")

    @override_settings(ACOMPANHAMENTO_ATIVO=False)
    def test_chave_desligada_responde_como_pagina_inexistente_para_todos(self):
        self.assertEqual(self.client.get(self.endereco).status_code, 404)
        for nome, usuario in self.perfis.items():
            with self.subTest(perfil=nome):
                self.client.force_login(usuario)
                self.assertEqual(self.client.get(self.endereco).status_code, 404)

    @override_settings(ACOMPANHAMENTO_ATIVO=True)
    def test_visitante_sem_login_vai_para_o_login(self):
        resposta = self.client.get(self.endereco)
        self.assertEqual(resposta.status_code, 302)
        self.assertIn(settings.LOGIN_URL, resposta["Location"])

    @override_settings(ACOMPANHAMENTO_ATIVO=True)
    def test_acesso_por_perfil_com_a_chave_ligada(self):
        for nome, usuario in self.perfis.items():
            with self.subTest(perfil=nome):
                self.client.force_login(usuario)
                esperado = 200 if nome in PERFIS_COM_GESTAO else 403
                self.assertEqual(self.client.get(self.endereco).status_code, esperado)

    @override_settings(ACOMPANHAMENTO_ATIVO=True)
    def test_modulo_sem_telas_informa_que_nao_ha_telas(self):

        self.client.force_login(self.perfis["servidor"])
        with patch("acompanhamento.views.TELAS", ()):
            self.assertContains(self.client.get(self.endereco), "Ainda não há telas disponíveis.")


class RegraDeAcessoTests(TestCase):
    """Garante que a regra do módulo é a mesma do sistema, nas duas cópias."""

    @classmethod
    def setUpTestData(cls):
        cls.perfis = criar_perfis()

    def test_as_tres_versoes_da_regra_concordam_para_todos_os_perfis(self):
        usuarios = dict(self.perfis, visitante=AnonymousUser())
        for nome, usuario in usuarios.items():
            with self.subTest(perfil=nome):
                da_tela = processos_views._has_gestao_access(usuario)
                do_menu = context_processors._has_gestao_access(usuario)
                self.assertEqual(da_tela, do_menu, "as duas cópias da regra do sistema divergiram")
                self.assertEqual(tem_acesso_gestao(usuario), do_menu)
                self.assertEqual(do_menu, nome in PERFIS_COM_GESTAO)


@override_settings(ACOMPANHAMENTO_ATIVO=True)
class LinhaDeBaseComModuloLigadoTests(linha_de_base.LinhaDeBaseTests):
    """Repete a linha de base com a chave ligada: nada do sistema pode mudar."""
