"""Lista de alunos pela situação num marco acadêmico.

A tela de alunos aplica os filtros que já existem (nome, nível, ingresso,
status, polo) e passa o resultado para cá. Esta função completa o filtro com
o que não é coluna do banco: a situação do aluno no marco, calculada pelo
núcleo em acompanhamento.situacao. Só a trajetória ativa de mestrado ou
doutorado entra, e todos os alunos são carregados de uma vez, com número
fixo de consultas.
"""

import logging
import re
from dataclasses import dataclass
from urllib.parse import urlencode

from django.urls import reverse
from django.utils import timezone

from acompanhamento.models import MarcoCalendario
from acompanhamento.situacao import (
    CUMPRIDO,
    FORA_DO_CALCULO,
    IRREGULAR_COM_CHANCE,
    IRREGULAR_SEM_CHANCE,
    NAO_CALCULADO,
    REGULAR,
    SEM_PRAZO,
    Resultado,
    calcular_situacoes,
)
from processos.models import Aluno, TrajetoriaAcademica

logger = logging.getLogger(__name__)

MARCO_INTERMEDIARIO = "marco"
DEFESA = "defesa"
QUALQUER_MARCO = "qualquer"
MARCOS = {
    MARCO_INTERMEDIARIO: "Projeto de dissertação ou qualificação",
    DEFESA: "Defesa",
    QUALQUER_MARCO: "Qualquer marco",
}

IRREGULARES = "irregulares"
SITUACOES = {
    REGULAR: "Regular",
    IRREGULAR_COM_CHANCE: "Irregular",
    IRREGULAR_SEM_CHANCE: "Irregular",
    CUMPRIDO: "Regular",
    SEM_PRAZO: "Não foi possível calcular",
    NAO_CALCULADO: "Não foi possível calcular",
}
SITUACOES_FILTRO = {
    IRREGULARES: "Irregular",
    REGULAR: "Regular",
    NAO_CALCULADO: "Não foi possível calcular",
}
SITUACOES_POR_FILTRO = {
    IRREGULARES: (IRREGULAR_COM_CHANCE, IRREGULAR_SEM_CHANCE),
    REGULAR: (REGULAR, CUMPRIDO),
    NAO_CALCULADO: (SEM_PRAZO, NAO_CALCULADO),
}

_FORMATO_SEMESTRE = re.compile(r"^\d{4}\.[12]$")


@dataclass(frozen=True)
class LinhaAluno:
    aluno: Aluno
    trajetoria: TrajetoriaAcademica
    resultado: Resultado
    semestre_marco: str
    marco_display: str = ""
    resultados_marco: tuple[tuple[str, Resultado], ...] = ()

    @property
    def situacao_display(self):
        return SITUACOES.get(self.resultado.situacao, self.resultado.situacao)


def validar_filtros(marco, situacao="", semestre_marco=""):
    """Confere os filtros recebidos da tela. Levanta ValueError com a mensagem
    que a tela mostra quando algum deles é inválido."""
    if marco not in MARCOS:
        raise ValueError("Marco inválido.")
    if marco == QUALQUER_MARCO:
        if situacao != IRREGULARES:
            raise ValueError('“Qualquer marco” só aceita a situação “Irregular”.')
        if semestre_marco:
            raise ValueError('Não é possível filtrar “Qualquer marco” por semestre.')
    if situacao and situacao not in SITUACOES_FILTRO:
        raise ValueError("Situação inválida.")
    if semestre_marco and not _FORMATO_SEMESTRE.match(semestre_marco):
        raise ValueError("Semestre do marco inválido. Use o formato AAAA.1 ou AAAA.2.")


