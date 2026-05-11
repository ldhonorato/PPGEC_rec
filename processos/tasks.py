from celery import shared_task
from django.core.mail import send_mail
from django.template.loader import render_to_string
import logging

logger = logging.getLogger(__name__)


def _send_email(subject, template_name, contexto, recipient):
    html = render_to_string(template_name, contexto)
    send_mail(
        subject=subject,
        message="",
        from_email=None,
        recipient_list=[recipient],
        html_message=html,
        fail_silently=False,
    )


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def send_email_novo_processo_aluno(self, processo_id: int):
    from .models import Processo
    try:
        processo = Processo.objects.select_related("usuario_criado_por", "setor_atual").get(pk=processo_id)
    except Processo.DoesNotExist:
        logger.error("Processo %s não encontrado", processo_id)
        return

    contexto = {"processo": processo, "aluno": processo.usuario_criado_por, "orientador": processo.obter_orientador_responsavel()}
    try:
        _send_email(
            subject=f"[PPGEC] Processo {processo.numero} aberto com sucesso",
            template_name="emails/aluno/novo_processo_aluno.html",
            contexto=contexto,
            recipient=processo.usuario_criado_por.email,
        )
    except Exception as exc:
        logger.exception("Falha ao enviar e-mail para aluno")
        raise self.retry(exc=exc)


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def send_email_novo_processo_orientador(self, processo_id: int):
    from .models import Processo
    try:
        processo = Processo.objects.select_related("usuario_criado_por", "setor_atual").get(pk=processo_id)
    except Processo.DoesNotExist:
        logger.error("Processo %s não encontrado", processo_id)
        return

    orientador = processo.obter_orientador_responsavel()
    if not orientador:
        return

    contexto = {"processo": processo, "aluno": processo.usuario_criado_por, "orientador": orientador}
    try:
        _send_email(
            subject=f"[PPGEC] Novo processo do orientando {processo.usuario_criado_por.nome}",
            template_name="emails/orientador/novo_processo_orientador.html",
            contexto=contexto,
            recipient=orientador.email,
        )
    except Exception as exc:
        logger.exception("Falha ao enviar e-mail para orientador")
        raise self.retry(exc=exc)
    
#ENVIO DE EMAIL, MOVIMENTAÇÃO DE PROCESSO
@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def send_email_movimentacao_aluno(self, processo_id: int, mensagem_status: str):
    from .models import Processo
    try:
        processo = Processo.objects.select_related("usuario_criado_por").get(pk=processo_id)
    except Processo.DoesNotExist:
        logger.error("Processo %s não encontrado", processo_id)
        return

    contexto = {
        "processo": processo,
        "mensagem_status": mensagem_status,
        "aluno": processo.usuario_criado_por,
        "orientador": processo.obter_orientador_responsavel()
    }
    
    try:
        _send_email(
            subject=f"[PPGEC] Movimentação no Processo {processo.numero}",
            template_name="emails/aluno/movimentacao_processo_aluno.html",
            contexto=contexto,
            recipient=processo.usuario_criado_por.email,
        )
    except Exception as exc:
        logger.exception("Falha ao enviar e-mail de movimentação para aluno")
        raise self.retry(exc=exc)


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def send_email_conclusao_aluno(self, processo_id: int):
    from .models import Processo
    try:
        processo = Processo.objects.select_related("usuario_criado_por").get(pk=processo_id)
    except Processo.DoesNotExist:
        logger.error("Processo %s não encontrado", processo_id)
        return

    contexto = {
        "processo": processo,
        "aluno": processo.usuario_criado_por,
        "orientador": processo.obter_orientador_responsavel(),
    }
    try:
        _send_email(
            subject=f"[PPGEC] Processo {processo.numero} finalizado",
            template_name="emails/aluno/conclusao_processo_aluno.html",
            contexto=contexto,
            recipient=processo.usuario_criado_por.email,
        )
    except Exception as exc:
        logger.exception("Falha ao enviar e-mail de conclusão para aluno")
        raise self.retry(exc=exc)


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def send_email_conclusao_orientador(self, processo_id: int):
    from .models import Processo
    try:
        processo = Processo.objects.select_related("usuario_criado_por").get(pk=processo_id)
    except Processo.DoesNotExist:
        logger.error("Processo %s não encontrado", processo_id)
        return

    orientador = processo.obter_orientador_responsavel()
    if not orientador:
        return

    contexto = {
        "processo": processo,
        "aluno": processo.usuario_criado_por,
        "orientador": orientador,
    }
    try:
        _send_email(
            subject=f"[PPGEC] Processo do orientando {processo.usuario_criado_por.nome} finalizado",
            template_name="emails/orientador/conclusao_processo_orientador.html",
            contexto=contexto,
            recipient=orientador.email,
        )
    except Exception as exc:
        logger.exception("Falha ao enviar e-mail de conclusão para orientador")
        raise self.retry(exc=exc)


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def send_email_movimentacao_orientador(self, processo_id: int, mensagem_status: str):
    from .models import Processo
    try:
        processo = Processo.objects.select_related("usuario_criado_por").get(pk=processo_id)
    except Processo.DoesNotExist:
        logger.error("Processo %s não encontrado", processo_id)
        return

    orientador = processo.obter_orientador_responsavel()
    if not orientador:
        return

    contexto = {
        "processo": processo,
        "mensagem_status": mensagem_status,
        "aluno": processo.usuario_criado_por,
        "orientador": orientador
    }
    
    try:
        _send_email(
            subject=f"[PPGEC] Movimentação no Processo do Orientando {processo.usuario_criado_por.nome}",
            template_name="emails/orientador/movimentacao_processo_orientador.html",
            contexto=contexto,
            recipient=orientador.email,
        )
    except Exception as exc:
        logger.exception("Falha ao enviar e-mail de movimentação para orientador")
        raise self.retry(exc=exc)
    
@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def send_email_solicitacao_ciencia(self, manifestacao_id):
    from .models import ManifestacaoProcesso
    try:
        manifestacao = ManifestacaoProcesso.objects.select_related('processo', 'responsavel').get(id=manifestacao_id)
        processo = manifestacao.processo
        orientador = manifestacao.responsavel

        contexto = {
            "processo": processo,
            "manifestacao": manifestacao,
            "aluno": processo.usuario_criado_por.get_full_name() or processo.usuario_criado_por.username,
        }
        
        _send_email(
            subject=f"[PPGEC] Solicitação de Ciência - Processo {processo.numero}",
            template_name="emails/orientador/solicitacao_ciencia.html",
            contexto=contexto,
            recipient=orientador.email,
        )
    except Exception as exc:
        logger.exception("Falha ao enviar e-mail de solicitação de ciência")
        raise self.retry(exc=exc)

@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def send_email_devolucao_requerente(self, processo_id, observacao):
    from .models import Processo
    from django.utils import timezone
    try:
        processo = Processo.objects.select_related("usuario_criado_por").get(pk=processo_id)
        
        contexto = {
            "processo": processo,
            "observacao": observacao,
            "data_devolucao": timezone.now(),
        }
        
        _send_email(
            subject=f"[PPGEC] Ajustes necessários - Processo {processo.numero}",
            template_name="emails/aluno/devolucao_processo.html",
            contexto=contexto,
            recipient=processo.usuario_criado_por.email,
        )
    except Exception as exc:
        logger.exception("Falha ao enviar e-mail de devolução")
        raise self.retry(exc=exc)