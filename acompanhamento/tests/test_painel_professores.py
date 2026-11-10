"""Cálculos do painel por professor, sempre com data de referência fixa."""

from datetime import date
from itertools import count

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from acompanhamento.servicos import (
    calcular_painel_professores,
    calcular_painel_sucesso_turma,
)
from processos.models import (
    Aluno,
    Docente,
    ProrrogacaoTrajetoria,
    TrajetoriaAcademica,
    User,
)

REFERENCIA = date(2026, 11, 5)
MESTRADO = Aluno.NivelCurso.MESTRADO
DOUTORADO = Aluno.NivelCurso.DOUTORADO
Status = TrajetoriaAcademica.Status

# Mestrado que ingressou em 2022.1: o prazo regimental (24 meses) vai até 31/03/2024.
TURMA_ANTIGA = ("2022.1", date(2022, 3, 1))
# Turma recente: só chega ao prazo regular em 2028.
TURMA_RECENTE = ("2026.2", date(2026, 8, 1))
DEFESA_NO_PRAZO = date(2023, 12, 15)
DEFESA_FORA_DO_PRAZO = date(2024, 6, 10)

_numero = count(1)


def criar_docente(nome):
    return Docente.objects.create(
        nome=nome, email=f"docente.{next(_numero)}@teste.invalid", password="!"
    )


def criar_trajetoria(
    orientador,
    turma=TURMA_ANTIGA,
    status=Status.ATIVA,
    nivel=MESTRADO,
    data_defesa=None,
    prorrogacao_por=None,
):
    ingresso, data_ingresso = turma
    numero = next(_numero)
    aluno = Aluno.objects.create(
        nome=f"Aluno {numero}", email=f"aluno.{numero}@teste.invalid", password="!"
    )
    dados = {}
    if status == Status.CONCLUIDA:
        dados = {
            "isQualificado": True,
            "numero_defesa": f"DEF-{numero}",
            "data_defesa": data_defesa,
        }
    trajetoria = TrajetoriaAcademica.objects.create(
        aluno=aluno,
        orientador=orientador,
        nivel_curso=nivel,
        status=status,
        ingresso=ingresso,
        data_ingresso=data_ingresso,
        **dados,
    )
    if prorrogacao_por is not None:
        ProrrogacaoTrajetoria.objects.create(
            trajetoria=trajetoria, meses=3, registrado_por=prorrogacao_por
        )
    return trajetoria


def linha_de(resultado, nome):
    return next(linha for linha in resultado["linhas"] if linha["nome"] == nome)


class PainelProfessoresTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        # As migrations criam alunos de exemplo; eles entrariam nas contagens.
        TrajetoriaAcademica.objects.all().delete()
        cls.servidor = User.objects.create_user(
            email="servidor.painel.professores@teste.invalid",
            nome="Servidor Teste",
            tipo_usuario=User.TipoUsuario.SERVIDOR,
        )
        # Bruno é criado antes da Ana: a ordem da tela não pode ser a de cadastro.
        cls.bruno = criar_docente("Bruno Lima")
        cls.ana = criar_docente("Ana Souza")

        # Ana: 4 alunos, 3 com prazo vencido (2 defenderam, 1 no prazo).
        criar_trajetoria(cls.ana, status=Status.CONCLUIDA, data_defesa=DEFESA_NO_PRAZO)
        criar_trajetoria(
            cls.ana,
            status=Status.CONCLUIDA,
            data_defesa=DEFESA_FORA_DO_PRAZO,
            prorrogacao_por=cls.servidor,
        )
        criar_trajetoria(cls.ana, status=Status.DESLIGADA)
        criar_trajetoria(cls.ana, turma=TURMA_RECENTE)
        # Fora do painel: doutorado, removida e em homologação.
        criar_trajetoria(cls.ana, turma=TURMA_RECENTE, nivel=DOUTORADO)
        criar_trajetoria(cls.ana, status=Status.REMOVIDA)
        criar_trajetoria(cls.ana, status=Status.EM_HOMOLOGACAO)

        # Bruno: só turma recente, então ninguém tem prazo vencido.
        criar_trajetoria(cls.bruno, turma=TURMA_RECENTE)
        criar_trajetoria(cls.bruno, turma=TURMA_RECENTE, status=Status.TRANCADA)

        # Sem orientador, com prazo vencido e sem defesa.
        criar_trajetoria(None)

    def calcular(self, **filtros):
        return calcular_painel_professores(MESTRADO, REFERENCIA, **filtros)

    def test_dois_professores_com_numeros_separados(self):
        resultado = self.calcular()

        ana = linha_de(resultado, "Ana Souza")
        self.assertEqual(ana["orientador_id"], self.ana.pk)
        self.assertEqual(
            {chave: ana[chave] for chave in (
                "alunos", "ativos", "concluidos", "desligados", "trancados",
                "com_prorrogacao", "prazo_vencido", "defenderam",
                "defenderam_no_prazo", "concluidos_sem_data",
            )},
            {
                "alunos": 4, "ativos": 1, "concluidos": 2, "desligados": 1,
                "trancados": 0, "com_prorrogacao": 1, "prazo_vencido": 3,
                "defenderam": 2, "defenderam_no_prazo": 1, "concluidos_sem_data": 0,
            },
        )
        self.assertEqual(ana["taxa_prorrogacao"], 25.0)
        self.assertEqual(ana["taxa_sucesso"], 66.7)
        self.assertEqual(ana["taxa_no_prazo"], 33.3)

        bruno = linha_de(resultado, "Bruno Lima")
        self.assertEqual(bruno["orientador_id"], self.bruno.pk)
        self.assertEqual(
            (bruno["alunos"], bruno["ativos"], bruno["trancados"], bruno["concluidos"]),
            (2, 1, 1, 0),
        )
        self.assertEqual(bruno["com_prorrogacao"], 0)
        self.assertEqual(bruno["taxa_prorrogacao"], 0.0)

    def test_linhas_em_ordem_alfabetica_e_sem_orientador_no_fim(self):
        nomes = [linha["nome"] for linha in self.calcular()["linhas"]]

        self.assertEqual(nomes, ["Ana Souza", "Bruno Lima", "Sem orientador"])

    def test_linha_sem_orientador(self):
        linha = linha_de(self.calcular(), "Sem orientador")

        self.assertIsNone(linha["orientador_id"])
        self.assertEqual((linha["alunos"], linha["ativos"], linha["prazo_vencido"]), (1, 1, 1))
        self.assertEqual(linha["defenderam"], 0)
        self.assertEqual(linha["taxa_sucesso"], 0.0)

    def test_sem_aluno_sem_orientador_nao_ha_linha_vazia(self):
        resultado = self.calcular(ano_inicial=2026)

        nomes = [linha["nome"] for linha in resultado["linhas"]]
        self.assertEqual(nomes, ["Ana Souza", "Bruno Lima"])

    def test_total_soma_as_linhas_e_calcula_as_taxas_sobre_o_total(self):
        resultado = self.calcular()
        total = resultado["total"]

        self.assertNotIn("orientador_id", total)
        self.assertNotIn("nome", total)
        for chave in ("alunos", "ativos", "concluidos", "desligados", "trancados",
                      "com_prorrogacao", "prazo_vencido", "defenderam",
                      "defenderam_no_prazo", "concluidos_sem_data"):
            self.assertEqual(total[chave], sum(linha[chave] for linha in resultado["linhas"]))
        self.assertEqual(total["alunos"], 7)
        self.assertEqual(total["prazo_vencido"], 4)
        # 1 de 7 alunos; 2 e 1 defesas entre 4 com prazo vencido.
        self.assertEqual(total["taxa_prorrogacao"], 14.3)
        self.assertEqual(total["taxa_sucesso"], 50.0)
        self.assertEqual(total["taxa_no_prazo"], 25.0)

    def test_soma_das_linhas_e_igual_a_soma_das_turmas(self):
        for filtros in ({}, {"ano_inicial": 2026}, {"ano_final": 2022}, {"ano_inicial": 2022, "ano_final": 2026}):
            with self.subTest(filtros=filtros):
                turmas = calcular_painel_sucesso_turma(MESTRADO, REFERENCIA, **filtros)
                resultado = self.calcular(**filtros)
                pares = (
                    ("alunos", "ingressantes"),
                    ("ativos", "ativos"),
                    ("concluidos", "concluidos"),
                    ("desligados", "desligados"),
                    ("trancados", "trancados"),
                    ("com_prorrogacao", "com_prorrogacao"),
                )
                for chave_professor, chave_turma in pares:
                    self.assertEqual(
                        sum(linha[chave_professor] for linha in resultado["linhas"]),
                        sum(turma[chave_turma] for turma in turmas),
                        chave_professor,
                    )

    def test_defesa_no_prazo_fora_do_prazo_e_concluido_sem_data(self):
        davi = criar_docente("Davi Rocha")
        criar_trajetoria(davi, status=Status.CONCLUIDA, data_defesa=DEFESA_NO_PRAZO)
        criar_trajetoria(davi, status=Status.CONCLUIDA, data_defesa=DEFESA_FORA_DO_PRAZO)
        sem_data = criar_trajetoria(davi, status=Status.CONCLUIDA, data_defesa=DEFESA_NO_PRAZO)
        # O cadastro exige a data ao concluir; um registro antigo pode não ter.
        TrajetoriaAcademica.objects.filter(pk=sem_data.pk).update(data_defesa=None)

        linha = linha_de(self.calcular(), "Davi Rocha")

        self.assertEqual(linha["concluidos"], 3)
        self.assertEqual(linha["prazo_vencido"], 3)
        self.assertEqual(linha["defenderam"], 2)
        self.assertEqual(linha["defenderam_no_prazo"], 1)
        self.assertEqual(linha["concluidos_sem_data"], 1)
        self.assertEqual(linha["taxa_sucesso"], 66.7)
        self.assertEqual(linha["taxa_no_prazo"], 33.3)

    def test_turma_recente_fica_fora_do_prazo_vencido_e_a_taxa_e_none(self):
        bruno = linha_de(self.calcular(), "Bruno Lima")

        self.assertEqual(bruno["alunos"], 2)
        self.assertEqual(bruno["prazo_vencido"], 0)
        self.assertEqual(bruno["defenderam"], 0)
        self.assertIsNone(bruno["taxa_sucesso"])
        self.assertIsNone(bruno["taxa_no_prazo"])

    def test_total_sem_base_tem_taxa_none(self):
        total = self.calcular(ano_inicial=2026)["total"]

        self.assertEqual(total["prazo_vencido"], 0)
        self.assertIsNone(total["taxa_sucesso"])
        self.assertIsNone(total["taxa_no_prazo"])

    def test_aluno_no_ultimo_dia_do_prazo_ainda_nao_esta_vencido(self):
        carla = criar_docente("Carla Dias")
        criar_trajetoria(carla, turma=("2024.1", date(2024, 3, 1)))  # prazo: 31/03/2026

        no_ultimo_dia = calcular_painel_professores(MESTRADO, date(2026, 3, 31))
        no_dia_seguinte = calcular_painel_professores(MESTRADO, date(2026, 4, 1))

        self.assertEqual(linha_de(no_ultimo_dia, "Carla Dias")["prazo_vencido"], 0)
        self.assertEqual(linha_de(no_dia_seguinte, "Carla Dias")["prazo_vencido"], 1)

    def test_trajetoria_sem_data_de_ingresso_nao_tem_prazo_vencido(self):
        eva = criar_docente("Eva Nunes")
        trajetoria = criar_trajetoria(eva)
        TrajetoriaAcademica.objects.filter(pk=trajetoria.pk).update(data_ingresso=None)

        linha = linha_de(self.calcular(), "Eva Nunes")

        self.assertEqual(linha["alunos"], 1)
        self.assertEqual(linha["prazo_vencido"], 0)

    def test_filtro_por_ano_de_ingresso(self):
        recentes = self.calcular(ano_inicial=2026)
        antigos = self.calcular(ano_final=2022)

        self.assertEqual(linha_de(recentes, "Ana Souza")["alunos"], 1)
        self.assertEqual(linha_de(recentes, "Bruno Lima")["alunos"], 2)
        self.assertEqual(recentes["total"]["alunos"], 3)

        self.assertEqual(linha_de(antigos, "Ana Souza")["alunos"], 3)
        self.assertEqual(linha_de(antigos, "Sem orientador")["alunos"], 1)
        self.assertEqual([linha["nome"] for linha in antigos["linhas"]], ["Ana Souza", "Sem orientador"])

    def test_filtro_por_nivel(self):
        resultado = calcular_painel_professores(DOUTORADO, REFERENCIA)

        self.assertEqual([linha["nome"] for linha in resultado["linhas"]], ["Ana Souza"])
        self.assertEqual(resultado["total"]["alunos"], 1)

    def test_dados_limitados_com_turma_anterior_a_2026_2(self):
        self.assertTrue(self.calcular()["dados_limitados"])
        self.assertFalse(self.calcular(ano_inicial=2026)["dados_limitados"])

    def test_entradas_invalidas_levantam_value_error(self):
        casos = (
            {"ano_inicial": "26"},
            {"ano_final": "20x6"},
            {"ano_inicial": 2027, "ano_final": 2026},
        )
        for filtros in casos:
            with self.subTest(filtros=filtros), self.assertRaises(ValueError):
                self.calcular(**filtros)
        with self.assertRaises(ValueError):
            calcular_painel_professores(Aluno.NivelCurso.POSDOUTORADO, REFERENCIA)

    def test_numero_de_consultas_nao_cresce_com_os_alunos(self):
        with CaptureQueriesContext(connection) as poucas:
            self.calcular()

        for _ in range(10):
            criar_trajetoria(
                self.ana, status=Status.CONCLUIDA, data_defesa=DEFESA_NO_PRAZO,
                prorrogacao_por=self.servidor,
            )
        with CaptureQueriesContext(connection) as muitas:
            self.calcular()

        self.assertEqual(len(poucas), len(muitas))
        self.assertLessEqual(len(muitas), 3)


class PainelSucessoTurmaPorOrientadorTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        # As migrations criam alunos de exemplo; eles entrariam nas contagens.
        TrajetoriaAcademica.objects.all().delete()
        cls.ana = criar_docente("Ana Souza")
        cls.bruno = criar_docente("Bruno Lima")
        criar_trajetoria(cls.ana, turma=TURMA_ANTIGA)
        criar_trajetoria(cls.ana, turma=TURMA_ANTIGA, status=Status.DESLIGADA)
        criar_trajetoria(cls.ana, turma=TURMA_RECENTE)
        criar_trajetoria(cls.bruno, turma=TURMA_RECENTE)
        criar_trajetoria(None, turma=TURMA_ANTIGA)

    def ingressantes(self, **filtros):
        turmas = calcular_painel_sucesso_turma(MESTRADO, REFERENCIA, **filtros)
        return {turma["turma"]: turma["ingressantes"] for turma in turmas}

    def test_sem_orientador_id_vale_o_programa_todo(self):
        self.assertEqual(self.ingressantes(), {"2026.2": 2, "2022.1": 3})

    def test_com_orientador_id_so_os_alunos_dele(self):
        self.assertEqual(
            self.ingressantes(orientador_id=self.ana.pk), {"2026.2": 1, "2022.1": 2}
        )
        self.assertEqual(self.ingressantes(orientador_id=self.bruno.pk), {"2026.2": 1})

    def test_orientador_sem_alunos_devolve_lista_vazia(self):
        self.assertEqual(self.ingressantes(orientador_id=0), {})

    def test_orientador_id_junto_com_filtro_de_ano(self):
        self.assertEqual(
            self.ingressantes(orientador_id=self.ana.pk, ano_final=2022), {"2022.1": 2}
        )
