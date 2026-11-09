from datetime import date

from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse

from acompanhamento.tests.test_linha_de_base import criar_perfis


@override_settings(ACOMPANHAMENTO_ATIVO=True)
class PainelProfessoresViewTests(TestCase):
    def setUp(self):
        perfis = criar_perfis()
        self.coordenador = perfis["docente_coordenador"]
        self.servidor = perfis["servidor"]

        self.url = reverse("acompanhamento:painel_professores")
        self.url_inicio = reverse("acompanhamento:acompanhamento_inicio")

    def test_coordenador_consegue_acessar_painel_professores(self):
        """Coordenador autenticado deve acessar a tela com sucesso (HTTP 200)."""
        self.client.force_login(self.coordenador)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "acompanhamento/painel_professores.html")

    def test_servidor_recebe_403_e_nao_ve_cartao_no_inicio(self):
        """Servidor sem permissão deve receber HTTP 403 e não visualizar o cartão na inicial."""
        self.client.force_login(self.servidor)

        response_painel = self.client.get(self.url)
        self.assertEqual(response_painel.status_code, 403)

        response_inicio = self.client.get(self.url_inicio)
        self.assertEqual(response_inicio.status_code, 200)
        self.assertNotContains(response_inicio, "Painel por Professor")

    @override_settings(ACOMPANHAMENTO_ATIVO=False)
    def test_chave_desligada_retorna_404(self):
        """Com o módulo inativo, qualquer tentativa de acesso deve responder HTTP 404."""
        self.client.force_login(self.coordenador)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 404)

    def test_filtros_invalidos_nao_causam_erro_500(self):
        """Entradas numéricas inconsistentes devem ser tratadas sem erro 500."""
        self.client.force_login(self.coordenador)

        response = self.client.get(
            self.url,
            {
                "orientador_id": "99999",
                "ano_inicial": "2026",
                "ano_final": "2020",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "O ano inicial não pode ser maior que o ano final.",
        )

    @override_settings(DEBUG=True)
    def test_exibicao_sem_base_quando_taxa_sucesso_eh_none(self):
        """Gera a massa fictícia e confirma a renderização da tag 'Sem base'."""
        call_command(
            "gerar_dados_ficticios",
            semente=42,
            data_referencia=date(2026, 11, 5),
        )

        perfis = criar_perfis()
        self.coordenador = perfis["docente_coordenador"]
        self.client.force_login(self.coordenador)

        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Sem base")

    @override_settings(DEBUG=True)
    def test_grafico_exibe_barras_ao_selecionar_professor(self):
        """Garante que ao filtrar por um professor específico o gráfico renderiza as turmas."""
        call_command(
            "gerar_dados_ficticios",
            semente=42,
            data_referencia=date(2026, 11, 5),
        )

        perfis = criar_perfis()
        self.coordenador = perfis["docente_coordenador"]
        self.client.force_login(self.coordenador)

        response_geral = self.client.get(self.url)
        self.assertEqual(response_geral.status_code, 200)

        professores = response_geral.context["professores_list"]
        self.assertTrue(len(professores) > 0, "Deve haver professores na lista fictícia")

        orientador_id = professores[0]["id"]
        response_prof = self.client.get(self.url, {"orientador_id": orientador_id})

        self.assertEqual(response_prof.status_code, 200)
        self.assertTrue(response_prof.context["turmas_grafico"])
        self.assertContains(response_prof, "turma-grupo")

    @override_settings(DEBUG=True)
    def test_previsao_conclusao_calculada_para_professores(self):
        """Garante que a previsão de conclusão é invocada na view ao selecionar um orientador."""
        call_command(
            "gerar_dados_ficticios",
            semente=42,
            data_referencia=date(2026, 11, 5),
        )

        perfis = criar_perfis()
        self.client.force_login(perfis["docente_coordenador"])

        response_geral = self.client.get(self.url)
        dados_painel = response_geral.context["dados_painel"]

        # Busca o ID de um orientador que possui discentes ativos na massa fictícia
        orientador_id = None
        for linha in dados_painel.get("linhas", []):
            if linha.get("orientador_id") is not None and linha.get("ativos", 0) > 0:
                orientador_id = linha["orientador_id"]
                break

        if not orientador_id:
            professores = response_geral.context["professores_list"]
            orientador_id = professores[0]["id"]

        response = self.client.get(self.url, {"orientador_id": orientador_id})

        self.assertEqual(response.status_code, 200)
        self.assertIn("dados_previsao", response.context)
