"""Testes da entrada do módulo no menu lateral do sistema."""

from django.db import connection
from django.test import RequestFactory, TestCase, override_settings
from django.test.utils import CaptureQueriesContext

from acompanhamento.tests.test_fundacao import PERFIS_COM_GESTAO
from acompanhamento.tests.test_linha_de_base import criar_perfis
from processos import context_processors

ROTULO = "Acompanhamento"


def rotulos_do_menu(usuario):
    """Devolve todos os rótulos do menu lateral do usuário, inclusive os filhos."""
    rotulos = []
    for secao in context_processors._menu_lateral_sections(usuario):
        for item in secao["items"]:
            rotulos.append(item["label"])
            rotulos.extend(filho["label"] for filho in item["children"])
    return rotulos


def consultas_do_menu(usuario):
    """Conta as consultas da montagem do menu, como numa requisição nova.

    O usuário é buscado de novo no banco antes da medição. Montar o menu guarda
    no objeto do usuário relações já consultadas, como o perfil de aluno ou de
    docente, e medir duas vezes com o mesmo objeto daria uma consulta a menos
    na segunda medição, sem relação com o módulo.
    """
    usuario = type(usuario).objects.get(pk=usuario.pk)
    requisicao = RequestFactory().get("/")
    requisicao.user = usuario
    with CaptureQueriesContext(connection) as consultas:
        context_processors.navegacao_lateral(requisicao)
    return len(consultas)


@override_settings(SECURE_SSL_REDIRECT=False)
class EntradaNoMenuTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.perfis = criar_perfis()

    @override_settings(ACOMPANHAMENTO_ATIVO=False)
    def test_chave_desligada_nao_mostra_o_item_para_ninguem(self):
        for nome, usuario in self.perfis.items():
            with self.subTest(perfil=nome):
                self.assertNotIn(ROTULO, rotulos_do_menu(usuario))

    @override_settings(ACOMPANHAMENTO_ATIVO=True)
    def test_chave_ligada_mostra_o_item_so_para_a_gestao(self):
        for nome, usuario in self.perfis.items():
            with self.subTest(perfil=nome):
                rotulos = rotulos_do_menu(usuario)
                self.assertEqual(ROTULO in rotulos, nome in PERFIS_COM_GESTAO)
                self.assertLessEqual(rotulos.count(ROTULO), 1, "o módulo deve ter um único item no menu")

    def test_o_item_nao_acrescenta_consultas_ao_menu(self):
        for nome, usuario in self.perfis.items():
            with self.subTest(perfil=nome):
                with override_settings(ACOMPANHAMENTO_ATIVO=False):
                    desligada = consultas_do_menu(usuario)
                with override_settings(ACOMPANHAMENTO_ATIVO=True):
                    ligada = consultas_do_menu(usuario)
                self.assertEqual(ligada, desligada)

    @override_settings(ACOMPANHAMENTO_ATIVO=True)
    def test_item_leva_ao_modulo_e_acende_na_pagina_do_modulo(self):
        self.client.force_login(self.perfis["servidor"])
        pagina_do_sistema = self.client.get("/").content.decode()
        self.assertIn('href="/acompanhamento/"', pagina_do_sistema)

        pagina_do_modulo = self.client.get("/acompanhamento/").content.decode()
        trecho = pagina_do_modulo[pagina_do_modulo.index('href="/acompanhamento/"') - 200:]
        self.assertIn("is-active", trecho[:400])
