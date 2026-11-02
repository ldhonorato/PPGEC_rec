"""Confere a situação de cada aluno gerado por gerar_dados_ficticios.

Os alunos são identificados pela ordem de criação (pk da trajetória), que
segue a ordem do comando. Se o comando mudar essa ordem, a tabela ESPERADO
precisa mudar junto.
"""

from datetime import date

from django.core.management import call_command
from django.test import TestCase, override_settings

from acompanhamento.situacao import (
    CUMPRIDO,
    FORA_DO_CALCULO,
    IRREGULAR_COM_CHANCE,
    IRREGULAR_SEM_CHANCE,
    MESTRADO,
    REGULAR,
    SEM_PRAZO,
    calcular_situacao_defesa,
    calcular_situacoes,
)
from processos.models import TrajetoriaAcademica

REFERENCIA = date(2026, 10, 12)

# (número, nível, status, situação no marco, situação na defesa, cenário)
ESPERADO = [
    (1, "MESTRADO", "ATIVA", REGULAR, REGULAR, "mestrado em dia"),
    (2, "MESTRADO", "ATIVA", IRREGULAR_COM_CHANCE, REGULAR,
     "projeto atrasado, com chance"),
    (3, "MESTRADO", "ATIVA", IRREGULAR_SEM_CHANCE, REGULAR,
     "projeto atrasado, sem chance"),
    (4, "MESTRADO", "ATIVA", SEM_PRAZO, SEM_PRAZO, "sem datas cadastradas"),
    (5, "MESTRADO", "ATIVA", IRREGULAR_SEM_CHANCE, IRREGULAR_SEM_CHANCE,
     "projeto e defesa vencidos"),
    (6, "MESTRADO", "CONCLUIDA", CUMPRIDO, FORA_DO_CALCULO, "já defendeu"),
    (7, "MESTRADO", "ATIVA", CUMPRIDO, REGULAR,
     "projeto aprovado antes do limite"),
    (8, "MESTRADO", "ATIVA", IRREGULAR_SEM_CHANCE, REGULAR,
     "apresentou depois do limite com conceito C"),
    # Os 30 dias de trancamento também empurram a data limite do projeto,
    # que passou 30 dias antes da referência e agora cai nela.
    (9, "MESTRADO", "ATIVA", REGULAR, REGULAR,
     "defesa salva por prorrogação e trancamento"),
    # O cálculo não filtra por status: trancado e desligado saem calculados.
    # Quem os tira da lista é a tela de alunos, não o cálculo.
    (10, "MESTRADO", "TRANCADA", IRREGULAR_SEM_CHANCE, REGULAR,
     "trancado, atrasado no projeto"),
    (11, "MESTRADO", "DESLIGADA", IRREGULAR_SEM_CHANCE, REGULAR,
     "desligado, atrasado no projeto"),
    (12, "DOUTORADO", "ATIVA", CUMPRIDO, REGULAR,
     "doutorado qualificado, em dia"),
    (13, "DOUTORADO", "ATIVA", CUMPRIDO, IRREGULAR_SEM_CHANCE,
     "doutorado qualificado, defesa vencida"),
    (14, "DOUTORADO", "ATIVA", IRREGULAR_COM_CHANCE, REGULAR,
     "qualificação atrasada, com chance"),
    # Turmas antigas, para o painel de sucesso por turma. Sem data limite do
    # projeto cadastrada, o marco sai "sem prazo".
    (15, "MESTRADO", "ATIVA", SEM_PRAZO, REGULAR, "turma de 6 meses atrás"),
    (16, "MESTRADO", "ATIVA", SEM_PRAZO, REGULAR, "ativo com prorrogação"),
    (17, "MESTRADO", "CONCLUIDA", CUMPRIDO, FORA_DO_CALCULO,
     "concluído com prorrogação"),
    (18, "MESTRADO", "CONCLUIDA", CUMPRIDO, FORA_DO_CALCULO, "concluído"),
    (19, "MESTRADO", "DESLIGADA", SEM_PRAZO, IRREGULAR_SEM_CHANCE, "desligado"),
    (20, "MESTRADO", "CONCLUIDA", CUMPRIDO, FORA_DO_CALCULO,
     "concluído com 6 meses de prorrogação"),
    (21, "MESTRADO", "TRANCADA", SEM_PRAZO, IRREGULAR_SEM_CHANCE, "trancado"),
    (22, "MESTRADO", "CONCLUIDA", CUMPRIDO, FORA_DO_CALCULO, "concluído"),
    (23, "MESTRADO", "DESLIGADA", SEM_PRAZO, IRREGULAR_SEM_CHANCE, "desligado"),
    (24, "DOUTORADO", "ATIVA", SEM_PRAZO, REGULAR, "turma de 6 meses atrás"),
    (25, "DOUTORADO", "DESLIGADA", SEM_PRAZO, REGULAR, "desligado"),
    (26, "DOUTORADO", "TRANCADA", SEM_PRAZO, REGULAR, "trancado"),
    (27, "DOUTORADO", "CONCLUIDA", CUMPRIDO, FORA_DO_CALCULO,
     "concluído com prorrogação"),
    (28, "DOUTORADO", "ATIVA", SEM_PRAZO, REGULAR, "ativo com prorrogação"),
    (29, "DOUTORADO", "CONCLUIDA", CUMPRIDO, FORA_DO_CALCULO, "concluído"),
    (30, "DOUTORADO", "CONCLUIDA", CUMPRIDO, FORA_DO_CALCULO, "concluído"),
    (31, "DOUTORADO", "DESLIGADA", SEM_PRAZO, IRREGULAR_SEM_CHANCE, "desligado"),
    (32, "DOUTORADO", "CONCLUIDA", CUMPRIDO, FORA_DO_CALCULO,
     "concluído com 12 meses de prorrogação"),
]


