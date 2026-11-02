"""Testes do comando gerar_dados_ficticios."""

from datetime import date, timedelta
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from processos.models import Docente, Polo, Processo, TrajetoriaAcademica, User

DOMINIO = "@ficticio.invalid"


def rodar(*args):
    saida = StringIO()
    call_command("gerar_dados_ficticios", *args, stdout=saida)
    return saida.getvalue()


@override_settings(DEBUG=True)
class GerarDadosFicticiosTests(TestCase):
    def test_recusa_com_debug_desligado_antes_de_escrever(self):
        with override_settings(DEBUG=False):
            with self.assertRaises(CommandError):
                rodar()
        self.assertFalse(User.objects.filter(email__endswith=DOMINIO).exists())

    def test_todos_os_usuarios_criados_usam_o_dominio_ficticio(self):
        antes = set(User.objects.values_list("pk", flat=True))
        rodar()
        novos = User.objects.exclude(pk__in=antes)
        self.assertTrue(novos.exists())
        for email in novos.values_list("email", flat=True):
            self.assertTrue(email.endswith(DOMINIO), email)

    def test_docentes_de_cada_combinacao_em_cada_polo_e_sem_polo(self):
        rodar()
        ficticios = Docente.objects.filter(email__endswith=DOMINIO)
        for polo in list(Polo.objects.filter(ativo=True)) + [None]:
            for permanente in (True, False):
                for externo in (True, False):
                    self.assertTrue(
                        ficticios.filter(
                            polo_atuacao=polo, permanente=permanente, externo=externo
                        ).exists(),
                        f"faltou polo={polo} permanente={permanente} externo={externo}",
                    )

    def test_processos_de_cada_tipo_abertos_e_finalizados_em_seis_meses(self):
        referencia = date(2026, 10, 5)
        rodar("--data-referencia", referencia.isoformat())
        processos = Processo.objects.filter(usuario_criado_por__email__endswith=DOMINIO)

        for tipo in Processo.TipoProcesso:
            do_tipo = processos.filter(tipo=tipo)
            self.assertTrue(do_tipo.filter(status=Processo.StatusProcesso.FINALIZADO).exists())
            self.assertTrue(do_tipo.exclude(status=Processo.StatusProcesso.FINALIZADO).exists())

        datas = [p.data_criacao.date() for p in processos]
        self.assertLessEqual(min(datas), referencia - timedelta(days=183))
        self.assertLessEqual(max(datas), referencia)

    def test_rodar_de_novo_substitui_em_vez_de_duplicar(self):
        rodar()
        total = User.objects.filter(email__endswith=DOMINIO).count()
        rodar()
        self.assertEqual(User.objects.filter(email__endswith=DOMINIO).count(), total)

    def test_mesma_semente_gera_os_mesmos_nomes(self):
        rodar("--semente", "7")
        primeira = sorted(User.objects.filter(email__endswith=DOMINIO).values_list("nome", flat=True))
        rodar("--semente", "7")
        segunda = sorted(User.objects.filter(email__endswith=DOMINIO).values_list("nome", flat=True))
        self.assertEqual(primeira, segunda)

    def test_apenas_remover_nao_toca_em_dados_reais(self):
        real = User.objects.create_user(email="real@exemplo.com", nome="Pessoa Real")
        rodar()
        rodar("--apenas-remover")
        self.assertFalse(User.objects.filter(email__endswith=DOMINIO).exists())
        self.assertFalse(Polo.objects.filter(nome__startswith="Fictício").exists())
        self.assertTrue(User.objects.filter(pk=real.pk).exists())

    def test_resumo_impresso_por_categoria(self):
        saida = rodar()
        self.assertIn("Docentes permanentes internos", saida)
        self.assertIn("Processos finalizados", saida)

    def test_falha_no_meio_desfaz_tudo(self):
        rodar()
        antes = User.objects.filter(email__endswith=DOMINIO).count()
        with patch(
            "acompanhamento.management.commands.gerar_dados_ficticios.Command.criar_processos",
            side_effect=RuntimeError("falha forçada"),
        ):
            with self.assertRaises(RuntimeError):
                rodar()
        self.assertEqual(User.objects.filter(email__endswith=DOMINIO).count(), antes)
    
    def test_cria_os_alunos_com_trajetoria_sem_duplicar(self):
        rodar()
        rodar()
        trajetorias = TrajetoriaAcademica.objects.filter(aluno__email__endswith=DOMINIO)
        self.assertEqual(trajetorias.count(), 32)