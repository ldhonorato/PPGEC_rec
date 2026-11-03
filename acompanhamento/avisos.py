"""Aviso diário de alunos que ficaram atrasados.

Calcula a situação de todos os alunos ativos, guarda um aviso para quem
ficou atrasado e manda um e-mail só com os avisos novos para a Secretaria
PPGEC e a Coordenação PPG. Sem aviso novo, nenhum e-mail sai.

O aviso não se perde: se o envio falha (depois de TENTATIVAS_ENVIO
tentativas) ou se ninguém está nos dois setores, o aviso continua sem
data de envio e entra no e-mail do dia seguinte.
"""

import logging
from time import sleep

from django.conf import settings
from django.core.mail import send_mail
from django.db import transaction
from django.db.models import F
from django.template.loader import render_to_string
from django.utils import timezone

from acompanhamento.lista_alunos import _trajetorias_ativas
from acompanhamento.models import AvisoIrregularidade, ExecucaoAviso
from acompanhamento.situacao import (
    IRREGULAR_COM_CHANCE,
    IRREGULAR_SEM_CHANCE,
    NAO_CALCULADO,
    calcular_situacoes,
)
from processos.models import Aluno, Setor, User

ATRASADO = (IRREGULAR_COM_CHANCE, IRREGULAR_SEM_CHANCE)
MARCOS = (AvisoIrregularidade.Marco.PROJETO_QUALIFICACAO, AvisoIrregularidade.Marco.DEFESA)
SETORES_AVISADOS = (Setor.NOME_SECRETARIA, Setor.NOME_COORDENACAO)

TENTATIVAS_ENVIO = 3
PAUSA_ENTRE_TENTATIVAS = 30  # segundos

logger = logging.getLogger(__name__)


def rodar_aviso_diario(data_referencia=None):
    """Atualiza os avisos e manda o e-mail do dia.

    O e-mail leva os avisos novos e também os que ficaram pendentes de dias
    anteriores (envio que falhou ou sem destinatário).
    """
    novos = atualizar_avisos(data_referencia)
    enviados = enviar_email(avisos_pendentes())
    ExecucaoAviso.objects.create(
        avisos_novos=len(novos),
        emails_enviados=enviados,
        avisos_pendentes=contar_pendentes(),
    )
    return {"avisos_novos": len(novos), "emails_enviados": enviados}


@transaction.atomic
def atualizar_avisos(data_referencia=None):
    """Cria os avisos de quem ficou atrasado e encerra os de quem saiu.

    Devolve os avisos criados agora.
    """
    if data_referencia is None:
        data_referencia = timezone.localdate()
    agora = timezone.now()

    trajetorias = _trajetorias_ativas(Aluno.objects.all())
    situacoes = calcular_situacoes(trajetorias, data_referencia)

    abertos = {}
    for aviso in AvisoIrregularidade.objects.filter(encerrado_em__isnull=True):
        abertos[(aviso.trajetoria_id, aviso.marco)] = aviso

    novos = []
    for trajetoria in trajetorias:
        for marco in MARCOS:
            resultado = situacoes[trajetoria.pk][marco]
            situacao = resultado.situacao
            aberto = abertos.pop((trajetoria.pk, marco), None)

            # Continua atrasado do mesmo jeito: o aviso já existe.
            if aberto and aberto.situacao == situacao:
                continue

            # Não deu para calcular hoje: o aviso aberto fica como está, senão
            # o aluno voltaria como novo no e-mail de amanhã.
            if situacao == NAO_CALCULADO:
                continue

            # Saiu do atraso ou mudou de situação: o aviso antigo acaba.
            if aberto:
                aberto.encerrado_em = agora
                aberto.save(update_fields=["encerrado_em"])

            if situacao in ATRASADO:
                aviso = AvisoIrregularidade.objects.create(
                    trajetoria=trajetoria,
                    marco=marco,
                    situacao=situacao,
                    data_limite=resultado.data_limite,
                    prazo_reapresentacao=resultado.prazo_reapresentacao,
                    motivo=resultado.motivo,
                )
                novos.append(aviso)

    # Sobram os avisos de quem deixou de estar ativo (trancou, foi desligado
    # ou concluiu). Eles também acabam.
    for aviso in abertos.values():
        aviso.encerrado_em = agora
        aviso.save(update_fields=["encerrado_em"])

    return novos


