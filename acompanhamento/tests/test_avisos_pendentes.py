"""O aviso não pode se perder: nova tentativa, pendência e mudança de situação."""

from unittest import mock

from django.core import mail
from django.test import TestCase

from acompanhamento.avisos import TENTATIVAS_ENVIO, rodar_aviso_diario
from acompanhamento.models import AvisoIrregularidade
from acompanhamento.tests.test_avisos import (
    HOJE,
    aluno_de_mestrado,
    membro,
    mudar_limite,
)
from processos.models import Setor, SetorMembro, TrajetoriaAcademica

ENVIO = "acompanhamento.avisos.send_mail"
PAUSA = "acompanhamento.avisos.sleep"
LOG = "acompanhamento.avisos"


class ReenvioTests(TestCase):
    def setUp(self):
        # As migrations já criam alunos de exemplo; eles atrapalhariam a contagem.
        TrajetoriaAcademica.objects.all().delete()
        self.secretaria = membro(Setor.NOME_SECRETARIA, "Pessoa Secretaria")
        self.coordenacao = membro(Setor.NOME_COORDENACAO, "Pessoa Coordenacao")

    def test_falha_e_tenta_de_novo_ate_conseguir(self):
        aluno_de_mestrado("Aluno Atrasado", -30)
        with mock.patch(PAUSA), mock.patch(
            ENVIO, side_effect=[OSError("fora do ar"), OSError("fora do ar"), None]
        ) as envio, self.assertLogs(LOG, level="WARNING"):
            resultado = rodar_aviso_diario(HOJE)

        self.assertEqual(envio.call_count, 3)
        self.assertEqual(resultado, {"avisos_novos": 1, "emails_enviados": 1})
        self.assertIsNotNone(AvisoIrregularidade.objects.get().enviado_em)

    def test_tenta_no_maximo_tres_vezes_e_o_aviso_fica_pendente(self):
        aluno_de_mestrado("Aluno Atrasado", -30)
        with mock.patch(PAUSA) as pausa, mock.patch(
            ENVIO, side_effect=OSError("fora do ar")
        ) as envio, self.assertLogs(LOG, level="WARNING"):
            resultado = rodar_aviso_diario(HOJE)

        self.assertEqual(TENTATIVAS_ENVIO, 3)
        self.assertEqual(envio.call_count, 3)
        self.assertEqual(pausa.call_count, 2)
        self.assertEqual(resultado, {"avisos_novos": 1, "emails_enviados": 0})
        aviso = AvisoIrregularidade.objects.get()
        self.assertIsNone(aviso.enviado_em)
        self.assertIsNone(aviso.encerrado_em)

    def test_aviso_pendente_entra_no_email_do_dia_seguinte(self):
        aluno_de_mestrado("Primeiro Atrasado", -30)
        with mock.patch(PAUSA), mock.patch(
            ENVIO, side_effect=OSError("fora do ar")
        ), self.assertLogs(LOG, level="WARNING"):
            rodar_aviso_diario(HOJE)

        aluno_de_mestrado("Segundo Atrasado", -10)
        resultado = rodar_aviso_diario(HOJE)

        self.assertEqual(resultado, {"avisos_novos": 1, "emails_enviados": 1})
        self.assertEqual(len(mail.outbox), 1)
        html = mail.outbox[0].alternatives[0][0]
        self.assertIn("Primeiro Atrasado", html)
        self.assertIn("Segundo Atrasado", html)
        self.assertFalse(
            AvisoIrregularidade.objects.filter(enviado_em__isnull=True).exists()
        )

    def test_depois_de_enviado_o_aviso_nao_volta_a_ser_mandado(self):
        aluno_de_mestrado("Aluno Atrasado", -30)
        with mock.patch(PAUSA), mock.patch(
            ENVIO, side_effect=OSError("fora do ar")
        ), self.assertLogs(LOG, level="WARNING"):
            rodar_aviso_diario(HOJE)
        rodar_aviso_diario(HOJE)
        resultado = rodar_aviso_diario(HOJE)

        self.assertEqual(resultado, {"avisos_novos": 0, "emails_enviados": 0})
        self.assertEqual(len(mail.outbox), 1)

    def test_aviso_encerrado_antes_do_envio_nao_e_mandado(self):
        trajetoria = aluno_de_mestrado("Aluno Atrasado", -30)
        with mock.patch(PAUSA), mock.patch(
            ENVIO, side_effect=OSError("fora do ar")
        ), self.assertLogs(LOG, level="WARNING"):
            rodar_aviso_diario(HOJE)
        mudar_limite(trajetoria, 30)
        resultado = rodar_aviso_diario(HOJE)

        self.assertEqual(resultado, {"avisos_novos": 0, "emails_enviados": 0})
        self.assertEqual(len(mail.outbox), 0)


