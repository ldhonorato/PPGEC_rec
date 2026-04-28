from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.core.exceptions import ValidationError
from django.utils import timezone

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


class FrontendIdentityTests(TestCase):
    def setUp(self):
        self.docente = Docente.objects.create(
            email="docente.frontend@example.com",
            password="senha-segura-123",
            nome="Leandro Silva",
        )
        self.aluno = Aluno.objects.create(
            email="aluno.frontend@example.com",
            password="senha-segura-123",
            nome="Aluno Frontend",
            ingresso="2026.1",
            orientador=self.docente,
        )

    def test_login_renderiza_identidade_acadflow(self):
        response = self.client.get(reverse("login"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "AcadFlow")
        self.assertContains(response, "css/app.css")
        self.assertContains(response, "img/acadflow-logo.png")
        self.assertContains(response, 'rel="icon"')
        self.assertContains(response, 'class="card login-card"')

    def test_home_renderiza_shell_e_dashboard_acadflow(self):
        self.client.force_login(self.docente)
        response = self.client.get(reverse("home"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Bem-vindo ao")
        self.assertContains(response, "Acad<span>Flow</span>", html=True)
        self.assertContains(response, 'class="sidebar"')
        self.assertContains(response, 'class="metric-grid"')
        self.assertContains(response, 'class="overdue-link"')
        self.assertContains(response, 'class="user-menu"')
        self.assertContains(response, "Meus Processos")
        self.assertContains(response, "Processos no Pleno")
        self.assertNotContains(response, 'class="nav"')
        self.assertContains(response, "Perfil")
        self.assertContains(response, "Sair")

    def test_home_aluno_mantem_acesso_rapido_para_novo_processo(self):
        self.client.force_login(self.aluno)
        response = self.client.get(reverse("home"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Novo requerimento")
        self.assertContains(response, "Consultar processos")
        self.assertContains(response, "Programa de Pos-Graduacao")


class ProcessoPrazoTests(TestCase):
    def setUp(self):
        self.servidor = User.objects.create_user(
            email="servidor.prazo@example.com",
            password="senha-segura-123",
            nome="Servidor Prazo",
            tipo_usuario=User.TipoUsuario.SERVIDOR,
        )
        self.aluno = Aluno.objects.create(
            email="aluno.prazo@example.com",
            password="senha-segura-123",
            nome="Aluno Prazo",
            ingresso="2026.1",
        )
        self.setor_requerente = Setor.objects.get(nome="Requerente")

    def test_processo_recebe_prazo_default_por_tipo(self):
        processo = Processo.objects.create(
            usuario_criado_por=self.aluno,
            tipo=Processo.TipoProcesso.TRANCAMENTO_MATRICULA,
            assunto="Trancamento",
            descricao="Solicitacao",
            setor_atual=self.setor_requerente,
        )

        self.assertEqual(
            processo.prazo_limite,
            timezone.localdate() + timedelta(days=15),
        )
        self.assertFalse(processo.esta_atrasado)

    def test_topbar_conta_e_lista_processos_atrasados(self):
        atrasado = Processo.objects.create(
            usuario_criado_por=self.aluno,
            tipo=Processo.TipoProcesso.OUTRO,
            assunto="Processo atrasado",
            descricao="Solicitacao",
            setor_atual=self.setor_requerente,
        )
        Processo.objects.filter(pk=atrasado.pk).update(
            prazo_limite=timezone.localdate() - timedelta(days=1)
        )
        finalizado = Processo.objects.create(
            usuario_criado_por=self.aluno,
            tipo=Processo.TipoProcesso.OUTRO,
            assunto="Processo finalizado atrasado",
            descricao="Solicitacao",
            setor_atual=self.setor_requerente,
            status=Processo.StatusProcesso.FINALIZADO,
            prazo_limite=timezone.localdate() - timedelta(days=5),
        )

        self.client.force_login(self.servidor)
        home = self.client.get(reverse("home"))
        self.assertContains(home, "1")
        self.assertContains(home, "processos atrasados")

        response = self.client.get(reverse("coordenacao_processos"), {"atrasados": "1"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, atrasado.assunto)
        self.assertNotContains(response, finalizado.assunto)
        self.assertContains(response, "Atrasado")
