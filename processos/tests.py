from django.test import TestCase
from django.urls import reverse
from django.core.exceptions import ValidationError

from .models import AlteracaoAluno, Aluno, Docente, Processo, Setor, User


class AlunosViewTests(TestCase):
    def setUp(self):
        self.servidor = User.objects.create_user(
            email="secretaria@example.com",
            password="senha-segura-123",
            nome="Servidor",
            tipo_usuario=User.TipoUsuario.SERVIDOR,
        )
        self.coordenador = Docente.objects.create(
            email="coordenador@example.com",
            password="senha-segura-123",
            nome="Coordenador",
            coordenador=True,
        )
        self.docente = Docente.objects.create(
            email="orientador@example.com",
            password="senha-segura-123",
            nome="Orientador",
        )
        self.aluno = Aluno.objects.create(
            email="aluno@example.com",
            password="senha-segura-123",
            nome="Aluno Teste",
            ingresso="2026.1",
            prazo_qualificacao="2026.2",
            prazo_defesa="2027.1",
            isQualificado=True,
            orientador=self.docente,
        )
        self.setor_requerente = Setor.objects.get(nome="Requerente")

    def test_servidor_acessa_lista_alunos(self):
        self.client.force_login(self.servidor)
        response = self.client.get(reverse("coordenacao_alunos"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.aluno.nome)

    def test_coordenador_acessa_lista_alunos(self):
        self.client.force_login(self.coordenador)
        response = self.client.get(reverse("coordenacao_alunos"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.aluno.nome)

    def test_filtros_por_nome_ingresso_e_status(self):
        aluno_inativo = Aluno.objects.create(
            email="inativo@example.com",
            password="senha-segura-123",
            nome="Outro Aluno",
            ingresso="2025.1",
            status_aluno=Aluno.StatusAluno.DESLIGADO,
        )

        self.client.force_login(self.servidor)
        response = self.client.get(
            reverse("coordenacao_alunos"),
            {
                "nome": "Aluno Teste",
                "ingresso_inicio": "2026.1",
                "ingresso_fim": "2026.2",
                "status": "ATIVO",
            },
        )
        self.assertEqual(response.status_code, 200)
        alunos = list(response.context["alunos"])
        self.assertEqual(len(alunos), 1)
        self.assertEqual(alunos[0].id, self.aluno.id)

    def test_docente_nao_coordenador_nao_tem_acesso(self):
        self.client.force_login(self.docente)
        response = self.client.get(reverse("coordenacao_alunos"))
        self.assertEqual(response.status_code, 403)

    def test_aluno_detalhe_exibe_processos(self):
        processo = Processo.objects.create(
            usuario_criado_por=self.aluno,
            tipo=Processo.TipoProcesso.QUALIFICACAO,
            assunto="Exame de qualificacao",
            descricao="Solicitacao de banca",
            setor_atual=self.setor_requerente,
        )

        self.client.force_login(self.servidor)
        response = self.client.get(reverse("aluno_detalhe", args=[self.aluno.id]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.aluno.nome)
        self.assertContains(response, processo.assunto)

    def test_semestre_invalido_gera_erro(self):
        with self.assertRaises(ValidationError):
            Aluno.objects.create(
                email="invalido@example.com",
                password="senha-segura-123",
                nome="Aluno Invalido",
                ingresso="2026-1",
            )

    def test_alterar_status_exige_comentario_e_cria_historico(self):
        self.client.force_login(self.servidor)
        url = reverse("aluno_detalhe", args=[self.aluno.id])

        response_sem_comentario = self.client.post(
            url,
            {
                "acao": "alterar_status",
                "status_aluno": Aluno.StatusAluno.DESLIGADO,
                "comentario": "",
            },
        )
        self.assertEqual(response_sem_comentario.status_code, 200)
        self.aluno.refresh_from_db()
        self.assertEqual(self.aluno.status_aluno, Aluno.StatusAluno.ATIVO)

        response_ok = self.client.post(
            url,
            {
                "acao": "alterar_status",
                "status_aluno": Aluno.StatusAluno.DESLIGADO,
                "comentario": "Desligamento por solicitacao formal.",
            },
        )
        self.assertEqual(response_ok.status_code, 302)
        self.aluno.refresh_from_db()
        self.assertEqual(self.aluno.status_aluno, Aluno.StatusAluno.DESLIGADO)
        self.assertTrue(
            AlteracaoAluno.objects.filter(
                aluno=self.aluno,
                tipo=AlteracaoAluno.TipoAlteracao.STATUS,
            ).exists()
        )

    def test_registrar_defesa_define_status_e_campos(self):
        self.client.force_login(self.servidor)
        url = reverse("aluno_detalhe", args=[self.aluno.id])
        response = self.client.post(
            url,
            {
                "acao": "registrar_defesa",
                "numero_defesa": "ATA-2026-33",
                "data_defesa": "2026-12-20",
                "comentario": "Defesa homologada.",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.aluno.refresh_from_db()
        self.assertEqual(self.aluno.status_aluno, Aluno.StatusAluno.DEFENDEU)
        self.assertEqual(self.aluno.numero_defesa, "ATA-2026-33")
        self.assertEqual(str(self.aluno.data_defesa), "2026-12-20")
