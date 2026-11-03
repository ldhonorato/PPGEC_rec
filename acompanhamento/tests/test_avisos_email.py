"""O que o e-mail do aviso traz e o que fica gravado quando ele não sai."""

from datetime import date
from unittest import mock

from django.core import mail
from django.test import TestCase

from acompanhamento.avisos import rodar_aviso_diario
from acompanhamento.models import AvisoIrregularidade
from acompanhamento.tests.test_avisos import HOJE, aluno_de_mestrado, membro
from processos.models import Setor, SetorMembro, TrajetoriaAcademica


class ConteudoDoEmailTests(TestCase):
    def setUp(self):
        # As migrations já criam alunos de exemplo; eles atrapalhariam a contagem.
        TrajetoriaAcademica.objects.all().delete()
        membro(Setor.NOME_SECRETARIA, "Pessoa Secretaria")
        membro(Setor.NOME_COORDENACAO, "Pessoa Coordenacao")

    def test_email_traz_data_limite_reapresentacao_motivo_e_link(self):
        trajetoria = aluno_de_mestrado("Aluno Atrasado", -30)
        rodar_aviso_diario(HOJE)

        html = mail.outbox[0].alternatives[0][0]
        aviso = AvisoIrregularidade.objects.get()
        self.assertEqual(aviso.data_limite, date(2026, 9, 19))
        self.assertEqual(aviso.prazo_reapresentacao, date(2026, 12, 19))
        self.assertIn("19/09/2026", html)
        self.assertIn("19/12/2026", html)
        self.assertIn(aviso.motivo, html)
        self.assertTrue(aviso.motivo)
        self.assertIn(f"/coordenacao/alunos/{trajetoria.aluno_id}/", html)

    def test_primeiro_email_e_o_levantamento_inicial_e_o_segundo_nao(self):
        aluno_de_mestrado("Primeiro Atrasado", -30)
        rodar_aviso_diario(HOJE)
        aluno_de_mestrado("Segundo Atrasado", -10)
        rodar_aviso_diario(HOJE)

        primeiro, segundo = mail.outbox
        self.assertIn("Levantamento inicial", primeiro.subject)
        self.assertIn("Levantamento inicial", primeiro.alternatives[0][0])
        self.assertNotIn("Levantamento inicial", segundo.subject)
        self.assertNotIn("Levantamento inicial", segundo.alternatives[0][0])

    def test_levantamento_inicial_continua_se_o_primeiro_envio_falhar(self):
        aluno_de_mestrado("Aluno Atrasado", -30)
        with mock.patch("acompanhamento.avisos.sleep"), mock.patch(
            "acompanhamento.avisos.send_mail", side_effect=OSError("fora do ar")
        ), self.assertLogs("acompanhamento.avisos", level="WARNING"):
            rodar_aviso_diario(HOJE)
        rodar_aviso_diario(HOJE)

        self.assertIn("Levantamento inicial", mail.outbox[0].subject)

    def test_aluno_atrasado_nos_dois_marcos_conta_como_um_aluno(self):
        trajetoria = aluno_de_mestrado("Aluno Atrasado", -30)
        trajetoria.data_ingresso = date(2024, 1, 1)  # defesa venceu em 31/01/2026
        trajetoria.save()
        rodar_aviso_diario(HOJE)

        self.assertEqual(AvisoIrregularidade.objects.count(), 2)
        self.assertIn(" 1 aluno(s)", mail.outbox[0].subject)


class MotivoDaPendenciaTests(TestCase):
    def setUp(self):
        TrajetoriaAcademica.objects.all().delete()

    def test_tres_falhas_gravam_as_tentativas_e_o_erro(self):
        membro(Setor.NOME_SECRETARIA, "Pessoa Secretaria")
        aluno_de_mestrado("Aluno Atrasado", -30)
        with mock.patch("acompanhamento.avisos.sleep"), mock.patch(
            "acompanhamento.avisos.send_mail", side_effect=OSError("fora do ar")
        ), self.assertLogs("acompanhamento.avisos", level="WARNING"):
            rodar_aviso_diario(HOJE)

        aviso = AvisoIrregularidade.objects.get()
        self.assertIsNone(aviso.enviado_em)
        self.assertEqual(aviso.tentativas_envio, 3)
        self.assertIn("fora do ar", aviso.motivo_pendencia)

    def test_sem_ninguem_nos_setores_grava_a_falta_de_destinatario(self):
        SetorMembro.objects.filter(
            setor__nome__in=[Setor.NOME_SECRETARIA, Setor.NOME_COORDENACAO]
        ).delete()
        aluno_de_mestrado("Aluno Atrasado", -30)
        with self.assertLogs("acompanhamento.avisos", level="WARNING"):
            rodar_aviso_diario(HOJE)

        aviso = AvisoIrregularidade.objects.get()
        self.assertIsNone(aviso.enviado_em)
        self.assertIn("Ninguém na Secretaria", aviso.motivo_pendencia)
