from datetime import date
from django.test import TestCase

from acompanhamento.previsao_conclusao import calcular_previsao_conclusao
from processos.models import (
    Aluno,
    Docente,
    ProrrogacaoTrajetoria,
    TrajetoriaAcademica,
    TrancamentoTrajetoria,
    User,
)


class PrevisaoConclusaoTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.servidor = User.objects.create_user(
            email="servidor.previsao@teste.invalid",
            nome="Servidor Teste",
            tipo_usuario=User.TipoUsuario.SERVIDOR,
        )
        cls.orientador_1 = Docente.objects.create_user(
            nome="Prof. Carlos Silva",
            email="carlos.silva@teste.invalid",
            tipo_usuario=User.TipoUsuario.DOCENTE,
        )
        cls.orientador_2 = Docente.objects.create_user(
            nome="Prof. Maria Souza",
            email="maria.souza@teste.invalid",
            tipo_usuario=User.TipoUsuario.DOCENTE,
        )

    def test_ingresso_fevereiro_2025_com_prorrogacao_da_maio_2027(self):
        """Mestrado com ingresso em 01/02/2025 (24 meses = fim de fev/2027) + 3 meses = 31/05/2027."""
        aluno = Aluno.objects.create_user(email="aluno_prorr@teste.invalid", nome="Aluno Prorrogação")
        traj = TrajetoriaAcademica.objects.create(
            aluno=aluno,
            orientador=self.orientador_1,
            nivel_curso=Aluno.NivelCurso.MESTRADO,
            status=TrajetoriaAcademica.Status.ATIVA,
            ingresso="2025.1",
            data_ingresso=date(2025, 2, 1),
        )
        ProrrogacaoTrajetoria.objects.create(
            trajetoria=traj,
            meses=3,
            registrado_por=self.servidor,
        )

        res = calcular_previsao_conclusao(
            self.orientador_1.pk,
            Aluno.NivelCurso.MESTRADO,
            data_referencia=date(2026, 11, 1),
        )

        self.assertEqual(len(res["alunos"]), 1)
        aluno_res = res["alunos"][0]
        self.assertEqual(aluno_res["data_limite"], date(2027, 5, 31))
        self.assertEqual(aluno_res["semestre"], "2027.1")
        self.assertEqual(res["por_semestre"], [{"semestre": "2027.1", "alunos": 1}])
        self.assertEqual(res["prazo_vencido"], 0)
        self.assertEqual(res["sem_previsao"], 0)

    def test_trancamento_soma_dias_no_prazo(self):
        """Trancamento estende o prazo limite de defesa pela quantidade de dias trancados."""
        aluno = Aluno.objects.create_user(email="aluno_tranc@teste.invalid", nome="Aluno Trancado")
        traj = TrajetoriaAcademica.objects.create(
            aluno=aluno,
            orientador=self.orientador_1,
            nivel_curso=Aluno.NivelCurso.MESTRADO,
            status=TrajetoriaAcademica.Status.ATIVA,
            ingresso="2025.1",
            data_ingresso=date(2025, 3, 1),
        )
        TrancamentoTrajetoria.objects.create(
            trajetoria=traj,
            data_inicio=date(2025, 6, 1),
            data_fim=date(2025, 8, 30),
            registrado_por=self.servidor,
        )

        res = calcular_previsao_conclusao(
            self.orientador_1.pk,
            Aluno.NivelCurso.MESTRADO,
            data_referencia=date(2026, 11, 1),
        )
        self.assertEqual(len(res["alunos"]), 1)
        self.assertEqual(res["alunos"][0]["data_limite"], date(2027, 6, 30))

    def test_prazo_vencido_e_sem_data_ingresso(self):
        """Prazo anterior à data de referência entra em prazo_vencido; sem data de ingresso entra em sem_previsao."""
        data_ref = date(2026, 11, 1)

        # 1. Aluno com prazo vencido
        a_vencido = Aluno.objects.create_user(email="vencido@teste.invalid", nome="Aluno Vencido")
        TrajetoriaAcademica.objects.create(
            aluno=a_vencido,
            orientador=self.orientador_1,
            nivel_curso=Aluno.NivelCurso.MESTRADO,
            status=TrajetoriaAcademica.Status.ATIVA,
            ingresso="2024.1",
            data_ingresso=date(2024, 3, 1),
        )

        # 2. Aluno sem data de ingresso
        a_sem_data = Aluno.objects.create_user(email="semdata@teste.invalid", nome="Aluno Sem Data")
        TrajetoriaAcademica.objects.create(
            aluno=a_sem_data,
            orientador=self.orientador_1,
            nivel_curso=Aluno.NivelCurso.MESTRADO,
            status=TrajetoriaAcademica.Status.ATIVA,
            ingresso="2026.2",
            data_ingresso=None,
        )

        res = calcular_previsao_conclusao(
            self.orientador_1.pk,
            Aluno.NivelCurso.MESTRADO,
            data_referencia=data_ref,
        )
        self.assertEqual(res["prazo_vencido"], 1)
        self.assertEqual(res["sem_previsao"], 1)

        aluno_vencido = next(a for a in res["alunos"] if a["nome"] == "Aluno Vencido")
        self.assertEqual(aluno_vencido["semestre"], "")
        self.assertIsNotNone(aluno_vencido["data_limite"])

        aluno_sem_data = next(a for a in res["alunos"] if a["nome"] == "Aluno Sem Data")
        self.assertEqual(aluno_sem_data["semestre"], "")
        self.assertIsNone(aluno_sem_data["data_limite"])

    def test_aluno_de_outro_orientador_e_aluno_sem_orientador(self):
        """Garante que alunos de outro professor não entram, e alunos sem orientador entram com orientador_id=None."""
        data_ref = date(2026, 11, 1)

        a1 = Aluno.objects.create_user(email="a1@teste.invalid", nome="Aluno P1")
        TrajetoriaAcademica.objects.create(
            aluno=a1,
            orientador=self.orientador_1,
            nivel_curso=Aluno.NivelCurso.DOUTORADO,
            status=TrajetoriaAcademica.Status.ATIVA,
            ingresso="2025.1",
            data_ingresso=date(2025, 3, 1),
        )

        a2 = Aluno.objects.create_user(email="a2@teste.invalid", nome="Aluno P2")
        TrajetoriaAcademica.objects.create(
            aluno=a2,
            orientador=self.orientador_2,
            nivel_curso=Aluno.NivelCurso.DOUTORADO,
            status=TrajetoriaAcademica.Status.ATIVA,
            ingresso="2025.1",
            data_ingresso=date(2025, 3, 1),
        )

        a_sem_orientador = Aluno.objects.create_user(email="sem_orientador@teste.invalid", nome="Sem Orientador")
        TrajetoriaAcademica.objects.create(
            aluno=a_sem_orientador,
            orientador=None,
            nivel_curso=Aluno.NivelCurso.DOUTORADO,
            status=TrajetoriaAcademica.Status.ATIVA,
            ingresso="2025.1",
            data_ingresso=date(2025, 3, 1),
        )

        res_p1 = calcular_previsao_conclusao(
            self.orientador_1.pk,
            Aluno.NivelCurso.DOUTORADO,
            data_referencia=data_ref,
        )
        nomes_p1 = [a["nome"] for a in res_p1["alunos"]]
        self.assertIn("Aluno P1", nomes_p1)
        self.assertNotIn("Aluno P2", nomes_p1)
        self.assertNotIn("Sem Orientador", nomes_p1)

        res_sem = calcular_previsao_conclusao(
            None,
            Aluno.NivelCurso.DOUTORADO,
            data_referencia=data_ref,
        )
        nomes_sem = [a["nome"] for a in res_sem["alunos"]]
        self.assertIn("Sem Orientador", nomes_sem)
        self.assertNotIn("Aluno P1", nomes_sem)
