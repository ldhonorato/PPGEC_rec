from unittest.mock import patch

from django.core import mail
from django.test import TestCase, override_settings

from acompanhamento.tasks import enviar_avisos_irregularidade

SEM_AVISO = {"avisos_novos": 0, "emails_enviados": 0}


class ChaveDoAvisoTests(TestCase):
    def _nao_faz_nada(self):
        with patch("acompanhamento.avisos.rodar_aviso_diario") as rotina:
            with self.assertNumQueries(0):
                resultado = enviar_avisos_irregularidade()
        self.assertEqual(resultado, SEM_AVISO)
        rotina.assert_not_called()
        self.assertEqual(len(mail.outbox), 0)

    @override_settings(ACOMPANHAMENTO_ATIVO=True, ACOMPANHAMENTO_AVISO_ATIVO=False)
    def test_aviso_desligado_nao_consulta_o_banco(self):
        self._nao_faz_nada()

    @override_settings(ACOMPANHAMENTO_ATIVO=False, ACOMPANHAMENTO_AVISO_ATIVO=True)
    def test_modulo_desligado_nao_consulta_o_banco(self):
        self._nao_faz_nada()

    @override_settings(ACOMPANHAMENTO_ATIVO=False, ACOMPANHAMENTO_AVISO_ATIVO=False)
    def test_os_dois_desligados_nao_consultam_o_banco(self):
        self._nao_faz_nada()


class TarefaLigadaTests(TestCase):
    @override_settings(ACOMPANHAMENTO_ATIVO=True, ACOMPANHAMENTO_AVISO_ATIVO=True)
    def test_com_as_duas_chaves_ligadas_chama_a_rotina_uma_vez(self):
        da_rotina = {"avisos_novos": 2, "emails_enviados": 1}
        with patch(
            "acompanhamento.avisos.rodar_aviso_diario", return_value=da_rotina
        ) as rotina:
            resultado = enviar_avisos_irregularidade()
        rotina.assert_called_once_with()
        self.assertEqual(resultado, da_rotina)