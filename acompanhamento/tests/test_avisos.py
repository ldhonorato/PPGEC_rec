"""Testes do aviso diário de alunos atrasados."""

from datetime import date, timedelta
from unittest.mock import patch

from django.core import mail
from django.db import IntegrityError
from django.test import TestCase

from acompanhamento.avisos import rodar_aviso_diario
from acompanhamento.models import AvisoIrregularidade
from acompanhamento.situacao import NAO_CALCULADO, Resultado
from processos.models import Aluno, Setor, SetorMembro, TrajetoriaAcademica, User

HOJE = date(2026, 10, 19)


def novo_usuario(nome):
    usuario = User(nome=nome, email=nome.lower().replace(" ", ".") + "@exemplo.invalid")
    usuario.set_unusable_password()
    usuario.save()
    return usuario


def membro(setor_nome, nome, saiu=False):
    usuario = novo_usuario(nome)
    setor = Setor.objects.get(nome=setor_nome)
    SetorMembro.objects.create(
        setor=setor,
        usuario=usuario,
        data_saida=HOJE - timedelta(days=1) if saiu else None,
    )
    return usuario


def aluno_de_mestrado(nome, dias_ate_o_limite):
    """Aluno com a data limite do projeto a tantos dias de HOJE
    (negativo = já passou)."""
    aluno = Aluno(nome=nome, email=nome.lower().replace(" ", ".") + "@exemplo.invalid")
    aluno.set_unusable_password()
    aluno.save()
    limite = HOJE + timedelta(days=dias_ate_o_limite)
    return TrajetoriaAcademica.objects.create(
        aluno=aluno,
        nivel_curso=Aluno.NivelCurso.MESTRADO,
        ingresso="2025.2",
        data_limite_qualificacao=limite,
        prazo_qualificacao=f"{limite.year}.{'1' if limite.month <= 6 else '2'}",
    )


def mudar_limite(trajetoria, dias_ate_o_limite):
    limite = HOJE + timedelta(days=dias_ate_o_limite)
    trajetoria.data_limite_qualificacao = limite
    trajetoria.prazo_qualificacao = f"{limite.year}.{'1' if limite.month <= 6 else '2'}"
    trajetoria.save()


