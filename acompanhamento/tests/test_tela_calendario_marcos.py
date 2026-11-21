from django.test import TestCase, override_settings
from django.urls import reverse

from acompanhamento.models import MarcoCalendario
from acompanhamento.tests.test_linha_de_base import criar_perfis


@override_settings(ACOMPANHAMENTO_ATIVO=True, SECURE_SSL_REDIRECT=False)
class TelaCalendarioMarcosTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.perfis = criar_perfis()
        cls.url = reverse("acompanhamento:calendario_marcos")

    def test_aluno_nao_pode_acessar_tela(self):
        """Garante que o aluno recebe HTTP 403 Forbidden ao tentar acessar a tela do calendário."""
        self.client.force_login(self.perfis["aluno"])
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 403)

    def test_gestao_consegue_acessar_e_cadastrar_marco(self):
        """Garante que a gestão consegue acessar, cadastrar um marco e que o alterado_por é gravado."""
        usuario_gestao = self.perfis["membro_secretaria"]
        self.client.force_login(usuario_gestao)

        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)

        dados = {
            "tipo": MarcoCalendario.TipoMarco.QUALIFICACAO,
            "semestre": "2026.2",
            "data_limite": "2026-09-21",
        }
        post_response = self.client.post(self.url, data=dados)
        self.assertEqual(post_response.status_code, 302)

        marco = MarcoCalendario.objects.get(semestre="2026.2", tipo=MarcoCalendario.TipoMarco.QUALIFICACAO)
        self.assertEqual(marco.alterado_por_id, usuario_gestao.pk)
