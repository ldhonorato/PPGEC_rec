from celery.schedules import crontab
from django.test import SimpleTestCase

from acompanhamento.tasks import enviar_avisos_irregularidade
from ppgec.celery import app


class AgendamentoDoAvisoTests(SimpleTestCase):
    def test_entrada_do_beat_aponta_para_a_tarefa_real(self):
        entrada = app.conf.beat_schedule["acompanhamento-aviso-diario"]
        self.assertEqual(entrada["task"], enviar_avisos_irregularidade.name)

    def test_roda_as_7h(self):
        entrada = app.conf.beat_schedule["acompanhamento-aviso-diario"]
        self.assertEqual(entrada["schedule"], crontab(hour=7, minute=0))