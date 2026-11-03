from celery import shared_task

from acompanhamento.chave import aviso_ativo


@shared_task(name="acompanhamento.tasks.enviar_avisos_irregularidade")
def enviar_avisos_irregularidade():
    if not aviso_ativo():
        return {"avisos_novos": 0, "emails_enviados": 0}

    from acompanhamento.avisos import rodar_aviso_diario

    return rodar_aviso_diario()