class AvisoDiarioTests(TestCase):
    def setUp(self):
        # As migrations já criam dois alunos de exemplo; eles atrapalhariam
        # a contagem dos avisos.
        TrajetoriaAcademica.objects.all().delete()
        self.secretaria = membro(Setor.NOME_SECRETARIA, "Pessoa Secretaria")
        self.coordenacao = membro(Setor.NOME_COORDENACAO, "Pessoa Coordenacao")

    def avisos_abertos(self):
        return AvisoIrregularidade.objects.filter(encerrado_em__isnull=True)

    def test_aluno_atrasado_gera_aviso_e_um_email(self):
        aluno_de_mestrado("Aluno Atrasado", -30)
        resultado = rodar_aviso_diario(HOJE)

        self.assertEqual(resultado, {"avisos_novos": 1, "emails_enviados": 1})
        aviso = self.avisos_abertos().get()
        self.assertEqual(aviso.marco, AvisoIrregularidade.Marco.PROJETO_QUALIFICACAO)
        self.assertEqual(aviso.situacao, AvisoIrregularidade.Situacao.COM_CHANCE)
        self.assertIsNotNone(aviso.enviado_em)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("Aluno Atrasado", mail.outbox[0].alternatives[0][0])

    def test_rodar_de_novo_nao_repete_aviso_nem_email(self):
        aluno_de_mestrado("Aluno Atrasado", -30)
        rodar_aviso_diario(HOJE)
        resultado = rodar_aviso_diario(HOJE)

        self.assertEqual(resultado, {"avisos_novos": 0, "emails_enviados": 0})
        self.assertEqual(AvisoIrregularidade.objects.count(), 1)
        self.assertEqual(len(mail.outbox), 1)

    def test_sem_aluno_atrasado_nenhum_email(self):
        aluno_de_mestrado("Aluno em Dia", 30)
        resultado = rodar_aviso_diario(HOJE)

        self.assertEqual(resultado, {"avisos_novos": 0, "emails_enviados": 0})
        self.assertEqual(len(mail.outbox), 0)

    def test_email_so_traz_os_alunos_novos(self):
        aluno_de_mestrado("Primeiro Atrasado", -30)
        rodar_aviso_diario(HOJE)
        aluno_de_mestrado("Segundo Atrasado", -10)
        rodar_aviso_diario(HOJE)

        self.assertEqual(len(mail.outbox), 2)
        segundo_email = mail.outbox[1].alternatives[0][0]
        self.assertIn("Segundo Atrasado", segundo_email)
        self.assertNotIn("Primeiro Atrasado", segundo_email)

    def test_quem_sai_do_atraso_tem_o_aviso_encerrado(self):
        trajetoria = aluno_de_mestrado("Aluno Atrasado", -30)
        rodar_aviso_diario(HOJE)
        mudar_limite(trajetoria, 30)
        rodar_aviso_diario(HOJE)

        self.assertFalse(self.avisos_abertos().exists())
        self.assertIsNotNone(AvisoIrregularidade.objects.get().encerrado_em)

    def test_quem_volta_a_atrasar_gera_aviso_novo(self):
        trajetoria = aluno_de_mestrado("Aluno Atrasado", -30)
        rodar_aviso_diario(HOJE)
        mudar_limite(trajetoria, 30)
        rodar_aviso_diario(HOJE)
        mudar_limite(trajetoria, -30)
        resultado = rodar_aviso_diario(HOJE)

        self.assertEqual(resultado["avisos_novos"], 1)
        self.assertEqual(AvisoIrregularidade.objects.count(), 2)
        self.assertEqual(self.avisos_abertos().count(), 1)

    def test_trajetoria_trancada_tem_o_aviso_encerrado(self):
        trajetoria = aluno_de_mestrado("Aluno Atrasado", -30)
        rodar_aviso_diario(HOJE)
        trajetoria.status = TrajetoriaAcademica.Status.TRANCADA
        trajetoria.save()
        rodar_aviso_diario(HOJE)

        self.assertFalse(self.avisos_abertos().exists())

    def test_email_vai_so_para_os_membros_atuais_dos_dois_setores(self):
        membro(Setor.NOME_SECRETARIA, "Ja Saiu", saiu=True)
        novo_usuario("Fora dos Setores")
        aluno_de_mestrado("Aluno Atrasado", -30)
        rodar_aviso_diario(HOJE)

        self.assertEqual(
            sorted(mail.outbox[0].to),
            sorted([self.secretaria.email, self.coordenacao.email]),
        )

    def test_banco_nao_aceita_dois_avisos_abertos_iguais(self):
        trajetoria = aluno_de_mestrado("Aluno Atrasado", -30)
        dados = {
            "trajetoria": trajetoria,
            "marco": AvisoIrregularidade.Marco.PROJETO_QUALIFICACAO,
            "situacao": AvisoIrregularidade.Situacao.COM_CHANCE,
        }
        AvisoIrregularidade.objects.create(**dados)
        with self.assertRaises(IntegrityError):
            AvisoIrregularidade.objects.create(**dados)

    def test_situacao_nao_calculada_nao_encerra_o_aviso(self):
        aluno_de_mestrado("Aluno Atrasado", -30)
        rodar_aviso_diario(HOJE)

        def nao_calcula(trajetorias, data_referencia):
            falha = Resultado(NAO_CALCULADO)
            return {t.pk: {"marco": falha, "defesa": falha} for t in trajetorias}

        with patch("acompanhamento.avisos.calcular_situacoes", nao_calcula):
            rodar_aviso_diario(HOJE)
        resultado = rodar_aviso_diario(HOJE)

        self.assertEqual(self.avisos_abertos().count(), 1)
        self.assertEqual(AvisoIrregularidade.objects.count(), 1)
        self.assertEqual(resultado, {"avisos_novos": 0, "emails_enviados": 0})
        self.assertEqual(len(mail.outbox), 1)
