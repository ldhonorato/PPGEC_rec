"""Testes do comando que mostra o e-mail do aviso sem gravar e sem enviar."""

import tempfile
from io import StringIO
from pathlib import Path
from unittest import mock

from django.core import mail
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from acompanhamento.avisos import rodar_aviso_diario
from acompanhamento.models import AvisoIrregularidade, ExecucaoAviso
from acompanhamento.tests.test_avisos import HOJE, aluno_de_mestrado, membro
from processos.models import Setor, SetorMembro, TrajetoriaAcademica

LOG = "acompanhamento.avisos"


class MostrarAvisoDoDiaTests(TestCase):
    def setUp(self):
        # As migrations já criam alunos de exemplo; eles atrapalhariam a contagem.
        TrajetoriaAcademica.objects.all().delete()
        self.secretaria = membro(Setor.NOME_SECRETARIA, "Pessoa Secretaria")
        self.coordenacao = membro(Setor.NOME_COORDENACAO, "Pessoa Coordenacao")

    def rodar(self, *argumentos):
        saida = StringIO()
        call_command(
            "mostrar_aviso_do_dia", "--data", HOJE.isoformat(), *argumentos, stdout=saida
        )
        return saida.getvalue()

    def test_mostra_o_aluno_o_assunto_e_os_destinatarios(self):
        aluno_de_mestrado("Aluno Atrasado", -30)
        saida = self.rodar()

        self.assertIn("Aluno Atrasado", saida)
        self.assertIn("1 aluno(s) com prazo vencido", saida)
        self.assertIn(self.secretaria.email, saida)
        self.assertIn(self.coordenacao.email, saida)
        self.assertIn("Atrasado com chance", saida)

    def test_nao_grava_nada_e_nao_envia(self):
        aluno_de_mestrado("Aluno Atrasado", -30)
        self.rodar()

        self.assertEqual(AvisoIrregularidade.objects.count(), 0)
        self.assertEqual(ExecucaoAviso.objects.count(), 0)
        self.assertEqual(len(mail.outbox), 0)

    def test_pode_rodar_varias_vezes_com_o_mesmo_resultado(self):
        aluno_de_mestrado("Aluno Atrasado", -30)
        primeira = self.rodar()
        segunda = self.rodar()

        self.assertEqual(primeira, segunda)
        self.assertEqual(AvisoIrregularidade.objects.count(), 0)

    def test_sem_aluno_atrasado_diz_que_nenhum_email_sairia(self):
        aluno_de_mestrado("Aluno em Dia", 30)
        saida = self.rodar()

        self.assertIn("Nenhum aviso para enviar", saida)
        self.assertNotIn("Assunto:", saida)

    def test_mostra_tambem_o_aviso_pendente_de_dias_anteriores(self):
        aluno_de_mestrado("Primeiro Atrasado", -30)
        with mock.patch("acompanhamento.avisos.sleep"), mock.patch(
            "acompanhamento.avisos.send_mail", side_effect=OSError("fora do ar")
        ), self.assertLogs(LOG, level="WARNING"):
            rodar_aviso_diario(HOJE)
        aluno_de_mestrado("Segundo Atrasado", -10)
        execucoes_antes = ExecucaoAviso.objects.count()

        saida = self.rodar()

        self.assertIn("Primeiro Atrasado", saida)
        self.assertIn("Segundo Atrasado", saida)
        self.assertIn("1 novo(s), 1 pendente(s) de dias anteriores", saida)
        self.assertEqual(ExecucaoAviso.objects.count(), execucoes_antes)
        self.assertEqual(AvisoIrregularidade.objects.count(), 1)

    def test_avisa_quando_ninguem_vai_receber(self):
        SetorMembro.objects.filter(
            setor__nome__in=[Setor.NOME_SECRETARIA, Setor.NOME_COORDENACAO]
        ).delete()
        aluno_de_mestrado("Aluno Atrasado", -30)
        saida = self.rodar()

        self.assertIn("Destinatários: NENHUM", saida)
        self.assertIn("Aluno Atrasado", saida)

    def test_html_grava_o_corpo_do_email_no_arquivo(self):
        aluno_de_mestrado("Aluno Atrasado", -30)
        with tempfile.TemporaryDirectory() as pasta:
            caminho = Path(pasta) / "email.html"
            saida = self.rodar("--html", str(caminho))

            self.assertIn(str(caminho), saida)
            self.assertIn("Aluno Atrasado", caminho.read_text(encoding="utf-8"))
        self.assertEqual(AvisoIrregularidade.objects.count(), 0)

    def test_data_invalida_e_recusada(self):
        with self.assertRaises(CommandError):
            call_command("mostrar_aviso_do_dia", "--data", "22/10/2026", stdout=StringIO())
