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
            template_name="emails/novo_processo_aluno.html",
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
            template_name="emails/novo_processo_orientador.html",
            contexto=contexto,
            recipient=orientador.email,
        )
    except Exception as exc:
        logger.exception("Falha ao enviar e-mail para orientador")
        raise self.retry(exc=exc)