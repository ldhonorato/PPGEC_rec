"""
Config file for Celery, this file sets up the Celery application, 
configures it to use Django settings, and enables automatic discovery
of tasks defined in the Django app.
"""

import os
from celery import Celery
from celery.schedules import crontab

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ppgec.settings")

app = Celery("ppgec")

# reads the celery config from settings.py with the CELERY_ prefix
app.config_from_object("django.conf:settings", namespace="CELERY")

# discover the tasks in the Django app
app.autodiscover_tasks()

app.conf.beat_schedule = {
    "atualizar-status-periodos-letivos-diariamente": {
        "task": "processos.tasks.atualizar_status_periodos_letivos",
        "schedule": crontab(hour=3, minute=5),
    },
    "lembrar-planejamento-presencial-diariamente": {
        "task": "processos.tasks.send_email_lembrete_planejamento_presencial",
        "schedule": crontab(hour=11, minute=0),
    },
}

# # ROTINA AUTÔNOMA DE TEMPO (CELERY BEAT) ===============================================
# app.conf.beat_schedule = {
#     'varrer-prazos-expirados-diariamente': {
#         'task': 'processos.tasks.verificar_prazos_expirados', 
#         'schedule': crontab(hour=0, minute=0),               # Executa de forma autônoma todo dia à meia-noite
        
#         #'schedule': crontab(minute='*/1'), #teste de 1 minuto
#     },
# }
