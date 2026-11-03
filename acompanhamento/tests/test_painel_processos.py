from datetime import date, datetime, timedelta
from django.test import TestCase
from django.utils import timezone
from acompanhamento.servicos import calcular_painel_processos
from processos.models import Aluno, Processo, Setor, User


class PainelProcessosServicoTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.setor = Setor.objects.create(
            nome="Coordenação de Teste",
            tipo=Setor.TipoSetor.SETOR,
        )
        cls.servidor = User.objects.create_user(
            email="servidor.teste@ficticio.invalid",
            nome="Servidor Teste",
            tipo_usuario=User.TipoUsuario.SERVIDOR,
        )
        cls.aluno = Aluno.objects.create_user(
            email="aluno.teste@ficticio.invalid",
            nome="Aluno Teste",
            status_aluno=Aluno.StatusAluno.ATIVO,
        )

    def setUp(self):
        Processo.objects.all().delete()
        hoje = timezone.localdate()
        self.semestre_atual = f"{hoje.year}.{'1' if hoje.month <= 6 else '2'}"

    def test_semestre_invalido_levanta_excecao(self):
        """Verifica se semestre inválido levanta ValueError."""
        with self.assertRaises(ValueError):
            calcular_painel_processos(semestre="2026")
        with self.assertRaises(ValueError):
            calcular_painel_processos(semestre="invalido")

    def test_intervalo_personalizado_prevalece_sobre_semestre(self):
        for data, tipo, assunto in (
            (datetime(2025, 1, 1), Processo.TipoProcesso.OUTRO, "Janeiro 1"),
            (datetime(2025, 1, 31, 23, 59), Processo.TipoProcesso.PRORROGACAO_PRAZO, "Janeiro 2"),
            (datetime(2025, 2, 1, 23, 59), Processo.TipoProcesso.TRANCAMENTO_MATRICULA, "Fevereiro"),
            (datetime(2025, 2, 2), Processo.TipoProcesso.OUTRO, "Fora do período"),
        ):
            processo = Processo.objects.create(
                usuario_criado_por=self.servidor,
                aluno_interessado=self.aluno,
                tipo=tipo,
                status=Processo.StatusProcesso.EM_ANALISE,
                assunto=assunto,
                descricao="Teste",
                setor_atual=self.setor,
            )
            Processo.objects.filter(pk=processo.pk).update(
                data_criacao=timezone.make_aware(data)
            )

        dados = calcular_painel_processos(
            semestre="semestre ignorado",
            data_inicio=date(2025, 1, 1),
            data_fim=date(2025, 2, 1),
        )

        self.assertEqual(dados["total_processos"], 3)
        self.assertEqual(dados["por_tipo"]["Outro"], 1)
        self.assertEqual(dados["por_tipo"]["Prorrogação de Prazo"], 1)
        self.assertEqual(dados["por_tipo"]["Trancamento de Matrícula"], 1)
        self.assertEqual(dados["abertos_por_mes"], {"2025-01": 2, "2025-02": 1})
        self.assertEqual(
            {
                (item["mes"], item["tipo"], item["total"])
                for item in dados["por_tipo_por_mes"]
            },
            {
                ("2025-01", "Outro", 1),
                ("2025-01", "Prorrogação de Prazo", 1),
                ("2025-02", "Trancamento de Matrícula", 1),
            },
        )

    def test_intervalo_personalizado_exige_as_duas_datas(self):
        with self.assertRaisesMessage(ValueError, "Informe as datas inicial e final."):
            calcular_painel_processos(data_inicio=date(2025, 1, 1))

    def test_intervalo_personalizado_rejeita_data_inicial_posterior(self):
        with self.assertRaisesMessage(
            ValueError,
            "A data inicial deve ser anterior ou igual à data final.",
        ):
            calcular_painel_processos(
                data_inicio=date(2025, 2, 1),
                data_fim=date(2025, 1, 1),
            )

    def test_intervalo_personalizado_rejeita_mais_de_dez_anos(self):
        with self.assertRaisesMessage(
            ValueError,
            "O intervalo pode ter no máximo 10 anos.",
        ):
            calcular_painel_processos(
                data_inicio=date(202, 1, 1),
                data_fim=date(2026, 12, 31),
            )

    def test_intervalo_personalizado_aceita_exatamente_dez_anos(self):
        dados = calcular_painel_processos(
            data_inicio=date(2020, 1, 1),
            data_fim=date(2029, 12, 31),
        )

        self.assertEqual(len(dados["abertos_por_mes"]), 120)

    def test_data_final_maxima_nao_estoura(self):
        processo = Processo.objects.create(
            usuario_criado_por=self.servidor,
            aluno_interessado=self.aluno,
            tipo=Processo.TipoProcesso.OUTRO,
            status=Processo.StatusProcesso.EM_ANALISE,
            assunto="Último dia do calendário",
            descricao="Teste",
            setor_atual=self.setor,
        )
        Processo.objects.filter(pk=processo.pk).update(
            data_criacao=timezone.make_aware(datetime(9999, 12, 31, 12))
        )

        dados = calcular_painel_processos(
            data_inicio=date(9999, 12, 31),
            data_fim=date(9999, 12, 31),
        )

        self.assertEqual(dados["total_processos"], 1)
        self.assertEqual(dados["abertos_por_mes"], {"9999-12": 1})

    def test_data_final_do_intervalo_permanece_apos_calcular_tempo_medio(self):
        processo = Processo.objects.create(
            usuario_criado_por=self.servidor,
            aluno_interessado=self.aluno,
            tipo=Processo.TipoProcesso.OUTRO,
            status=Processo.StatusProcesso.FINALIZADO,
            assunto="Processo finalizado",
            descricao="Teste",
            setor_atual=self.setor,
        )
        data_inicio_processo = timezone.make_aware(datetime(2025, 1, 10))
        data_finalizacao = timezone.make_aware(datetime(2025, 1, 15))
        Processo.objects.filter(pk=processo.pk).update(
            data_criacao=data_inicio_processo,
            finalizado_em=data_finalizacao,
        )

        dados = calcular_painel_processos(
            data_inicio=date(2025, 1, 1),
            data_fim=date(2025, 1, 31),
        )

        self.assertEqual(dados["data_fim"], date(2025, 1, 31))
        self.assertEqual(dados["tempo_medio_dias"], 5.0)

    def test_quantidade_por_tipo_e_status(self):
        """Verifica a contagem correta por tipo e por status de processo."""
        Processo.objects.create(
            usuario_criado_por=self.servidor,
            aluno_interessado=self.aluno,
            tipo=Processo.TipoProcesso.PRORROGACAO_PRAZO,
            status=Processo.StatusProcesso.EM_ANALISE,
            assunto="Prorrogação 1",
            descricao="Teste",
            setor_atual=self.setor,
        )
        Processo.objects.create(
            usuario_criado_por=self.servidor,
            aluno_interessado=self.aluno,
            tipo=Processo.TipoProcesso.PRORROGACAO_PRAZO,
            status=Processo.StatusProcesso.AGUARDANDO_DOCUMENTO,
            assunto="Prorrogação 2",
            descricao="Teste",
            setor_atual=self.setor,
        )
        Processo.objects.create(
            usuario_criado_por=self.servidor,
            aluno_interessado=self.aluno,
            tipo=Processo.TipoProcesso.TRANCAMENTO_MATRICULA,
            status=Processo.StatusProcesso.EM_ANALISE,
            assunto="Trancamento 1",
            descricao="Teste",
            setor_atual=self.setor,
        )

        dados = calcular_painel_processos()

        self.assertEqual(dados["total_processos"], 3)
        self.assertEqual(dados["por_tipo"]["Prorrogação de Prazo"], 2)
        self.assertEqual(dados["por_tipo"]["Trancamento de Matrícula"], 1)
        self.assertEqual(dados["por_status"]["Em análise"], 2)
        self.assertEqual(dados["por_status"]["Aguardando documento"], 1)

    def test_processos_abertos_por_mes_inclui_meses_zerados(self):
        """Verifica o agrupamento mensal com meses vazios inicializados em 0."""
        proc = Processo.objects.create(
            usuario_criado_por=self.servidor,
            aluno_interessado=self.aluno,
            tipo=Processo.TipoProcesso.OUTRO,
            status=Processo.StatusProcesso.EM_ANALISE,
            assunto="Processo Mensal",
            descricao="Teste",
            setor_atual=self.setor,
        )
        mes_esperado = timezone.localtime(proc.data_criacao).strftime("%Y-%m")

        dados = calcular_painel_processos()

        self.assertEqual(len(dados["abertos_por_mes"]), 6)
        self.assertEqual(dados["abertos_por_mes"][mes_esperado], 1)
        # Os outros 5 meses devem ser 0
        meses_zerados = [m for k, m in dados["abertos_por_mes"].items() if k != mes_esperado]
        self.assertTrue(all(qtd == 0 for qtd in meses_zerados))

    def test_tempo_medio_de_resolucao(self):
        """Verifica o cálculo em dias do tempo médio de resolução para finalizados."""
        agora = timezone.now()
        proc1 = Processo.objects.create(
            usuario_criado_por=self.servidor,
            aluno_interessado=self.aluno,
            tipo=Processo.TipoProcesso.OUTRO,
            status=Processo.StatusProcesso.FINALIZADO,
            assunto="Finalizado 1",
            descricao="Teste",
            setor_atual=self.setor,
            finalizado_em=agora,
        )
        Processo.objects.filter(pk=proc1.pk).update(data_criacao=agora - timedelta(days=4))

        proc2 = Processo.objects.create(
            usuario_criado_por=self.servidor,
            aluno_interessado=self.aluno,
            tipo=Processo.TipoProcesso.OUTRO,
            status=Processo.StatusProcesso.FINALIZADO,
            assunto="Finalizado 2",
            descricao="Teste",
            setor_atual=self.setor,
            finalizado_em=agora,
        )
        Processo.objects.filter(pk=proc2.pk).update(data_criacao=agora - timedelta(days=2))

        dados = calcular_painel_processos()

        self.assertEqual(dados["tempo_medio_dias"], 3.0)

    def test_tempo_medio_sem_finalizados_retorna_none(self):
        """Verifica se retorna None quando não houver processos finalizados."""
        Processo.objects.create(
            usuario_criado_por=self.servidor,
            aluno_interessado=self.aluno,
            tipo=Processo.TipoProcesso.OUTRO,
            status=Processo.StatusProcesso.EM_ANALISE,
            assunto="Em aberto",
            descricao="Teste",
            setor_atual=self.setor,
        )
        dados = calcular_painel_processos()
        self.assertIsNone(dados["tempo_medio_dias"])

    def test_processos_em_atraso(self):
        """Verifica a identificação de processos com prazo_limite vencido e não finalizados."""
        hoje = timezone.localdate()

        Processo.objects.create(
            usuario_criado_por=self.servidor,
            aluno_interessado=self.aluno,
            tipo=Processo.TipoProcesso.OUTRO,
            status=Processo.StatusProcesso.EM_ANALISE,
            assunto="Atrasado",
            descricao="Teste",
            setor_atual=self.setor,
            prazo_limite=hoje - timedelta(days=3),
        )

        Processo.objects.create(
            usuario_criado_por=self.servidor,
            aluno_interessado=self.aluno,
            tipo=Processo.TipoProcesso.OUTRO,
            status=Processo.StatusProcesso.EM_ANALISE,
            assunto="Em dia",
            descricao="Teste",
            setor_atual=self.setor,
            prazo_limite=hoje + timedelta(days=5),
        )

        Processo.objects.create(
            usuario_criado_por=self.servidor,
            aluno_interessado=self.aluno,
            tipo=Processo.TipoProcesso.OUTRO,
            status=Processo.StatusProcesso.FINALIZADO,
            assunto="Finalizado fora do prazo",
            descricao="Teste",
            setor_atual=self.setor,
            prazo_limite=hoje - timedelta(days=10),
            finalizado_em=timezone.now(),
        )

        dados = calcular_painel_processos()

        self.assertEqual(dados["total_atrasados"], 1)
        