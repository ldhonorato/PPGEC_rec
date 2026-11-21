"""Regra de situação do aluno em cada marco.

Projeto de dissertação (mestrado) e qualificação (doutorado): a data
limite é a da sessão. Quem não apresenta até ela tem uma única nova
chance, em 3 meses (mestrado) ou 6 meses (doutorado).

Defesa: o prazo regimental termina no último dia do mês em que se
completam 24 meses (mestrado) ou 48 meses (doutorado) do ingresso. O
limite efetivo soma a esse prazo as prorrogações (em meses) e os dias
de trancamento.
"""

import calendar
import logging
from dataclasses import dataclass, field, replace
from datetime import date, timedelta

logger = logging.getLogger(__name__)

REGULAR = "regular"
IRREGULAR_COM_CHANCE = "irregular_com_chance"
IRREGULAR_SEM_CHANCE = "irregular_sem_chance"
CUMPRIDO = "cumprido"
SEM_PRAZO = "sem_prazo"
FORA_DO_CALCULO = "fora_do_calculo"
NAO_CALCULADO = "nao_calculado"

MESTRADO = "mestrado"
DOUTORADO = "doutorado"

ORIGEM_TRAJETORIA = "trajetoria"
ORIGEM_CALENDARIO = "calendario"

MESES_REAPRESENTACAO = {MESTRADO: 3, DOUTORADO: 6}
MESES_PRAZO_DEFESA = {MESTRADO: 24, DOUTORADO: 48}
CONCEITOS_APROVADOS = ("A", "B")


@dataclass(frozen=True)
class Apresentacao:
    data: date
    conceito: str


@dataclass(frozen=True)
class Resultado:
    situacao: str
    data_limite: date | None = None
    prazo_reapresentacao: date | None = None
    motivo: str = ""
    avisos: list = field(default_factory=list)
    origem_data_limite: str = ""


def somar_meses(data, meses):
    """Soma meses mantendo o dia; se o mês não tem o dia, usa o último."""
    indice = data.year * 12 + (data.month - 1) + meses
    ano, mes = divmod(indice, 12)
    mes += 1
    ultimo_dia = calendar.monthrange(ano, mes)[1]
    return date(ano, mes, min(data.day, ultimo_dia))


def _formatar(data):
    return data.strftime("%d/%m/%Y")


def calcular_situacao_marco(
    nivel,
    data_limite,
    apresentacoes,
    data_referencia,
    concluido_marcado=False,
):
    """Projeto de dissertação (mestrado) ou qualificação (doutorado)."""
    meses = MESES_REAPRESENTACAO[nivel]
    feitas = sorted(
        (a for a in apresentacoes if a.data <= data_referencia),
        key=lambda a: a.data,
    )
    aprovada = any(a.conceito.strip().upper() in CONCEITOS_APROVADOS for a in feitas)

    if aprovada or concluido_marcado:
        avisos = []
        if not aprovada:
            avisos.append(
                "Marcação de projeto/qualificação concluído sem apresentação "
                "aprovada registrada. Conferir o cadastro."
            )
        elif not concluido_marcado:
            avisos.append(
                "Apresentação aprovada registrada, mas a trajetória não está "
                "marcada como projeto/qualificação concluído. Conferir o cadastro."
            )
        return Resultado(
            CUMPRIDO,
            data_limite=data_limite,
            motivo="Marco cumprido.",
            avisos=avisos,
        )

    if data_limite is None:
        return Resultado(
            SEM_PRAZO,
            motivo="Não há data limite cadastrada para o semestre do marco.",
        )

    if any(a.conceito.strip().upper() == "D" for a in feitas):
        return Resultado(
            IRREGULAR_SEM_CHANCE,
            data_limite=data_limite,
            motivo="Reprovado com conceito D.",
        )

    if not feitas:
        if data_referencia <= data_limite:
            return Resultado(
                REGULAR,
                data_limite=data_limite,
                motivo=f"Data limite em {_formatar(data_limite)}.",
            )
        prazo = somar_meses(data_limite, meses)
        if data_referencia <= prazo:
            return Resultado(
                IRREGULAR_COM_CHANCE,
                data_limite=data_limite,
                prazo_reapresentacao=prazo,
                motivo=(
                    f"Não apresentou até {_formatar(data_limite)}. "
                    f"Reapresentação até {_formatar(prazo)}."
                ),
            )
        return Resultado(
            IRREGULAR_SEM_CHANCE,
            data_limite=data_limite,
            prazo_reapresentacao=prazo,
            motivo=(
                f"Não apresentou até {_formatar(data_limite)} nem na "
                f"reapresentação, que ia até {_formatar(prazo)}."
            ),
        )

    primeira = feitas[0]

    if primeira.data > data_limite:
        return Resultado(
            IRREGULAR_SEM_CHANCE,
            data_limite=data_limite,
            motivo=(
                f"Apresentou depois da data limite ({_formatar(data_limite)}), "
                "na reapresentação, e não foi aprovado."
            ),
        )

    if len(feitas) > 1:
        return Resultado(
            IRREGULAR_SEM_CHANCE,
            data_limite=data_limite,
            motivo="Não foi aprovado na repetição, que era a última chance.",
        )

    prazo = somar_meses(primeira.data, meses)
    if data_referencia <= prazo:
        return Resultado(
            IRREGULAR_COM_CHANCE,
            data_limite=data_limite,
            prazo_reapresentacao=prazo,
            motivo=(
                f"Conceito C em {_formatar(primeira.data)}. "
                f"Repetição até {_formatar(prazo)}."
            ),
        )
    return Resultado(
        IRREGULAR_SEM_CHANCE,
        data_limite=data_limite,
        prazo_reapresentacao=prazo,
        motivo=(
            f"Conceito C em {_formatar(primeira.data)} e a repetição, "
            f"que ia até {_formatar(prazo)}, não aconteceu."
        ),
    )


