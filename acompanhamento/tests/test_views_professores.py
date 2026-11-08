from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.test import TestCase, override_settings
from django.urls import reverse

User = get_user_model()


class PainelProfessoresViewTests(TestCase):
    def setUp(self):
        # 1. Usuário Coordenador (Acesso Permitido)
        self.coordenador = User.objects.create_user(
            username="coordenador",
            email="coordenador@teste.invalid",
            password="senha_teste",
            is_staff=True,
        )
        # Adicione o perfil/vínculo de coordenador conforme o modelo do sistema (ex: Docente.coordenador = True)

        # 2. Usuário Comum / Servidor (Acesso Negado)
        self.servidor = User.objects.create_user(
            username="servidor",
            email="servidor@teste.invalid",
            password="senha_teste",
        )

        self.url = reverse("acompanhamento:painel_professores")
        self.url_inicio = reverse("acompanhamento:acompanhamento_inicio")

    # CENÁRIO 1: Coordenador abre a tela com sucesso
    def test_coordenador_consegue_acessar_painel_professores(self):
        self.client.login(username="coordenador", password="senha_teste")
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "acompanhamento/painel_professores.html")

    # CENÁRIO 2: Servidor recebe 403 e NÃO vê o cartão na página inicial
    def test_servidor_recebe_403_e_nao_ve_cartao_no_inicio(self):
        self.client.login(username="servidor", password="senha_teste")
        
        # Testa o acesso direto à tela (deve retornar 403 / PermissionDenied)
        response_painel = self.client.get(self.url)
        self.assertEqual(response_painel.status_code, 403)

        # Testa a página inicial (o cartão não deve estar presente no contexto/HTML)
        response_inicio = self.client.get(self.url_inicio)
        self.assertEqual(response_inicio.status_code, 200)
        self.assertNotContains(response_inicio, "Painel por Professor")

    # CENÁRIO 3: Com a chave do módulo desligada, responde com 404
    @override_settings(ACOMPANHAMENTO_ATIVO=False)
    def test_chave_desligada_retorna_404(self):
        self.client.login(username="coordenador", password="senha_teste")
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 404)

    # CENÁRIO 4: Professor ou parâmetros de filtro inválidos não quebram com Erro 500
    def test_filtros_invalidos_nao_causam_erro_500(self):
        self.client.login(username="coordenador", password="senha_teste")
        
        # Passa ID de professor inexistente e anos inválidos/invertidos
        response = self.client.get(self.url, {
            "orientador_id": "99999",
            "ano_inicial": "abc",
            "ano_final": "2020",
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Ano inicial inválido.")

    # CENÁRIO 5: Exibição "Sem base" quando a taxa é None
    def test_exibicao_sem_base_quando_taxa_sucesso_eh_none(self):
        self.client.login(username="coordenador", password="senha_teste")
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        # Verifica se o badge ou texto "Sem base" é renderizado na tabela
        self.assertContains(response, "Sem base")
        