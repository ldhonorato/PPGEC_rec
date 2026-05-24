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

#CRIAÇÃO DE PROCESSO======================================================================
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

#SOLICITAÇÃO DE CIÊNCIA E DEVOLUÇÃO DE PROCESSO===========================================
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
            "aluno": processo.usuario_criado_por,
            "orientador": orientador,
        }

        _send_email(
            subject=f"[PPGEC] Solicitação de Ciência - Processo {processo.numero}",
            template_name="emails/aluno/solicitacao_ciencia.html",
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
            "aluno": processo.usuario_criado_por,
            "observacao": observacao,
            "data_devolucao": timezone.now(),
        }

        _send_email(
            subject=f"[PPGEC] Ajustes necessários - Processo {processo.numero}",
            template_name="emails/orientador/devolucao_processo.html",
            contexto=contexto,
            recipient=processo.usuario_criado_por.email,
        )
    except Exception as exc:
        logger.exception("Falha ao enviar e-mail de devolução")
        raise self.retry(exc=exc)

#MOVIMENTAÇÃO DE PROCESSO=================================================================
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

#CONCLUSÃO DE PROCESSO===================================================================
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

#PLENO===================================================================================
@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def send_email_movimentacao_pleno(self, processo_id: int):
    """Avisa a todos os integrantes do Pleno sobre o novo processo"""
    from .models import Processo, User
    try:
        processo = Processo.objects.select_related("usuario_criado_por", "setor_atual").get(pk=processo_id)
    except Processo.DoesNotExist:
        logger.error("Processo %s não encontrado", processo_id)
        return

    docentes = User.objects.filter(tipo_usuario=User.TipoUsuario.DOCENTE)#pega todos os usuários do tipo docente

    contexto={
        "processo": processo,
        "aluno": processo.usuario_criado_por,
        "orientador": processo.obter_orientador_responsavel()
    }

    try:
        for docente in docentes:
            if docente.email:
                _send_email(
                    subject=f"[PPGEC] Novo processo em pauta no Pleno ({processo.numero})",
                    template_name="emails/pleno/novo_processo_pleno.html",
                    contexto=contexto,
                    recipient=docente.email,
                )
    except Exception as exc:
        logger.exception("Falha ao enviar e-mail de movimentação para o Pleno.")
        raise self.retry(exc=exc)


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def send_email_processo_comentado_pleno(self, processo_id: int, comentario_id: int):
    """Avisa a todos os integrantes do Pleno sobre comentário em um processo, cancelando sua aprovação automática e exigindo debate no Pleno"""
    from .models import Processo, ComentarioProcesso, User
    try:
        processo = Processo.objects.select_related("usuario_criado_por", "setor_atual").get(pk=processo_id)
        comentario = ComentarioProcesso.objects.select_related("autor").get(pk=comentario_id)
    except (Processo.DoesNotExist, ComentarioProcesso.DoesNotExist):
        logger.error("Processo %s ou Comentário %s não encontrado", processo_id, comentario_id)
        return

    docentes = User.objects.filter(tipo_usuario=User.TipoUsuario.DOCENTE)#pega todos os usuários do tipo docente
    
    contexto={
        "processo": processo,
        "aluno": processo.usuario_criado_por,
        "autor_comentario": comentario.autor
    }

    try:
        for docente in docentes:
            if docente.email:
                _send_email(
                    subject=f"[PPGEC] Intervenção no Processo {processo.numero} — Aprovação automática cancelada",
                    template_name="emails/pleno/processo_comentado_pleno.html",
                    contexto=contexto,
                    recipient=docente.email,
                )
    except Exception as exc:
        logger.exception("Falha ao enviar e-mail de comentário para o Pleno.")
        raise self.retry(exc=exc)
    
#CRIAÇÃO DE PROCESSO (PARA SECRETARIA), TRAMITAÇÃO ENTRE SETORES E MUDANÇA DE STATUS=====
@shared_task
def send_email_novo_processo_secretaria(processo_id: int):
    """Avisa a Secretaria (via e-mail do setor) que um novo processo foi aberto"""
    from .models import Processo, Setor
    try:
        processo = Processo.objects.select_related("usuario_criado_por", "setor_atual").get(pk=processo_id)
    except Processo.DoesNotExist:
        return

    setor_secretaria = processo.setor_atual #nasce na Secretaria

    if setor_secretaria and setor_secretaria.email: #segurança: se o setor existe E tem email cadastrado
        contexto = {
            "processo": processo,
            "aluno": processo.usuario_criado_por
        }
        _send_email(
            subject=f"[PPGEC] Novo Processo Aguardando Análise ({processo.numero})",
            template_name="emails/secretaria/novo_processo_secretaria.html",
            contexto=contexto,
            recipient=setor_secretaria.email
        )


@shared_task
def send_email_mudanca_setor(processo_id: int):
    """Avisa o NOVO setor que um processo foi tramitado para ele"""
    from .models import Processo
    try:
        processo = Processo.objects.select_related("setor_atual").get(pk=processo_id)
    except Processo.DoesNotExist:
        return

    setor_destino = processo.setor_atual
    
    if setor_destino and setor_destino.email: #segurança: se o setor existe E tem email cadastrado
        contexto = {"processo": processo}
        _send_email(
            subject=f"[PPGEC] Processo Tramitado para o seu Setor - Nº {processo.numero}",
            template_name="emails/setor/mudanca_setor.html",
            contexto=contexto,
            recipient=setor_destino.email
        )


@shared_task
def send_email_status_atualizado(processo_id: int, status_anterior: str, status_atual: str):
    """Avisa o setor atual que o processo sofreu uma alteração de status externa"""
    from .models import Processo
    try:
        processo = Processo.objects.select_related("setor_atual").get(pk=processo_id)
    except Processo.DoesNotExist:
        return

    setor_atual = processo.setor_atual

    if setor_atual and setor_atual.email: #segurança: se o setor existe E tem email cadastrado
        contexto = {
            "processo": processo,
            "status_anterior": status_anterior,
            "status_atual": status_atual
        }
        _send_email(
            subject=f"[PPGEC] Alteração de Status Interno: Processo {processo.numero}",
            template_name="emails/setor/status_atualizado.html",
            contexto=contexto,
            recipient=setor_atual.email
        )