def calcular_situacao_defesa(
    nivel,
    mes_ingresso,
    prorrogacao_meses,
    data_referencia,
    defesa_registrada=False,
    dias_trancamento=0,
):
    """Defesa: limite efetivo = prazo regimental + prorrogações + trancamento."""
    if defesa_registrada:
        return Resultado(FORA_DO_CALCULO, motivo="Defesa já registrada.")

    if mes_ingresso is None:
        return Resultado(SEM_PRAZO, motivo="Não há mês de ingresso cadastrado.")

    # prazo regimental: último dia do mês, 24 ou 48 meses após o ingresso
    fim = somar_meses(mes_ingresso, MESES_PRAZO_DEFESA[nivel])
    ultimo_dia = calendar.monthrange(fim.year, fim.month)[1]
    prazo_regimental = date(fim.year, fim.month, ultimo_dia)

    # limite efetivo: soma as prorrogações (meses) e o trancamento (dias).
    # A prorrogação é contada em meses e vale até o fim do mês em que termina,
    # como respondeu a secretaria em 12/10/2026: ingresso em fevereiro de 2025
    # com 3 meses de prorrogação vai até maio de 2027, e não até o dia 28.
    fim_prorrogacao = somar_meses(prazo_regimental, prorrogacao_meses)
    ultimo_dia = calendar.monthrange(fim_prorrogacao.year, fim_prorrogacao.month)[1]
    data_limite = date(fim_prorrogacao.year, fim_prorrogacao.month, ultimo_dia)
    data_limite = data_limite + timedelta(days=dias_trancamento)

    if data_referencia <= data_limite:
        return Resultado(
            REGULAR,
            data_limite=data_limite,
            motivo=f"Defesa até {_formatar(data_limite)}.",
        )
    return Resultado(
        IRREGULAR_SEM_CHANCE,
        data_limite=data_limite,
        motivo=f"Não defendeu até {_formatar(data_limite)}.",
    )


def resolver_data_limite(trajetoria, calendario=None):
    """Data limite do marco intermediário do aluno e de onde ela veio.

    Mestrado: vale a "Data limite do projeto" da trajetória, se preenchida;
    senão, a data do calendário para o semestre do projeto. Doutorado: vale
    a data do calendário para o quinto semestre. Sem nenhuma das duas,
    devolve (None, "").

    Se receber o calendário já carregado ({(semestre, tipo): data}), não
    consulta o banco.
    """
    from acompanhamento.models import MarcoCalendario

    if trajetoria.nivel_curso == "MESTRADO":
        if trajetoria.data_limite_qualificacao:
            return trajetoria.data_limite_qualificacao, ORIGEM_TRAJETORIA
        semestre = trajetoria.prazo_qualificacao
        tipo = MarcoCalendario.TipoMarco.PROJETO_DISSERTACAO
    elif trajetoria.nivel_curso == "DOUTORADO":
        semestre = (
            trajetoria.prazo_qualificacao or trajetoria.prazo_qualificacao_regimental
        )
        tipo = MarcoCalendario.TipoMarco.QUALIFICACAO
    else:
        return None, ""

    if not semestre:
        return None, ""
    if calendario is None:
        data = MarcoCalendario.data_limite_do(semestre, tipo)
    else:
        data = calendario.get((semestre, tipo))
    return (data, ORIGEM_CALENDARIO) if data else (None, "")


