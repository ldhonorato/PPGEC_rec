from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse

from datetime import date

from acompanhamento.tests.test_linha_de_base import criar_perfis


@override_settings(ACOMPANHAMENTO_ATIVO=True)
class PainelProfessoresViewTests(TestCase):
    def setUp(self):
        # Utiliza o helper do sistema para criar perfis válidos
        perfis = criar_perfis()

        # O modelo Docente vincula-se ao User através da propriedade .docente
        # Já o Servidor vincula-se através de .usuario
        self.coordenador = perfis["docente_coordenador"].docente
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

        # Acesso direto deve ser negado com 403 (PermissionDenied)
        response_painel = self.client.get(self.url)
        self.assertEqual(response_painel.status_code, 403)

        # Cartão não deve ser renderizado na página inicial do módulo
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
            "O ano inicial não pode ser maior que o ano final."
        )

    @override_settings(DEBUG=True)
    def test_exibicao_sem_base_quando_taxa_sucesso_eh_none(self):
        """Gera a massa fictícia e confirma a renderização da tag 'Sem base'."""
        call_command(
            "gerar_dados_ficticios",
            semente=42,
            data_referencia=date(2026, 11, 5),
        )

        # O comando pode remover os usuários anteriores.
        # Recria os perfis para autenticar com um usuário válido.
        perfis = criar_perfis()
        self.coordenador = perfis["docente_coordenador"].docente

        self.client.force_login(self.coordenador)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Sem base")