def _trajetorias_ativas(alunos):
    """A trajetória ativa de mestrado ou doutorado de cada aluno, numa consulta.

    Se um aluno tiver mais de uma trajetória ativa, vale a mais recente, como
    em Aluno.trajetoria_ativa().
    """
    trajetorias = (
        TrajetoriaAcademica.objects.filter(
            aluno__in=alunos.values("pk"),
            status=TrajetoriaAcademica.Status.ATIVA,
            nivel_curso__in=[Aluno.NivelCurso.MESTRADO, Aluno.NivelCurso.DOUTORADO],
        )
        .select_related("aluno", "aluno__polo_atuacao", "orientador")
        .order_by("aluno_id", "-criado_em")
    )
    por_aluno = {}
    for trajetoria in trajetorias:
        por_aluno.setdefault(trajetoria.aluno_id, trajetoria)
    return list(por_aluno.values())


def _combina_situacao(resultado, situacao):
    if not situacao:
        return True
    return resultado.situacao in SITUACOES_POR_FILTRO[situacao]


def listar_alunos_por_marco(
    alunos, marco, situacao="", semestre_marco="", data_referencia=None
):
    """Alunos com a situação no marco escolhido, já filtrados e ordenados.

    alunos: queryset de Aluno com os filtros da tela já aplicados.
    marco: um marco de MARCOS. QUALQUER_MARCO exige situação IRREGULARES e
    não aceita filtro por semestre.
    situacao: uma das chaves de SITUACOES_FILTRO ou vazio para todas.
    semestre_marco: "AAAA.1" ou "AAAA.2"; mantém só quem tem a data limite
    do marco nesse semestre.
    data_referencia: data do cálculo; sem ela, vale o dia de hoje.
    """
    validar_filtros(marco, situacao, semestre_marco)
    if data_referencia is None:
        data_referencia = timezone.localdate()

    trajetorias = _trajetorias_ativas(alunos)
    situacoes = calcular_situacoes(trajetorias, data_referencia)

    linhas = []
    for trajetoria in trajetorias:
        if marco == QUALQUER_MARCO:
            resultados_marco = tuple(
                (MARCOS[chave], situacoes[trajetoria.pk][chave])
                for chave in (MARCO_INTERMEDIARIO, DEFESA)
                if _combina_situacao(
                    situacoes[trajetoria.pk][chave], IRREGULARES
                )
            )
            if not resultados_marco:
                continue
            linhas.append(
                LinhaAluno(
                    aluno=trajetoria.aluno,
                    trajetoria=trajetoria,
                    resultado=resultados_marco[0][1],
                    semestre_marco="",
                    marco_display=" e ".join(
                        nome_marco for nome_marco, _ in resultados_marco
                    ),
                    resultados_marco=resultados_marco,
                )
            )
            continue

        resultado = situacoes[trajetoria.pk][marco]
        # Quem já defendeu sai do cálculo e não aparece na lista.
        if resultado.situacao == FORA_DO_CALCULO:
            continue
        semestre = (
            MarcoCalendario.semestre_da_data(resultado.data_limite)
            if resultado.data_limite
            else ""
        )
        if not _combina_situacao(resultado, situacao):
            continue
        if semestre_marco and semestre != semestre_marco:
            continue
        linhas.append(
            LinhaAluno(
                aluno=trajetoria.aluno,
                trajetoria=trajetoria,
                resultado=resultado,
                semestre_marco=semestre,
                marco_display=MARCOS[marco],
                resultados_marco=((MARCOS[marco], resultado),),
            )
        )
    linhas.sort(key=lambda linha: (linha.aluno.nome or "").lower())
    return linhas


def cartao_alunos_irregulares(data_referencia=None):
    """Total e endereço da lista de alunos irregulares em qualquer marco."""
    try:
        linhas = listar_alunos_por_marco(
            Aluno.objects.all(),
            QUALQUER_MARCO,
            situacao=IRREGULARES,
            data_referencia=data_referencia,
        )
        query = urlencode(
            {"marco": QUALQUER_MARCO, "situacao_marco": IRREGULARES}
        )
        return {
            "total": len(linhas),
            "endereco": f"{reverse('coordenacao_alunos')}?{query}",
        }
    except Exception:
        logger.exception("Falha ao calcular o cartão de alunos irregulares.")
        return None
