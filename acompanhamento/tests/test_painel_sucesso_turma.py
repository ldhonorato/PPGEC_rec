from datetime import date
from django.test import TestCase
from acompanhamento.lista_alunos import (
    DEFESA,
    IRREGULARES,
    MARCO_INTERMEDIARIO,
    listar_alunos_por_marco,
)
from acompanhamento.models import MarcoCalendario
from acompanhamento.servicos import calcular_painel_sucesso_turma
from processos.models import Aluno, Processo, ProrrogacaoTrajetoria, Setor, TrajetoriaAcademica, User


class PainelSucessoTurmaServicoTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.setor = Setor.objects.create(
            nome="Coordenação Acadêmica Teste",
            tipo=Setor.TipoSetor.SETOR,
        )
        cls.servidor = User.objects.create_user(
            email="servidor.sucesso.isolado@teste.invalid",
            nome="Servidor Teste",
            tipo_usuario=User.TipoUsuario.SERVIDOR,
        )

    def test_irregulares_no_marco_e_conferencia_com_tela_de_alunos(self):
        """
        Confere que alunos irregulares no marco (com chance e sem chance) contam como irregulares
        e conferem exatamente com a soma de irregulares da tela de alunos (listar_alunos_por_marco).
        """
        turma = "2026.2"
        data_ref = date(2026, 11, 1)

        MarcoCalendario.objects.create(
            semestre="2026.2",
            tipo=MarcoCalendario.TipoMarco.PROJETO_DISSERTACAO,
            data_limite=date(2026, 9, 20),
        )

        a_marco_com_chance = Aluno.objects.create_user(email="m_chance@teste.invalid", nome="Marco Com Chance")
        TrajetoriaAcademica.objects.create(
            aluno=a_marco_com_chance,
            nivel_curso=Aluno.NivelCurso.MESTRADO,
            status=TrajetoriaAcademica.Status.ATIVA,
            ingresso=turma,
            data_ingresso=date(2026, 8, 1),
            prazo_qualificacao="2026.2",
        )

        a_marco_sem_chance = Aluno.objects.create_user(email="m_sem_chance@teste.invalid", nome="Marco Sem Chance")
        TrajetoriaAcademica.objects.create(
            aluno=a_marco_sem_chance,
            nivel_curso=Aluno.NivelCurso.MESTRADO,
            status=TrajetoriaAcademica.Status.ATIVA,
            ingresso=turma,
            data_ingresso=date(2026, 8, 1),
            prazo_qualificacao="2026.2",
            data_limite_qualificacao=date(2026, 7, 10),
        )

        a_defesa = Aluno.objects.create_user(email="defesa_irreg@teste.invalid", nome="Defesa Irregular")
        TrajetoriaAcademica.objects.create(
            aluno=a_defesa,
            nivel_curso=Aluno.NivelCurso.MESTRADO,
            status=TrajetoriaAcademica.Status.ATIVA,
            ingresso=turma,
            data_ingresso=date(2024, 1, 1),
        )

        a_regular = Aluno.objects.create_user(email="ativo_reg@teste.invalid", nome="Ativo Regular")
        TrajetoriaAcademica.objects.create(
            aluno=a_regular,
            nivel_curso=Aluno.NivelCurso.MESTRADO,
            status=TrajetoriaAcademica.Status.ATIVA,
            ingresso=turma,
            data_ingresso=date(2026, 8, 1),
            prazo_qualificacao="2027.1",
        )

        dados = calcular_painel_sucesso_turma(Aluno.NivelCurso.MESTRADO, data_referencia=data_ref)
        turma_dados = next((t for t in dados if t["turma"] == turma), None)
        self.assertIsNotNone(turma_dados)
        self.assertEqual(turma_dados["ingressantes"], 4)
        self.assertEqual(turma_dados["ativos_regulares"], 1)
        self.assertEqual(turma_dados["ativos_irregulares"], 3)

        alunos = Aluno.objects.filter(
            pk__in=[a_marco_com_chance.pk, a_marco_sem_chance.pk, a_defesa.pk, a_regular.pk]
        )
        irregulares_na_tela = {
            linha.trajetoria.pk
            for marco in (MARCO_INTERMEDIARIO, DEFESA)
            for linha in listar_alunos_por_marco(
                alunos, marco, situacao=IRREGULARES, data_referencia=data_ref
            )
        }
        self.assertEqual(len(irregulares_na_tela), turma_dados["ativos_irregulares"])

    def test_separacao_por_nivel_e_exclusao_de_especial_e_homologacao(self):
        """Mestrado e doutorado saem separados; aluno especial e em homologação ficam fora."""
        turma = "2099.1"
        data_ref = date(2099, 5, 1)

        a_m = Aluno.objects.create_user(email="m1@teste.invalid", nome="M1")
        TrajetoriaAcademica.objects.create(
            aluno=a_m,
            nivel_curso=Aluno.NivelCurso.MESTRADO,
            status=TrajetoriaAcademica.Status.ATIVA,
            ingresso=turma,
            data_ingresso=date(2099, 3, 1),
        )

        a_d = Aluno.objects.create_user(email="d1@teste.invalid", nome="D1")
        TrajetoriaAcademica.objects.create(
            aluno=a_d,
            nivel_curso=Aluno.NivelCurso.DOUTORADO,
            status=TrajetoriaAcademica.Status.ATIVA,
            ingresso=turma,
            data_ingresso=date(2099, 3, 1),
        )

        a_esp = Aluno.objects.create_user(email="esp1@teste.invalid", nome="ESP1")
        TrajetoriaAcademica.objects.create(
            aluno=a_esp,
            nivel_curso=Aluno.NivelCurso.ALUNO_ESPECIAL,
            status=TrajetoriaAcademica.Status.CONCLUIDA,
            ingresso=turma,
            data_ingresso=date(2099, 3, 1),
        )

        a_hom = Aluno.objects.create_user(email="hom1@teste.invalid", nome="HOM1")
        TrajetoriaAcademica.objects.create(
            aluno=a_hom,
            nivel_curso=Aluno.NivelCurso.MESTRADO,
            status=TrajetoriaAcademica.Status.EM_HOMOLOGACAO,
            ingresso=turma,
            data_ingresso=date(2099, 3, 1),
        )

        dados_mestrado = calcular_painel_sucesso_turma(Aluno.NivelCurso.MESTRADO, data_referencia=data_ref)
        turma_m = next((t for t in dados_mestrado if t["turma"] == turma), None)
        self.assertIsNotNone(turma_m)
        self.assertEqual(turma_m["ingressantes"], 1)
        self.assertEqual(turma_m["ativos"], 1)

        dados_doutorado = calcular_painel_sucesso_turma(Aluno.NivelCurso.DOUTORADO, data_referencia=data_ref)
        turma_d = next((t for t in dados_doutorado if t["turma"] == turma), None)
        self.assertIsNotNone(turma_d)
        self.assertEqual(turma_d["ingressantes"], 1)

    def test_aluno_defendeu_com_doutorado_ativo_conta_como_ativo(self):
        """Aluno com status_aluno=DEFENDEU mas trajetória de doutorado ATIVA conta como ativo."""
        turma = "2099.2"
        data_ref = date(2099, 9, 1)

        aluno = Aluno.objects.create_user(
            email="defendeu_doutorado@teste.invalid",
            nome="Aluno Defendeu Doutorando",
            status_aluno=Aluno.StatusAluno.DEFENDEU,
        )
        TrajetoriaAcademica.objects.create(
            aluno=aluno,
            nivel_curso=Aluno.NivelCurso.DOUTORADO,
            status=TrajetoriaAcademica.Status.ATIVA,
            ingresso=turma,
            data_ingresso=date(2099, 8, 1),
        )

        dados = calcular_painel_sucesso_turma(Aluno.NivelCurso.DOUTORADO, data_referencia=data_ref)
        turma_d = next((t for t in dados if t["turma"] == turma), None)
        self.assertIsNotNone(turma_d)
        self.assertEqual(turma_d["concluidos"], 0)
        self.assertEqual(turma_d["ativos"], 1)

    def test_prorrogacao_registrada_vs_processo_em_analise(self):
        """Prorrogação só conta se registrada na trajetória; processo em análise não conta."""
        turma = "2098.1"
        data_ref = date(2099, 5, 1)

        a_com_prorr = Aluno.objects.create_user(email="com_prorr@teste.invalid", nome="Com Prorr")
        traj_com_prorr = TrajetoriaAcademica.objects.create(
            aluno=a_com_prorr,
            nivel_curso=Aluno.NivelCurso.MESTRADO,
            status=TrajetoriaAcademica.Status.CONCLUIDA,
            ingresso=turma,
            data_ingresso=date(2098, 3, 1),
            isQualificado=True,
            numero_defesa="DEF-9903",
            data_defesa=date(2099, 4, 1),
        )
        ProrrogacaoTrajetoria.objects.create(
            trajetoria=traj_com_prorr,
            meses=3,
            registrado_por=self.servidor,
        )

        a_em_analise = Aluno.objects.create_user(email="em_analise@teste.invalid", nome="Em Analise")
        TrajetoriaAcademica.objects.create(
            aluno=a_em_analise,
            nivel_curso=Aluno.NivelCurso.MESTRADO,
            status=TrajetoriaAcademica.Status.ATIVA,
            ingresso=turma,
            data_ingresso=date(2098, 3, 1),
        )
        Processo.objects.create(
            usuario_criado_por=self.servidor,
            aluno_interessado=a_em_analise,
            tipo=Processo.TipoProcesso.PRORROGACAO_PRAZO,
            status=Processo.StatusProcesso.EM_ANALISE,
            assunto="Prorrogação Pendente",
            descricao="Aguardando parecer",
            setor_atual=self.setor,
        )

        dados = calcular_painel_sucesso_turma(Aluno.NivelCurso.MESTRADO, data_referencia=data_ref)
        turma_dados = next((t for t in dados if t["turma"] == turma), None)
        self.assertIsNotNone(turma_dados)
        self.assertEqual(turma_dados["ingressantes"], 2)
        self.assertEqual(turma_dados["concluidos"], 1)
        self.assertEqual(turma_dados["com_prorrogacao"], 1)
        self.assertEqual(turma_dados["percentual_prorrogacao"], 50.0)

    def test_distribuicao_completa_e_percentuais(self):
        """
        Turma com 40 ingressantes: 18 concluídos, 7 desligados, 1 trancado,
        14 ativos (10 regulares e 4 irregulares).
        Percentuais: 45%, 17.5%, 2.5%, 25% e 10%.
        """
        turma = "2097.1"
        data_ref = date(2101, 6, 1)

        for i in range(18):
            aluno = Aluno.objects.create_user(email=f"conc_{i}@teste.invalid", nome=f"C{i}")
            TrajetoriaAcademica.objects.create(
                aluno=aluno,
                nivel_curso=Aluno.NivelCurso.MESTRADO,
                status=TrajetoriaAcademica.Status.CONCLUIDA,
                ingresso=turma,
                data_ingresso=date(2097, 3, 1),
                isQualificado=True,
                numero_defesa=f"DEF-{i}",
                data_defesa=date(2098, 3, 1),
            )

        for i in range(7):
            aluno = Aluno.objects.create_user(email=f"desl_{i}@teste.invalid", nome=f"D{i}")
            TrajetoriaAcademica.objects.create(
                aluno=aluno,
                nivel_curso=Aluno.NivelCurso.MESTRADO,
                status=TrajetoriaAcademica.Status.DESLIGADA,
                ingresso=turma,
                data_ingresso=date(2097, 3, 1),
            )

        aluno_tranc = Aluno.objects.create_user(email="tranc@teste.invalid", nome="T1")
        TrajetoriaAcademica.objects.create(
            aluno=aluno_tranc,
            nivel_curso=Aluno.NivelCurso.MESTRADO,
            status=TrajetoriaAcademica.Status.TRANCADA,
            ingresso=turma,
            data_ingresso=date(2097, 3, 1),
        )

        for i in range(10):
            aluno = Aluno.objects.create_user(email=f"reg_{i}@teste.invalid", nome=f"R{i}")
            TrajetoriaAcademica.objects.create(
                aluno=aluno,
                nivel_curso=Aluno.NivelCurso.MESTRADO,
                status=TrajetoriaAcademica.Status.ATIVA,
                ingresso=turma,
                data_ingresso=date(2101, 1, 1),
            )

        for i in range(4):
            aluno = Aluno.objects.create_user(email=f"irreg_{i}@teste.invalid", nome=f"IR{i}")
            TrajetoriaAcademica.objects.create(
                aluno=aluno,
                nivel_curso=Aluno.NivelCurso.MESTRADO,
                status=TrajetoriaAcademica.Status.ATIVA,
                ingresso=turma,
                data_ingresso=date(2097, 1, 1),
            )

        dados = calcular_painel_sucesso_turma(Aluno.NivelCurso.MESTRADO, data_referencia=data_ref)
        turma_dados = next((t for t in dados if t["turma"] == turma), None)
        self.assertIsNotNone(turma_dados)

        self.assertEqual(turma_dados["ingressantes"], 40)
        self.assertEqual(turma_dados["concluidos"], 18)
        self.assertEqual(turma_dados["desligados"], 7)
        self.assertEqual(turma_dados["trancados"], 1)
        self.assertEqual(turma_dados["ativos"], 14)
        self.assertEqual(turma_dados["ativos_regulares"], 10)
        self.assertEqual(turma_dados["ativos_irregulares"], 4)

        self.assertEqual(turma_dados["taxa_conclusao"], 45.0)
        self.assertEqual(turma_dados["taxa_desligamento"], 17.5)
        self.assertEqual(turma_dados["taxa_trancamento"], 2.5)
        self.assertEqual(turma_dados["taxa_ativos_regulares"], 25.0)
        self.assertEqual(turma_dados["taxa_ativos_irregulares"], 10.0)

    def test_filtro_intervalo_anos_e_validacoes(self):
        """Testa validações de ano_inicial e ano_final levantando ValueError com formato ou ordem inválidos."""
        # Formato inválido
        with self.assertRaisesMessage(ValueError, "Ano inicial inválido. Utilize o formato YYYY."):
            calcular_painel_sucesso_turma(Aluno.NivelCurso.MESTRADO, ano_inicial="25")

        with self.assertRaisesMessage(ValueError, "Ano final inválido. Utilize o formato YYYY."):
            calcular_painel_sucesso_turma(Aluno.NivelCurso.MESTRADO, ano_final="ABCD")

        # ano_inicial maior que ano_final
        with self.assertRaisesMessage(ValueError, "O ano inicial não pode ser maior que o ano final."):
            calcular_painel_sucesso_turma(Aluno.NivelCurso.MESTRADO, ano_inicial=2026, ano_final=2025)

    def test_filtro_por_ano_seleciona_turmas_corretas(self):
        """Testa se o filtro por ano seleciona exatamente as turmas do intervalo com base no campo ingresso."""
        data_ref = date(2099, 12, 1)

        turmas_anos = ["2090.1", "2090.2", "2091.1", "2091.2", "2092.1"]
        for idx, t in enumerate(turmas_anos):
            aluno = Aluno.objects.create_user(email=f"aluno_ano_{idx}@teste.invalid", nome=f"Aluno {idx}")
            TrajetoriaAcademica.objects.create(
                aluno=aluno,
                nivel_curso=Aluno.NivelCurso.DOUTORADO,
                status=TrajetoriaAcademica.Status.ATIVA,
                ingresso=t,
                data_ingresso=date(int(t[:4]), 3 if t.endswith(".1") else 8, 1),
            )

        res = calcular_painel_sucesso_turma(
            Aluno.NivelCurso.DOUTORADO,
            data_referencia=data_ref,
            ano_inicial=2090,
            ano_final=2091,
        )
        turmas_retornadas = [item["turma"] for item in res if item["turma"].startswith("209")]
        self.assertEqual(turmas_retornadas, ["2091.2", "2091.1", "2090.2", "2090.1"])

        res_2092 = calcular_painel_sucesso_turma(
            Aluno.NivelCurso.DOUTORADO,
            data_referencia=data_ref,
            ano_inicial=2092,
            ano_final=2092,
        )
        turmas_2092 = [item["turma"] for item in res_2092 if item["turma"].startswith("209")]
        self.assertEqual(turmas_2092, ["2092.1"])

        # Só um dos dois anos
        so_inicial = calcular_painel_sucesso_turma(
            Aluno.NivelCurso.DOUTORADO, data_referencia=data_ref, ano_inicial=2092
        )
        self.assertEqual([item["turma"] for item in so_inicial], ["2092.1"])

        so_final = calcular_painel_sucesso_turma(
            Aluno.NivelCurso.DOUTORADO, data_referencia=data_ref, ano_final=2090
        )
        turmas_so_final = [item["turma"] for item in so_final if item["turma"].startswith("209")]
        self.assertEqual(turmas_so_final, ["2090.2", "2090.1"])
