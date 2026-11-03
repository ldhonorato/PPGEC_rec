"""Cálculo da situação de todos os alunos de uma vez.

Confere que o número de consultas ao banco não cresce com a quantidade de
alunos e que o resultado traz o marco e a defesa de cada trajetória.
"""

from datetime import date

from django.test import TestCase

from acompanhamento.models import MarcoCalendario
from acompanhamento.situacao import REGULAR, calcular_situacoes
from processos.models import (
    Aluno,
    ProrrogacaoTrajetoria,
    TrajetoriaAcademica,
    TrancamentoTrajetoria,
)

REFERENCIA = date(2026, 6, 1)


class CalculoEmLoteTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.secretaria = Aluno.objects.create(
            nome="Registro", email="registro@ficticio.invalid", password="!"
        )
        MarcoCalendario.objects.create(
            semestre="2026.1",
            tipo=MarcoCalendario.TipoMarco.PROJETO_DISSERTACAO,
            data_limite=date(2026, 6, 20),
        )
        cls.ids = []
        for i in range(50):
            aluno = Aluno.objects.create(
                nome=f"Aluno {i}", email=f"aluno{i}@ficticio.invalid", password="!"
            )
            trajetoria = TrajetoriaAcademica.objects.create(
                aluno=aluno,
                nivel_curso=Aluno.NivelCurso.MESTRADO,
                ingresso="2025.1",
                data_ingresso=date(2025, 3, 1),
                prazo_qualificacao="2026.1",
            )
            cls.ids.append(trajetoria.pk)
            ProrrogacaoTrajetoria.objects.create(
                trajetoria=trajetoria, meses=1, registrado_por=cls.secretaria
            )
            TrancamentoTrajetoria.objects.create(
                trajetoria=trajetoria,
                data_inicio=date(2025, 8, 1),
                data_fim=date(2025, 8, 10),
                registrado_por=cls.secretaria,
            )

    def consultas_para(self, quantidade):
        trajetorias = TrajetoriaAcademica.objects.filter(pk__in=self.ids[:quantidade])
        with self.assertNumQueries(5):
            calcular_situacoes(trajetorias, REFERENCIA)

    def test_mesmo_numero_de_consultas_para_1_aluno(self):
        self.consultas_para(1)

    def test_mesmo_numero_de_consultas_para_50_alunos(self):
        self.consultas_para(50)

    def test_resultado_traz_marco_e_defesa_de_cada_aluno(self):
        trajetorias = TrajetoriaAcademica.objects.filter(pk__in=self.ids)
        situacoes = calcular_situacoes(trajetorias, REFERENCIA)
        self.assertEqual(len(situacoes), 50)
        situacao = next(iter(situacoes.values()))
        self.assertEqual(situacao["marco"].situacao, REGULAR)
        # 20/06/2026 do calendário + 10 dias de trancamento
        self.assertEqual(situacao["marco"].data_limite, date(2026, 6, 30))
        # 31/03/2027 + 1 mês de prorrogação + 10 dias de trancamento
        self.assertEqual(situacao["defesa"].situacao, REGULAR)
        self.assertEqual(situacao["defesa"].data_limite, date(2027, 5, 10))