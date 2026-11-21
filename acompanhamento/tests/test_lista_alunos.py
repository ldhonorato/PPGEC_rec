"""Lista de alunos pela situação no marco: filtros, falha isolada e desempenho."""

import time
from datetime import date
from unittest.mock import patch

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from acompanhamento import situacao as nucleo
from acompanhamento.lista_alunos import (
    DEFESA,
    IRREGULARES,
    MARCO_INTERMEDIARIO,
    QUALQUER_MARCO,
    cartao_alunos_irregulares,
    listar_alunos_por_marco,
)
from acompanhamento.models import MarcoCalendario
from acompanhamento.situacao import (
    CUMPRIDO,
    IRREGULAR_COM_CHANCE,
    IRREGULAR_SEM_CHANCE,
    NAO_CALCULADO,
    REGULAR,
    SEM_PRAZO,
)
from processos.models import Aluno, TrajetoriaAcademica

REFERENCIA = date(2026, 11, 1)
MESTRADO = Aluno.NivelCurso.MESTRADO
DOUTORADO = Aluno.NivelCurso.DOUTORADO


def criar_aluno(nome, nivel=MESTRADO, **trajetoria):
    aluno = Aluno.objects.create(
        nome=nome,
        email=f"{nome.lower().replace(' ', '.')}@ficticio.invalid",
        password="!",
    )
    dados = {
        "nivel_curso": nivel,
        "ingresso": "2025.1",
        "data_ingresso": date(2025, 3, 1),
        "prazo_qualificacao": "2026.2",
    }
    dados.update(trajetoria)
    TrajetoriaAcademica.objects.create(aluno=aluno, **dados)
    return aluno


def ficticios():
    """Só os alunos criados pelo teste: as migrations do sistema já criam outros."""
    return Aluno.objects.filter(email__endswith="@ficticio.invalid")


def nomes(linhas):
    return [linha.aluno.nome for linha in linhas]


class ListaAlunosPorMarcoTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        MarcoCalendario.objects.create(
            semestre="2026.2",
            tipo=MarcoCalendario.TipoMarco.PROJETO_DISSERTACAO,
            data_limite=date(2026, 9, 20),
        )
        # Data individual depois da referência: ainda no prazo.
        criar_aluno("Ana Regular", data_limite_qualificacao=date(2026, 12, 15))
        # Calendário em 20/09, sem apresentação: dentro dos 3 meses.
        criar_aluno("Bruno Com Chance")
        criar_aluno("Carla Cumprida", isQualificado=True)
        # Sem data individual e sem calendário para 2027.1.
        criar_aluno("Diego Sem Prazo", prazo_qualificacao="2027.1")
        # Data individual em 10/07: a reapresentação acabou em 10/10.
        criar_aluno("Elisa Sem Chance", data_limite_qualificacao=date(2026, 7, 10))
        criar_aluno("Fabio Trancado", status=TrajetoriaAcademica.Status.TRANCADA)
        criar_aluno("Gabi Especial", nivel=Aluno.NivelCurso.ALUNO_ESPECIAL)
        criar_aluno(
            "Hugo Defendeu",
            status=TrajetoriaAcademica.Status.CONCLUIDA,
            data_defesa=date(2026, 8, 1),
            numero_defesa="001",
            isQualificado=True,
        )
        criar_aluno("Igor Doutorado", nivel=DOUTORADO, prazo_qualificacao="2027.1")

    def listar(self, alunos=None, **filtros):
        filtros.setdefault("marco", MARCO_INTERMEDIARIO)
        return listar_alunos_por_marco(
            alunos if alunos is not None else ficticios(),
            data_referencia=REFERENCIA,
            **filtros,
        )

    def test_so_trajetorias_ativas_de_mestrado_e_doutorado(self):
        self.assertEqual(
            nomes(self.listar()),
            [
                "Ana Regular",
                "Bruno Com Chance",
                "Carla Cumprida",
                "Diego Sem Prazo",
                "Elisa Sem Chance",
                "Igor Doutorado",
            ],
        )

    def test_situacao_de_cada_aluno(self):
        situacoes = {linha.aluno.nome: linha.resultado.situacao for linha in self.listar()}
        self.assertEqual(situacoes["Ana Regular"], REGULAR)
        self.assertEqual(situacoes["Bruno Com Chance"], IRREGULAR_COM_CHANCE)
        self.assertEqual(situacoes["Carla Cumprida"], CUMPRIDO)
        self.assertEqual(situacoes["Diego Sem Prazo"], SEM_PRAZO)
        self.assertEqual(situacoes["Elisa Sem Chance"], IRREGULAR_SEM_CHANCE)
        self.assertEqual(situacoes["Igor Doutorado"], SEM_PRAZO)

    def test_as_duas_situacoes_irregulares_sao_exibidas_com_o_mesmo_rotulo(self):
        displays = {linha.aluno.nome: linha.situacao_display for linha in self.listar()}
        self.assertEqual(displays["Bruno Com Chance"], "Irregular")
        self.assertEqual(displays["Elisa Sem Chance"], "Irregular")

    def test_atalho_irregulares_traz_as_duas_situacoes_irregulares(self):
        self.assertEqual(
            nomes(self.listar(situacao=IRREGULARES)),
            ["Bruno Com Chance", "Elisa Sem Chance"],
        )

    def test_qualquer_marco_reune_irregulares_sem_duplicar_alunos(self):
        criar_aluno(
            "Julia Apenas Defesa",
            data_ingresso=date(2020, 1, 1),
            isQualificado=True,
        )
        criar_aluno(
            "Marina Ambos os Marcos",
            data_ingresso=date(2020, 1, 1),
            data_limite_qualificacao=date(2026, 7, 10),
            prazo_qualificacao="2026.2",
        )

        linhas = self.listar(marco=QUALQUER_MARCO, situacao=IRREGULARES)
        por_nome = {linha.aluno.nome: linha for linha in linhas}

        self.assertEqual(
            set(por_nome),
            {
                "Bruno Com Chance",
                "Elisa Sem Chance",
                "Julia Apenas Defesa",
                "Marina Ambos os Marcos",
            },
        )
        self.assertEqual(
            por_nome["Bruno Com Chance"].marco_display,
            "Projeto de dissertação ou qualificação",
        )
        self.assertEqual(por_nome["Julia Apenas Defesa"].marco_display, "Defesa")
        self.assertEqual(
            por_nome["Marina Ambos os Marcos"].marco_display,
            "Projeto de dissertação ou qualificação e Defesa",
        )

    def test_cartao_usa_o_total_da_lista_e_aponta_para_o_filtro(self):
        cartao = cartao_alunos_irregulares(data_referencia=REFERENCIA)
        linhas = listar_alunos_por_marco(
            Aluno.objects.all(),
            QUALQUER_MARCO,
            situacao=IRREGULARES,
            data_referencia=REFERENCIA,
        )

        self.assertEqual(cartao["total"], len(linhas))
        self.assertEqual(
            cartao["endereco"],
            "/coordenacao/alunos/?marco=qualquer&situacao_marco=irregulares",
        )

    def test_cartao_registra_erro_e_retorna_none(self):
        with patch(
            "acompanhamento.lista_alunos.listar_alunos_por_marco",
            side_effect=RuntimeError("falha no cálculo"),
        ):
            with self.assertLogs("acompanhamento.lista_alunos", level="ERROR"):
                self.assertIsNone(cartao_alunos_irregulares(data_referencia=REFERENCIA))

    def test_filtro_por_uma_situacao(self):
        self.assertEqual(
            nomes(self.listar(situacao=REGULAR)),
            ["Ana Regular", "Carla Cumprida"],
        )

    def test_coluna_situacao_mostra_os_tres_estados(self):
        situacoes = {linha.aluno.nome: linha.situacao_display for linha in self.listar()}

        self.assertEqual(situacoes["Ana Regular"], "Regular")
        self.assertEqual(situacoes["Carla Cumprida"], "Regular")
        self.assertEqual(situacoes["Bruno Com Chance"], "Irregular")
        self.assertEqual(situacoes["Diego Sem Prazo"], "Não foi possível calcular")

    def test_filtro_nao_calculado_reune_sem_prazo_e_falha_de_calculo(self):
        original = nucleo.calcular_situacao_trajetoria

        def falha_para_o_bruno(trajetoria, *args, **kwargs):
            if trajetoria.aluno.nome == "Bruno Com Chance":
                raise RuntimeError("cadastro inesperado")
            return original(trajetoria, *args, **kwargs)

        with patch.object(nucleo, "calcular_situacao_trajetoria", falha_para_o_bruno):
            with self.assertLogs("acompanhamento.situacao", level="ERROR"):
                linhas = self.listar(situacao=NAO_CALCULADO)

        self.assertEqual(
            nomes(linhas),
            ["Bruno Com Chance", "Diego Sem Prazo", "Igor Doutorado"],
        )

    def test_filtro_pelo_semestre_do_marco(self):
        self.assertEqual(
            nomes(self.listar(semestre_marco="2026.2")),
            ["Ana Regular", "Bruno Com Chance", "Carla Cumprida", "Elisa Sem Chance"],
        )
        self.assertEqual(nomes(self.listar(semestre_marco="2027.1")), [])

    def test_respeita_os_filtros_que_a_tela_ja_aplicou(self):
        alunos = Aluno.objects.filter(nome__icontains="bruno")
        self.assertEqual(nomes(self.listar(alunos=alunos)), ["Bruno Com Chance"])

    def test_defesa_usa_o_limite_efetivo(self):
        linhas = self.listar(marco=DEFESA)
        self.assertNotIn("Hugo Defendeu", nomes(linhas))
        bruno = next(l for l in linhas if l.aluno.nome == "Bruno Com Chance")
        self.assertEqual(bruno.resultado.situacao, REGULAR)
        self.assertEqual(bruno.resultado.data_limite, date(2027, 3, 31))
        self.assertEqual(bruno.semestre_marco, "2027.1")

    def test_filtros_invalidos_sao_recusados(self):
        for filtros, mensagem in (
            ({"marco": "outro"}, "Marco inválido."),
            ({"situacao": "atrasado"}, "Situação inválida."),
            ({"situacao": IRREGULAR_COM_CHANCE}, "Situação inválida."),
            ({"situacao": IRREGULAR_SEM_CHANCE}, "Situação inválida."),
            ({"situacao": CUMPRIDO}, "Situação inválida."),
            ({"situacao": SEM_PRAZO}, "Situação inválida."),
            (
                {"semestre_marco": "2026-2"},
                "Semestre do marco inválido. Use o formato AAAA.1 ou AAAA.2.",
            ),
        ):
            with self.subTest(filtros=filtros), self.assertRaises(ValueError) as erro:
                self.listar(**filtros)
            self.assertEqual(str(erro.exception), mensagem)

    def test_qualquer_marco_exige_irregular_e_semestre_vazio(self):
        for filtros, mensagem in (
            (
                {"marco": QUALQUER_MARCO},
                '“Qualquer marco” só aceita a situação “Irregular”.',
            ),
            (
                {"marco": QUALQUER_MARCO, "situacao": REGULAR},
                '“Qualquer marco” só aceita a situação “Irregular”.',
            ),
            (
                {
                    "marco": QUALQUER_MARCO,
                    "situacao": IRREGULARES,
                    "semestre_marco": "2026.2",
                },
                'Não é possível filtrar “Qualquer marco” por semestre.',
            ),
        ):
            with self.subTest(filtros=filtros), self.assertRaises(ValueError) as erro:
                self.listar(**filtros)
            self.assertEqual(str(erro.exception), mensagem)

    def test_falha_em_um_aluno_nao_derruba_a_lista(self):
        original = nucleo.calcular_situacao_trajetoria

        def falha_para_o_bruno(trajetoria, *args, **kwargs):
            if trajetoria.aluno.nome == "Bruno Com Chance":
                raise RuntimeError("cadastro inesperado")
            return original(trajetoria, *args, **kwargs)

        with patch.object(nucleo, "calcular_situacao_trajetoria", falha_para_o_bruno):
            with self.assertLogs("acompanhamento.situacao", level="ERROR") as log:
                linhas = self.listar()

        situacoes = {linha.aluno.nome: linha.resultado.situacao for linha in linhas}
        self.assertEqual(situacoes["Bruno Com Chance"], NAO_CALCULADO)
        self.assertEqual(situacoes["Ana Regular"], REGULAR)
        self.assertEqual(len(linhas), 6)
        self.assertIn("Falha ao calcular a situação", log.output[0])

    def test_numero_de_consultas_nao_cresce_com_os_alunos(self):
        def consultas(alunos):
            with CaptureQueriesContext(connection) as contexto:
                self.listar(alunos=alunos)
            return len(contexto.captured_queries)

        um = consultas(Aluno.objects.filter(nome="Ana Regular"))
        todos = consultas(ficticios())
        self.assertEqual(um, todos)


class DesempenhoListaAlunosTests(TestCase):
    QUANTIDADE = 500

    @classmethod
    def setUpTestData(cls):
        MarcoCalendario.objects.create(
            semestre="2026.2",
            tipo=MarcoCalendario.TipoMarco.PROJETO_DISSERTACAO,
            data_limite=date(2026, 9, 20),
        )
        for indice in range(cls.QUANTIDADE):
            criar_aluno(f"Aluno {indice:03d}")

    def test_500_alunos_em_menos_de_2_segundos(self):
        inicio = time.perf_counter()
        linhas = listar_alunos_por_marco(
            ficticios(), MARCO_INTERMEDIARIO, data_referencia=REFERENCIA
        )
        duracao = time.perf_counter() - inicio
        self.assertEqual(len(linhas), self.QUANTIDADE)
        self.assertLess(duracao, 2)
