"""Registro de cada execução do aviso e o texto da página inicial do módulo."""

from datetime import timedelta
from unittest import mock

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from acompanhamento.avisos import rodar_aviso_diario
from acompanhamento.models import ExecucaoAviso
from acompanhamento.tasks import enviar_avisos_irregularidade
from acompanhamento.tests.test_avisos import HOJE, aluno_de_mestrado, membro
from acompanhamento.tests.test_linha_de_base import criar_perfis
from processos.models import Setor, TrajetoriaAcademica

LOG = "acompanhamento.avisos"


class RegistroDaExecucaoTests(TestCase):
    def setUp(self):
        # As migrations já criam alunos de exemplo; eles atrapalhariam a contagem.
        TrajetoriaAcademica.objects.all().delete()
        membro(Setor.NOME_SECRETARIA, "Pessoa Secretaria")
        membro(Setor.NOME_COORDENACAO, "Pessoa Coordenacao")

    def test_registra_avisos_novos_e_emails_enviados(self):
        aluno_de_mestrado("Aluno Atrasado", -30)
        rodar_aviso_diario(HOJE)

        execucao = ExecucaoAviso.objects.get()
        self.assertEqual(execucao.avisos_novos, 1)
        self.assertEqual(execucao.emails_enviados, 1)
        self.assertEqual(execucao.avisos_pendentes, 0)

    def test_dia_sem_novidade_tambem_e_registrado(self):
        aluno_de_mestrado("Aluno em Dia", 30)
        rodar_aviso_diario(HOJE)

        execucao = ExecucaoAviso.objects.get()
        self.assertEqual(
            (execucao.avisos_novos, execucao.emails_enviados, execucao.avisos_pendentes),
            (0, 0, 0),
        )

    def test_cada_vez_que_roda_registra_uma_execucao(self):
        aluno_de_mestrado("Aluno Atrasado", -30)
        rodar_aviso_diario(HOJE)
        rodar_aviso_diario(HOJE)

        self.assertEqual(ExecucaoAviso.objects.count(), 2)
        ultima, primeira = ExecucaoAviso.objects.all()
        self.assertEqual((primeira.avisos_novos, primeira.emails_enviados), (1, 1))
        self.assertEqual((ultima.avisos_novos, ultima.emails_enviados), (0, 0))

    def test_envio_que_falha_aparece_como_pendente(self):
        aluno_de_mestrado("Aluno Atrasado", -30)
        with mock.patch("acompanhamento.avisos.sleep"), mock.patch(
            "acompanhamento.avisos.send_mail", side_effect=OSError("fora do ar")
        ), self.assertLogs(LOG, level="WARNING"):
            rodar_aviso_diario(HOJE)

        execucao = ExecucaoAviso.objects.get()
        self.assertEqual(execucao.avisos_novos, 1)
        self.assertEqual(execucao.emails_enviados, 0)
        self.assertEqual(execucao.avisos_pendentes, 1)

    @override_settings(ACOMPANHAMENTO_ATIVO=True, ACOMPANHAMENTO_AVISO_ATIVO=False)
    def test_com_o_aviso_desligado_a_rotina_nao_registra_nada(self):
        aluno_de_mestrado("Aluno Atrasado", -30)
        enviar_avisos_irregularidade()

        self.assertEqual(ExecucaoAviso.objects.count(), 0)


@override_settings(ACOMPANHAMENTO_ATIVO=True, SECURE_SSL_REDIRECT=False)
class InicioMostraOUltimoAvisoTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.perfis = criar_perfis()
        cls.url = reverse("acompanhamento:acompanhamento_inicio")

    def abrir(self):
        self.client.force_login(self.perfis["membro_secretaria"])
        return self.client.get(self.url)

    @override_settings(ACOMPANHAMENTO_AVISO_ATIVO=True)
    def test_mostra_quando_o_aviso_rodou_pela_ultima_vez(self):
        ExecucaoAviso.objects.create(avisos_novos=2, emails_enviados=1, avisos_pendentes=3)
        execucao = ExecucaoAviso.objects.get()
        quando = timezone.localtime(execucao.executada_em).strftime("%d/%m/%Y %H:%M")

        resposta = self.abrir()

        self.assertContains(resposta, f"Último aviso rodou em {quando}")
        self.assertContains(resposta, "2 aviso(s) novo(s)")
        self.assertContains(resposta, "1 e-mail(s) enviado(s)")
        self.assertContains(resposta, "3 pendente(s)")

    @override_settings(ACOMPANHAMENTO_AVISO_ATIVO=True)
    def test_mostra_a_execucao_mais_recente(self):
        antiga = ExecucaoAviso.objects.create(avisos_novos=9)
        ExecucaoAviso.objects.filter(pk=antiga.pk).update(
            executada_em=timezone.now() - timedelta(days=3)
        )
        ExecucaoAviso.objects.create(avisos_novos=4)

        resposta = self.abrir()

        self.assertContains(resposta, "4 aviso(s) novo(s)")
        self.assertNotContains(resposta, "9 aviso(s) novo(s)")

    @override_settings(ACOMPANHAMENTO_AVISO_ATIVO=True)
    def test_aviso_que_nunca_rodou(self):
        resposta = self.abrir()

        self.assertContains(resposta, "O aviso diário ainda não rodou.")
        self.assertNotContains(resposta, "Último aviso rodou em")

    @override_settings(ACOMPANHAMENTO_AVISO_ATIVO=False)
    def test_aviso_desligado(self):
        ExecucaoAviso.objects.create(avisos_novos=1)

        resposta = self.abrir()

        self.assertContains(resposta, "O aviso diário de alunos atrasados está desligado.")
        self.assertNotContains(resposta, "Último aviso rodou em")
