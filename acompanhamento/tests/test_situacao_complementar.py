"""Testes complementares da regra de situação.

Cobrem o que os testes por caso (test_situacao.py) não alcançam: de onde
vem a data limite do projeto e da qualificação, a leitura do cadastro no
cálculo da defesa e a regra do conceito (B aprovado, D sem chance).

As trajetórias aqui são objetos simples, só com os campos que a regra lê,
para o teste não depender do banco. A única exceção é a consulta ao
calendário gravado, que precisa dele.
"""

from datetime import date
from types import SimpleNamespace

from django.test import SimpleTestCase, TestCase

from acompanhamento.models import MarcoCalendario
from acompanhamento.situacao import (
    CUMPRIDO,
    FORA_DO_CALCULO,
    IRREGULAR_COM_CHANCE,
    IRREGULAR_SEM_CHANCE,
    MESTRADO,
    ORIGEM_CALENDARIO,
    ORIGEM_TRAJETORIA,
    REGULAR,
    SEM_PRAZO,
    Apresentacao,
    calcular_situacao_defesa_trajetoria,
    calcular_situacao_marco,
    calcular_situacao_trajetoria,
    resolver_data_limite,
)

PROJETO = MarcoCalendario.TipoMarco.PROJETO_DISSERTACAO
QUALIFICACAO = MarcoCalendario.TipoMarco.QUALIFICACAO


def lista(*itens):
    """Imita a relação do Django: a regra só chama .all()."""
    return SimpleNamespace(all=lambda: list(itens))


def apresentacao(dia, conceito):
    return SimpleNamespace(data_apresentacao=dia, conceito=conceito)


def trancamento(inicio, fim):
    return SimpleNamespace(data_inicio=inicio, data_fim=fim)


def trajetoria(
    nivel,
    data_limite=None,
    prazo="",
    regimental="",
    apresentacoes=(),
    qualificado=False,
    ingresso=None,
    prorrogacoes=(),
    trancamentos=(),
    data_defesa=None,
):
    return SimpleNamespace(
        nivel_curso=nivel,
        data_limite_qualificacao=data_limite,
        prazo_qualificacao=prazo,
        prazo_qualificacao_regimental=regimental,
        apresentacoes_qualificacao=lista(*apresentacoes),
        isQualificado=qualificado,
        data_ingresso=ingresso,
        prorrogacoes=lista(*[SimpleNamespace(meses=m) for m in prorrogacoes]),
        trancamentos=lista(*trancamentos),
        data_defesa=data_defesa,
    )


class DataLimiteDoMestradoTests(SimpleTestCase):
    """Mestrado: a data da trajetória vale; sem ela, a do calendário."""

    CALENDARIO = {("2026.2", PROJETO): date(2026, 9, 30)}

    def test_data_da_trajetoria_vale(self):
        t = trajetoria("MESTRADO", data_limite=date(2026, 9, 20), prazo="2026.2")
        self.assertEqual(
            resolver_data_limite(t, self.CALENDARIO),
            (date(2026, 9, 20), ORIGEM_TRAJETORIA),
        )

    def test_sem_data_propria_usa_o_calendario(self):
        t = trajetoria("MESTRADO", prazo="2026.2")
        self.assertEqual(
            resolver_data_limite(t, self.CALENDARIO),
            (date(2026, 9, 30), ORIGEM_CALENDARIO),
        )

    def test_data_do_calendario_de_outro_tipo_nao_vale(self):
        t = trajetoria("MESTRADO", prazo="2026.2")
        calendario = {("2026.2", QUALIFICACAO): date(2026, 9, 30)}
        self.assertEqual(resolver_data_limite(t, calendario), (None, ""))

    def test_semestre_sem_entrada_no_calendario(self):
        t = trajetoria("MESTRADO", prazo="2026.2")
        calendario = {("2026.1", PROJETO): date(2026, 3, 30)}
        self.assertEqual(resolver_data_limite(t, calendario), (None, ""))

    def test_sem_semestre_e_sem_data_propria(self):
        t = trajetoria("MESTRADO")
        self.assertEqual(resolver_data_limite(t, self.CALENDARIO), (None, ""))


class DataLimiteDoDoutoradoTests(SimpleTestCase):
    """Doutorado: sempre a data do calendário, no semestre da qualificação."""

    CALENDARIO = {
        ("2028.1", QUALIFICACAO): date(2028, 6, 7),
        ("2029.1", QUALIFICACAO): date(2029, 6, 7),
    }

    def test_usa_o_prazo_cadastrado(self):
        t = trajetoria("DOUTORADO", prazo="2028.1", regimental="2029.1")
        self.assertEqual(
            resolver_data_limite(t, self.CALENDARIO),
            (date(2028, 6, 7), ORIGEM_CALENDARIO),
        )

    def test_sem_prazo_cadastrado_usa_o_regimental(self):
        t = trajetoria("DOUTORADO", prazo="", regimental="2029.1")
        self.assertEqual(
            resolver_data_limite(t, self.CALENDARIO),
            (date(2029, 6, 7), ORIGEM_CALENDARIO),
        )

    def test_data_propria_da_trajetoria_nao_vale_no_doutorado(self):
        # Pela regra documentada, só o mestrado lê a data da trajetória.
        t = trajetoria("DOUTORADO", data_limite=date(2028, 6, 7))
        self.assertEqual(resolver_data_limite(t, self.CALENDARIO), (None, ""))

    def test_nivel_sem_prazo_de_marco(self):
        t = trajetoria("ESPECIAL", prazo="2028.1")
        self.assertEqual(resolver_data_limite(t, self.CALENDARIO), (None, ""))


