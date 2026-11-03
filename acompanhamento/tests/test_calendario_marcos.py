from datetime import date

from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import RequestFactory, TestCase

from acompanhamento.admin import MarcoCalendarioAdmin
from acompanhamento.models import MarcoCalendario

PROJETO = MarcoCalendario.TipoMarco.PROJETO_DISSERTACAO
QUALIFICACAO = MarcoCalendario.TipoMarco.QUALIFICACAO


class MarcoCalendarioTests(TestCase):
    def test_data_limite_do_retorna_a_data_cadastrada(self):
        MarcoCalendario.objects.create(
            semestre="2028.1", tipo=QUALIFICACAO, data_limite=date(2028, 6, 7)
        )
        self.assertEqual(
            MarcoCalendario.data_limite_do("2028.1", QUALIFICACAO), date(2028, 6, 7)
        )

    def test_admin_grava_usuario_em_alterado_por(self):
        usuario = get_user_model().objects.create_user("secretaria", password="x")
        marco = MarcoCalendario(
            semestre="2026.2",
            tipo=PROJETO,
            data_limite=date(2026, 9, 21),
        )
        request = RequestFactory().post("/")
        request.user = usuario

        MarcoCalendarioAdmin(MarcoCalendario, AdminSite()).save_model(
            request, marco, form=None, change=False
        )

        marco.refresh_from_db()
        self.assertEqual(marco.alterado_por, usuario)

    def test_data_limite_do_retorna_none_sem_cadastro(self):
        self.assertIsNone(MarcoCalendario.data_limite_do("2026.2", PROJETO))

    def test_data_limite_do_separa_os_tipos(self):
        MarcoCalendario.objects.create(
            semestre="2026.2", tipo=PROJETO, data_limite=date(2026, 9, 21)
        )
        self.assertIsNone(MarcoCalendario.data_limite_do("2026.2", QUALIFICACAO))

    def test_semestre_e_tipo_sao_unicos(self):
        MarcoCalendario.objects.create(
            semestre="2026.2", tipo=PROJETO, data_limite=date(2026, 9, 21)
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            MarcoCalendario.objects.create(
                semestre="2026.2", tipo=PROJETO, data_limite=date(2026, 9, 22)
            )

    def test_semestre_fora_do_formato_e_recusado(self):
        marco = MarcoCalendario(semestre="2026-2", tipo=PROJETO, data_limite=date(2026, 9, 21))
        with self.assertRaises(ValidationError) as erro:
            marco.full_clean()
        self.assertIn("semestre", erro.exception.message_dict)

    def test_data_fora_do_semestre_e_recusada(self):
        marco = MarcoCalendario(semestre="2026.1", tipo=PROJETO, data_limite=date(2026, 9, 21))
        with self.assertRaises(ValidationError) as erro:
            marco.full_clean()
        self.assertIn("data_limite", erro.exception.message_dict)

    def test_data_dentro_do_semestre_e_aceita(self):
        MarcoCalendario(
            semestre="2026.2", tipo=PROJETO, data_limite=date(2026, 9, 21)
        ).full_clean()

    def test_semestre_da_data_nos_limites(self):
        self.assertEqual(MarcoCalendario.semestre_da_data(date(2026, 6, 30)), "2026.1")
        self.assertEqual(MarcoCalendario.semestre_da_data(date(2026, 7, 1)), "2026.2")