def destinatarios():
    """E-mails dos membros atuais da Secretaria PPGEC e da Coordenação PPG."""
    emails = (
        User.objects.filter(
            is_active=True,
            participacoes_setor__setor__nome__in=SETORES_AVISADOS,
            participacoes_setor__data_saida__isnull=True,
        )
        .exclude(email="")
        .values_list("email", flat=True)
        .distinct()
    )
    return sorted(emails)


def avisos_pendentes():
    """Avisos abertos que ainda não foram enviados por e-mail."""
    return list(
        AvisoIrregularidade.objects.filter(
            encerrado_em__isnull=True, enviado_em__isnull=True
        )
        .select_related("trajetoria__aluno")
        .order_by("trajetoria__aluno__nome", "marco")
    )


def contar_pendentes():
    """Quantos avisos abertos ainda não foram enviados por e-mail."""
    return AvisoIrregularidade.objects.filter(
        encerrado_em__isnull=True, enviado_em__isnull=True
    ).count()


def montar_email(avisos):
    """Assunto e corpo (HTML) do e-mail com os avisos. Não envia nada.

    Enquanto nenhum aviso tiver saído, o e-mail é o levantamento inicial:
    traz todos os alunos que já estavam atrasados quando o aviso foi ligado.
    """
    levantamento_inicial = not AvisoIrregularidade.objects.filter(
        enviado_em__isnull=False
    ).exists()
    alunos = len({aviso.trajetoria.aluno_id for aviso in avisos})
    assunto = f"[PPGEC] {alunos} aluno(s) com prazo vencido"
    if levantamento_inicial:
        assunto = f"[PPGEC] Levantamento inicial: {alunos} aluno(s) com prazo vencido"

    html = render_to_string(
        "acompanhamento/emails/aviso_irregularidade.html",
        {
            "avisos": avisos,
            "site_url": settings.SITE_URL,
            "levantamento_inicial": levantamento_inicial,
        },
    )
    return assunto, html


def enviar_email(avisos):
    """Manda um e-mail com os avisos. Devolve 1 se enviou e 0 se não.

    Se o envio falhar, tenta de novo até TENTATIVAS_ENVIO vezes. Se continuar
    falhando, ou se não houver destinatário, os avisos ficam pendentes e o
    motivo fica gravado em cada um.
    """
    if not avisos:
        return 0
    ids = [aviso.pk for aviso in avisos]

    emails = destinatarios()
    if not emails:
        motivo = "Ninguém na Secretaria PPGEC nem na Coordenação PPG para receber."
        AvisoIrregularidade.objects.filter(pk__in=ids).update(motivo_pendencia=motivo)
        logger.warning(
            "Aviso de irregularidade sem destinatário: %d aviso(s) ficam "
            "pendentes para o próximo dia.",
            len(avisos),
        )
        return 0

    assunto, html = montar_email(avisos)

    erro = ""
    for tentativa in range(1, TENTATIVAS_ENVIO + 1):
        try:
            send_mail(
                subject=assunto,
                message="",
                from_email=None,
                recipient_list=emails,
                html_message=html,
                fail_silently=False,
            )
        except Exception as exc:
            erro = f"{type(exc).__name__}: {exc}"
            logger.warning(
                "Falha ao enviar o aviso de irregularidade (tentativa %d de %d).",
                tentativa,
                TENTATIVAS_ENVIO,
                exc_info=True,
            )
            if tentativa < TENTATIVAS_ENVIO:
                sleep(PAUSA_ENTRE_TENTATIVAS)
            continue

        AvisoIrregularidade.objects.filter(pk__in=ids).update(
            enviado_em=timezone.now(),
            tentativas_envio=F("tentativas_envio") + tentativa,
            motivo_pendencia="",
        )
        return 1

    AvisoIrregularidade.objects.filter(pk__in=ids).update(
        tentativas_envio=F("tentativas_envio") + TENTATIVAS_ENVIO,
        motivo_pendencia=f"Envio falhou {TENTATIVAS_ENVIO} vezes. Último erro: {erro}",
    )
    logger.error(
        "Aviso de irregularidade não enviado após %d tentativas: %d aviso(s) "
        "ficam pendentes para o próximo dia.",
        TENTATIVAS_ENVIO,
        len(avisos),
    )
    return 0