class DataLimiteNoBancoTests(TestCase):
    def test_consulta_o_calendario_gravado_quando_nao_recebe_o_dicionario(self):
        MarcoCalendario.objects.update_or_create(
            semestre="2026.2",
            tipo=PROJETO,
            defaults={"data_limite": date(2026, 9, 20)},
        )
        t = trajetoria("MESTRADO", prazo="2026.2")
        self.assertEqual(
            resolver_data_limite(t), (date(2026, 9, 20), ORIGEM_CALENDARIO)
        )


class SituacaoDoMarcoLidaDoCadastroTests(SimpleTestCase):
    def test_data_da_trajetoria_aparece_no_resultado(self):
        t = trajetoria("MESTRADO", data_limite=date(2026, 9, 20))
        r = calcular_situacao_trajetoria(t, date(2026, 9, 1), {})
        self.assertEqual(r.situacao, REGULAR)
        self.assertEqual(r.data_limite, date(2026, 9, 20))
        self.assertEqual(r.origem_data_limite, ORIGEM_TRAJETORIA)

    def test_data_do_calendario_aparece_no_resultado(self):
        t = trajetoria("MESTRADO", prazo="2026.2")
        calendario = {("2026.2", PROJETO): date(2026, 9, 20)}
        r = calcular_situacao_trajetoria(t, date(2026, 11, 1), calendario)
        self.assertEqual(r.situacao, IRREGULAR_COM_CHANCE)
        self.assertEqual(r.prazo_reapresentacao, date(2026, 12, 20))
        self.assertEqual(r.origem_data_limite, ORIGEM_CALENDARIO)

    def test_sem_nenhuma_data_fica_sem_prazo_e_sem_origem(self):
        r = calcular_situacao_trajetoria(trajetoria("MESTRADO"), date(2026, 11, 1), {})
        self.assertEqual(r.situacao, SEM_PRAZO)
        self.assertEqual(r.origem_data_limite, "")

    def test_apresentacao_aprovada_do_cadastro_cumpre_o_marco(self):
        t = trajetoria(
            "MESTRADO",
            data_limite=date(2026, 9, 20),
            apresentacoes=[apresentacao(date(2026, 9, 18), "A")],
            qualificado=True,
        )
        r = calcular_situacao_trajetoria(t, date(2026, 11, 1), {})
        self.assertEqual(r.situacao, CUMPRIDO)
        self.assertFalse(r.avisos)

    def test_doutorado_tem_6_meses_para_reapresentar(self):
        t = trajetoria("DOUTORADO", prazo="2028.1")
        calendario = {("2028.1", QUALIFICACAO): date(2028, 6, 7)}
        r = calcular_situacao_trajetoria(t, date(2028, 9, 1), calendario)
        self.assertEqual(r.situacao, IRREGULAR_COM_CHANCE)
        self.assertEqual(r.prazo_reapresentacao, date(2028, 12, 7))


class DefesaLidaDoCadastroTests(SimpleTestCase):
    """Mestrado com ingresso em 01/03/2025: prazo regimental até 31/03/2027."""

    INGRESSO = date(2025, 3, 1)

    def test_soma_varias_prorrogacoes_e_varios_trancamentos(self):
        # prorrogações 2 + 1 = 3 meses: 31/03/2027 vira 30/06/2027
        # trancamentos 10 dias (01 a 10/04) + 5 dias (01 a 05/05) = 15 dias
        # limite efetivo: 30/06/2027 + 15 dias = 15/07/2027
        t = trajetoria(
            "MESTRADO",
            ingresso=self.INGRESSO,
            prorrogacoes=[2, 1],
            trancamentos=[
                trancamento(date(2026, 4, 1), date(2026, 4, 10)),
                trancamento(date(2026, 5, 1), date(2026, 5, 5)),
            ],
        )
        no_limite = calcular_situacao_defesa_trajetoria(t, date(2027, 7, 15))
        self.assertEqual(no_limite.situacao, REGULAR)
        self.assertEqual(no_limite.data_limite, date(2027, 7, 15))

        depois = calcular_situacao_defesa_trajetoria(t, date(2027, 7, 16))
        self.assertEqual(depois.situacao, IRREGULAR_SEM_CHANCE)

    def test_trancamento_de_um_dia_conta_um_dia(self):
        t = trajetoria(
            "MESTRADO",
            ingresso=self.INGRESSO,
            trancamentos=[trancamento(date(2026, 4, 1), date(2026, 4, 1))],
        )
        r = calcular_situacao_defesa_trajetoria(t, date(2027, 4, 1))
        self.assertEqual(r.data_limite, date(2027, 4, 1))

    def test_defesa_registrada_sai_do_calculo(self):
        t = trajetoria(
            "MESTRADO", ingresso=self.INGRESSO, data_defesa=date(2027, 1, 10)
        )
        r = calcular_situacao_defesa_trajetoria(t, date(2027, 5, 1))
        self.assertEqual(r.situacao, FORA_DO_CALCULO)

    def test_sem_ingresso_fica_sem_prazo(self):
        t = trajetoria("MESTRADO", ingresso=None)
        r = calcular_situacao_defesa_trajetoria(t, date(2027, 5, 1))
        self.assertEqual(r.situacao, SEM_PRAZO)