@override_settings(DEBUG=True)
class AlunosFicticiosSituacaoTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command(
            "gerar_dados_ficticios",
            data_referencia=REFERENCIA,
            verbosity=0,
        )
        cls.trajetorias = list(
            TrajetoriaAcademica.objects.filter(
                aluno__email__endswith="@ficticio.invalid"
            ).order_by("pk")
        )
        cls.resultados = calcular_situacoes(cls.trajetorias, REFERENCIA)

    def test_gera_todas_as_trajetorias(self):
        self.assertEqual(len(self.trajetorias), len(ESPERADO))

    def test_cada_aluno_cai_na_situacao_esperada(self):
        for numero, nivel, status, marco, defesa, cenario in ESPERADO:
            with self.subTest(aluno=numero, cenario=cenario):
                trajetoria = self.trajetorias[numero - 1]
                resultado = self.resultados[trajetoria.pk]
                self.assertEqual(trajetoria.nivel_curso, nivel)
                self.assertEqual(trajetoria.status, status)
                self.assertEqual(resultado["marco"].situacao, marco)
                self.assertEqual(resultado["defesa"].situacao, defesa)

    def test_as_cinco_situacoes_aparecem_no_marco(self):
        no_marco = {r["marco"].situacao for r in self.resultados.values()}
        cinco = {
            REGULAR,
            IRREGULAR_COM_CHANCE,
            IRREGULAR_SEM_CHANCE,
            CUMPRIDO,
            SEM_PRAZO,
        }
        self.assertTrue(cinco <= no_marco, f"faltam: {cinco - no_marco}")

    def test_todo_resultado_traz_motivo_em_portugues(self):
        for pk, resultado in self.resultados.items():
            for parte in ("marco", "defesa"):
                with self.subTest(trajetoria=pk, parte=parte):
                    self.assertTrue(resultado[parte].motivo.strip())

    def test_prorrogacao_e_trancamento_estendem_o_prazo_da_defesa(self):
        trajetoria = self.trajetorias[8]
        defesa = self.resultados[trajetoria.pk]["defesa"]

        # prazo regimental 30/09/2026 + 3 meses (até o fim de dezembro,
        # 31/12/2026) + 30 dias de trancamento
        self.assertEqual(defesa.data_limite, date(2027, 1, 30))

        # sem as extensões, o mesmo aluno estaria atrasado
        sem_extensoes = calcular_situacao_defesa(
            nivel=MESTRADO,
            mes_ingresso=trajetoria.data_ingresso,
            prorrogacao_meses=0,
            data_referencia=REFERENCIA,
        )
        self.assertEqual(sem_extensoes.situacao, IRREGULAR_SEM_CHANCE)

    def test_turmas_em_pelo_menos_cinco_anos_por_nivel(self):
        for nivel in ("MESTRADO", "DOUTORADO"):
            with self.subTest(nivel=nivel):
                anos = {t.ingresso[:4] for t in self.trajetorias if t.nivel_curso == nivel}
                self.assertGreaterEqual(len(anos), 5)

    def test_cada_nivel_tem_concluido_desligado_trancado_e_prorrogacao(self):
        for nivel in ("MESTRADO", "DOUTORADO"):
            with self.subTest(nivel=nivel):
                do_nivel = [t for t in self.trajetorias if t.nivel_curso == nivel]
                status = {t.status for t in do_nivel}
                self.assertTrue(
                    {"CONCLUIDA", "DESLIGADA", "TRANCADA", "ATIVA"} <= status
                )
                self.assertTrue(any(t.meses_prorrogados > 0 for t in do_nivel))

    def test_concluidos_em_varias_turmas_por_nivel(self):
        for nivel in ("MESTRADO", "DOUTORADO"):
            with self.subTest(nivel=nivel):
                turmas = {
                    t.ingresso
                    for t in self.trajetorias
                    if t.nivel_curso == nivel and t.status == "CONCLUIDA"
                }
                self.assertGreaterEqual(len(turmas), 3)