class SemDestinatarioTests(TestCase):
    def setUp(self):
        TrajetoriaAcademica.objects.all().delete()
        SetorMembro.objects.filter(
            setor__nome__in=[Setor.NOME_SECRETARIA, Setor.NOME_COORDENACAO]
        ).delete()

    def test_sem_ninguem_nos_setores_o_aviso_fica_pendente(self):
        aluno_de_mestrado("Aluno Atrasado", -30)
        with self.assertLogs(LOG, level="WARNING"):
            resultado = rodar_aviso_diario(HOJE)

        self.assertEqual(resultado, {"avisos_novos": 1, "emails_enviados": 0})
        self.assertEqual(len(mail.outbox), 0)
        self.assertIsNone(AvisoIrregularidade.objects.get().enviado_em)

    def test_quando_aparece_alguem_o_aviso_pendente_e_mandado(self):
        aluno_de_mestrado("Aluno Atrasado", -30)
        with self.assertLogs(LOG, level="WARNING"):
            rodar_aviso_diario(HOJE)
        membro(Setor.NOME_SECRETARIA, "Pessoa Secretaria")
        resultado = rodar_aviso_diario(HOJE)

        self.assertEqual(resultado, {"avisos_novos": 0, "emails_enviados": 1})
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("Aluno Atrasado", mail.outbox[0].alternatives[0][0])
        self.assertIsNotNone(AvisoIrregularidade.objects.get().enviado_em)


class MudancaDeSituacaoTests(TestCase):
    def setUp(self):
        TrajetoriaAcademica.objects.all().delete()
        membro(Setor.NOME_SECRETARIA, "Pessoa Secretaria")
        membro(Setor.NOME_COORDENACAO, "Pessoa Coordenacao")

    def test_de_com_chance_para_sem_chance_gera_aviso_novo(self):
        trajetoria = aluno_de_mestrado("Aluno Atrasado", -30)
        rodar_aviso_diario(HOJE)
        mudar_limite(trajetoria, -120)
        resultado = rodar_aviso_diario(HOJE)

        self.assertEqual(resultado, {"avisos_novos": 1, "emails_enviados": 1})
        self.assertEqual(AvisoIrregularidade.objects.count(), 2)
        aberto = AvisoIrregularidade.objects.get(encerrado_em__isnull=True)
        self.assertEqual(aberto.situacao, AvisoIrregularidade.Situacao.SEM_CHANCE)
        antigo = AvisoIrregularidade.objects.get(encerrado_em__isnull=False)
        self.assertEqual(antigo.situacao, AvisoIrregularidade.Situacao.COM_CHANCE)

    def test_o_email_da_mudanca_traz_so_o_aviso_novo(self):
        trajetoria = aluno_de_mestrado("Aluno Atrasado", -30)
        rodar_aviso_diario(HOJE)
        mudar_limite(trajetoria, -120)
        rodar_aviso_diario(HOJE)

        self.assertEqual(len(mail.outbox), 2)
        segundo = mail.outbox[1].alternatives[0][0]
        self.assertIn("Atrasado sem chance", segundo)
        self.assertNotIn("Atrasado com chance", segundo)
        