class ConceitoTests(SimpleTestCase):
    LIMITE = date(2026, 9, 20)

    def marco(self, apresentacoes, referencia, concluido=False):
        return calcular_situacao_marco(
            nivel=MESTRADO,
            data_limite=self.LIMITE,
            apresentacoes=apresentacoes,
            data_referencia=referencia,
            concluido_marcado=concluido,
        )

    def test_conceito_b_conta_como_aprovado(self):
        r = self.marco(
            [Apresentacao(date(2026, 9, 18), "B")], date(2026, 11, 1), concluido=True
        )
        self.assertEqual(r.situacao, CUMPRIDO)
        self.assertFalse(r.avisos)

    def test_conceito_em_minuscula_e_com_espaco_e_aceito(self):
        r = self.marco(
            [Apresentacao(date(2026, 9, 18), " b ")], date(2026, 11, 1), concluido=True
        )
        self.assertEqual(r.situacao, CUMPRIDO)

    def test_conceito_d_no_mestrado_fica_sem_chance(self):
        r = self.marco([Apresentacao(date(2026, 9, 15), "D")], date(2026, 9, 16))
        self.assertEqual(r.situacao, IRREGULAR_SEM_CHANCE)
        self.assertIn("conceito D", r.motivo)

    def test_apresentacao_futura_nao_conta(self):
        # Em 01/11 ainda não houve a sessão de 10/12: vale como não apresentou.
        r = self.marco([Apresentacao(date(2026, 12, 10), "A")], date(2026, 11, 1))
        self.assertEqual(r.situacao, IRREGULAR_COM_CHANCE)
        self.assertEqual(r.prazo_reapresentacao, date(2026, 12, 20))


class TrancamentoNoMarcoIntermediarioTests(SimpleTestCase):
    """O trancamento estende também o prazo do projeto e da qualificação."""

    def test_caso_24_trancamento_estende_a_data_limite_do_projeto(self):
        # 20/09/2026 + 153 dias de trancamento (01/08 a 31/12/2026) = 20/02/2027
        t = trajetoria(
            "MESTRADO",
            data_limite=date(2026, 9, 20),
            trancamentos=[trancamento(date(2026, 8, 1), date(2026, 12, 31))],
        )
        r = calcular_situacao_trajetoria(t, date(2027, 1, 5), calendario={})
        self.assertEqual(r.situacao, REGULAR)
        self.assertEqual(r.data_limite, date(2027, 2, 20))

    def test_reapresentacao_conta_a_partir_da_data_limite_estendida(self):
        # Limite estendido para 20/02/2027: a reapresentação vai até 20/05/2027.
        t = trajetoria(
            "MESTRADO",
            data_limite=date(2026, 9, 20),
            trancamentos=[trancamento(date(2026, 8, 1), date(2026, 12, 31))],
        )
        r = calcular_situacao_trajetoria(t, date(2027, 3, 1), calendario={})
        self.assertEqual(r.situacao, IRREGULAR_COM_CHANCE)
        self.assertEqual(r.prazo_reapresentacao, date(2027, 5, 20))

    def test_trancamento_estende_a_data_do_calendario_no_doutorado(self):
        t = trajetoria(
            "DOUTORADO",
            prazo="2028.1",
            trancamentos=[trancamento(date(2027, 3, 1), date(2027, 3, 10))],
        )
        calendario = {("2028.1", QUALIFICACAO): date(2028, 6, 7)}
        r = calcular_situacao_trajetoria(t, date(2028, 6, 15), calendario=calendario)
        self.assertEqual(r.situacao, REGULAR)
        self.assertEqual(r.data_limite, date(2028, 6, 17))

    def test_sem_trancamento_a_data_limite_nao_muda(self):
        t = trajetoria("MESTRADO", data_limite=date(2026, 9, 20))
        r = calcular_situacao_trajetoria(t, date(2026, 9, 1), calendario={})
        self.assertEqual(r.data_limite, date(2026, 9, 20))