def _dias_trancados(trajetoria):
    """Dias de trancamento da trajetória, contando o primeiro e o último dia,
    como em TrajetoriaAcademica.dias_trancados."""
    return sum(
        (t.data_fim - t.data_inicio).days + 1 for t in trajetoria.trancamentos.all()
    )


def calcular_situacao_trajetoria(trajetoria, data_referencia, calendario=None):
    """Situação de uma trajetória no projeto/qualificação, lendo o cadastro."""
    nivel = MESTRADO if trajetoria.nivel_curso == "MESTRADO" else DOUTORADO
    data_limite, origem = resolver_data_limite(trajetoria, calendario)
    # O trancamento estende também o prazo do projeto e da qualificação, e
    # não só o da defesa, como confirmou a secretaria em 12/10/2026.
    if data_limite is not None:
        data_limite += timedelta(days=_dias_trancados(trajetoria))
    apresentacoes = [
        Apresentacao(a.data_apresentacao, a.conceito)
        for a in trajetoria.apresentacoes_qualificacao.all()
    ]
    resultado = calcular_situacao_marco(
        nivel=nivel,
        data_limite=data_limite,
        apresentacoes=apresentacoes,
        data_referencia=data_referencia,
        concluido_marcado=trajetoria.isQualificado,
    )
    return replace(resultado, origem_data_limite=origem)


def calcular_situacao_defesa_trajetoria(trajetoria, data_referencia):
    """Situação de uma trajetória na defesa, lendo o cadastro."""
    nivel = MESTRADO if trajetoria.nivel_curso == "MESTRADO" else DOUTORADO
    prorrogacao = sum(p.meses for p in trajetoria.prorrogacoes.all())
    return calcular_situacao_defesa(
        nivel=nivel,
        mes_ingresso=trajetoria.data_ingresso,
        prorrogacao_meses=prorrogacao,
        data_referencia=data_referencia,
        defesa_registrada=trajetoria.data_defesa is not None,
        dias_trancamento=_dias_trancados(trajetoria),
    )


def calcular_situacoes(trajetorias, data_referencia):
    """Situação de várias trajetórias de uma vez, no marco e na defesa.

    Carrega o calendário e os dados de todas as trajetórias em um número
    fixo de consultas, que não cresce com a quantidade de alunos. Devolve
    {id da trajetória: {"marco": Resultado, "defesa": Resultado}}.
    Só mestrado e doutorado entram no cálculo; os demais níveis ficam
    fora do cálculo.
    """
    from django.db.models import prefetch_related_objects

    from acompanhamento.models import MarcoCalendario

    trajetorias = list(trajetorias)
    prefetch_related_objects(
        trajetorias, "apresentacoes_qualificacao", "prorrogacoes", "trancamentos"
    )
    calendario = {
        (m.semestre, m.tipo): m.data_limite for m in MarcoCalendario.objects.all()
    }

    situacoes = {}
    for trajetoria in trajetorias:
        if trajetoria.nivel_curso not in ("MESTRADO", "DOUTORADO"):
            fora = Resultado(FORA_DO_CALCULO, motivo="Nível sem prazos acadêmicos.")
            situacoes[trajetoria.pk] = {"marco": fora, "defesa": fora}
            continue
        # Um cadastro com dado inesperado não pode derrubar a lista inteira:
        # a falha fica registrada no log e só aquela trajetória sai sem cálculo.
        try:
            situacoes[trajetoria.pk] = {
                "marco": calcular_situacao_trajetoria(
                    trajetoria, data_referencia, calendario
                ),
                "defesa": calcular_situacao_defesa_trajetoria(
                    trajetoria, data_referencia
                ),
            }
        except Exception:
            logger.exception(
                "Falha ao calcular a situação da trajetória %s.", trajetoria.pk
            )
            falha = Resultado(NAO_CALCULADO, motivo="Não foi possível calcular.")
            situacoes[trajetoria.pk] = {"marco": falha, "defesa": falha}
    return situacoes