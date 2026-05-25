from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.core.exceptions import ValidationError
from django.utils import timezone

from .models import AlteracaoAluno, Aluno, Docente, ManifestacaoProcesso, Processo, Setor, TrajetoriaAcademica, User


def criar_trajetoria(aluno, **kwargs):
    defaults = {
        "nivel_curso": Aluno.NivelCurso.MESTRADO,
        "status": TrajetoriaAcademica.Status.ATIVA,
        "ingresso": "2026.1",
        "prazo_qualificacao": "2026.2",
        "prazo_defesa": "2027.1",
    }
    defaults.update(kwargs)
    return TrajetoriaAcademica.objects.create(aluno=aluno, **defaults)


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
        self.coorientador = Docente.objects.create(
            email="coorientador@example.com",
            password="senha-segura-123",
            nome="Coorientador",
        )
        self.aluno = Aluno.objects.create(
            email="aluno@example.com",
            password="senha-segura-123",
            nome="Aluno Teste",
        )
        criar_trajetoria(
            self.aluno,
            isQualificado=True,
            orientador=self.docente,
            coorientador=self.coorientador,
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
            status_aluno=Aluno.StatusAluno.DESLIGADO,
        )
        criar_trajetoria(aluno_inativo, ingresso="2025.1")

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
            tipo=Processo.TipoProcesso.QUALIFICACAO_DOUTORADO,
            assunto="Exame de qualificacao",
            descricao="Solicitacao de banca",
            setor_atual=self.setor_requerente,
        )

        self.client.force_login(self.servidor)
        response = self.client.get(reverse("aluno_detalhe", args=[self.aluno.id]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.aluno.nome)
        self.assertContains(response, processo.assunto)
        self.assertContains(response, "Mestrado")
        self.assertContains(response, "Projeto de")

    def test_aluno_doutorado_exibe_qualificacao(self):
        aluno_doutorado = Aluno.objects.create(
            email="aluno.doutorado@example.com",
            password="senha-segura-123",
            nome="Aluno Doutorado",
        )
        criar_trajetoria(aluno_doutorado, nivel_curso=Aluno.NivelCurso.DOUTORADO, orientador=self.docente)

        self.client.force_login(self.servidor)
        response = self.client.get(reverse("aluno_detalhe", args=[aluno_doutorado.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Doutorado")
        self.assertContains(response, "Qualifica")
        self.assertNotContains(response, "Projeto de")

    def test_lista_alunos_filtra_por_nivel(self):
        aluno_doutorado_filtro = Aluno.objects.create(
            email="aluno.doutorado.filtro@example.com",
            password="senha-segura-123",
            nome="Aluno Doutorado Filtro",
        )
        criar_trajetoria(aluno_doutorado_filtro, nivel_curso=Aluno.NivelCurso.DOUTORADO)

        self.client.force_login(self.servidor)
        response = self.client.get(reverse("coordenacao_alunos"), {"nivel": Aluno.NivelCurso.DOUTORADO})

        self.assertEqual(response.status_code, 200)
        alunos = list(response.context["alunos"])
        self.assertEqual(len(alunos), 1)
        self.assertEqual(alunos[0].trajetoria_atual.nivel_curso, Aluno.NivelCurso.DOUTORADO)

    def test_lista_alunos_usa_ultima_conclusao_sem_trajetoria_ativa(self):
        aluno_concluido = Aluno.objects.create(
            email="aluno.concluido@example.com",
            password="senha-segura-123",
            nome="Aluno Concluido",
            matricula="2025A0002",
            status_aluno=Aluno.StatusAluno.DEFENDEU,
        )
        criar_trajetoria(
            aluno_concluido,
            nivel_curso=Aluno.NivelCurso.DOUTORADO,
            status=TrajetoriaAcademica.Status.CONCLUIDA,
            ingresso="2025.1",
            orientador=self.docente,
            numero_defesa="ATA-2026-01",
            data_defesa=timezone.localdate(),
        )

        self.client.force_login(self.servidor)
        response = self.client.get(
            reverse("coordenacao_alunos"),
            {
                "nivel": Aluno.NivelCurso.DOUTORADO,
                "ingresso_inicio": "2025.1",
                "ingresso_fim": "2025.1",
                "status": Aluno.StatusAluno.DEFENDEU,
            },
        )

        self.assertEqual(response.status_code, 200)
        alunos = list(response.context["alunos"])
        self.assertEqual(len(alunos), 1)
        self.assertEqual(alunos[0].id, aluno_concluido.id)
        self.assertEqual(alunos[0].trajetoria_atual.status, TrajetoriaAcademica.Status.CONCLUIDA)
        self.assertContains(response, "Matricula: 2025A0002")
        self.assertContains(response, "Nivel: Doutorado")
        self.assertContains(response, "Ingresso: 2025.1")
        self.assertContains(response, "Orientador: Orientador")
        self.assertNotContains(response, "Status: Concluido")
        self.assertNotContains(response, "Prazo defesa")
        self.assertNotContains(response, "Qualifica")
        self.assertNotContains(response, "Coorientador:")

    def test_dashboard_exibe_apenas_trajetorias_ativas(self):
        aluno_concluido = Aluno.objects.create(
            email="aluno.dashboard.concluido@example.com",
            password="senha-segura-123",
            nome="Aluno Dashboard Concluido",
        )
        criar_trajetoria(
            aluno_concluido,
            status=TrajetoriaAcademica.Status.CONCLUIDA,
            orientador=self.docente,
            numero_defesa="ATA-2026-02",
            data_defesa=timezone.localdate(),
        )

        self.client.force_login(self.servidor)
        response = self.client.get(reverse("coordenacao_dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.aluno.nome)
        self.assertNotContains(response, aluno_concluido.nome)
        self.assertNotContains(response, aluno_concluido.email)

    def test_meus_orientandos_separa_vinculos_por_status_e_papel(self):
        aluno_coorientado = Aluno.objects.create(
            email="aluno.coorientado@example.com",
            password="senha-segura-123",
            nome="Aluno Coorientado",
        )
        criar_trajetoria(
            aluno_coorientado,
            nivel_curso=Aluno.NivelCurso.DOUTORADO,
            orientador=self.coordenador,
            coorientador=self.docente,
        )
        aluno_concluido = Aluno.objects.create(
            email="aluno.vinculo.concluido@example.com",
            password="senha-segura-123",
            nome="Aluno Vinculo Concluido",
        )
        criar_trajetoria(
            aluno_concluido,
            status=TrajetoriaAcademica.Status.CONCLUIDA,
            orientador=self.docente,
            numero_defesa="ATA-2026-03",
            data_defesa=timezone.localdate(),
        )

        self.client.force_login(self.docente)
        response = self.client.get(reverse("menu_meus_orientandos"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Orientacoes ativas")
        self.assertContains(response, "Coorientacoes")
        self.assertContains(response, "Orientacoes/coorientacoes concluidas")
        self.assertContains(response, self.aluno.nome)
        self.assertContains(response, aluno_coorientado.nome)
        self.assertContains(response, aluno_concluido.nome)
        self.assertContains(response, "Coorientador")

    def test_coorientador_cadastrado_acessa_processo_do_aluno(self):
        processo = Processo.objects.create(
            usuario_criado_por=self.aluno,
            tipo=Processo.TipoProcesso.OUTRO,
            assunto="Solicitacao com coorientador",
            descricao="Acompanhamento do coorientador",
            setor_atual=self.setor_requerente,
        )

        self.client.force_login(self.coorientador)
        response = self.client.get(reverse("processo_detalhe", args=[processo.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, processo.assunto)

    def test_aluno_detalhe_exibe_coorientador_externo(self):
        aluno_externo = Aluno.objects.create(
            email="aluno.externo@example.com",
            password="senha-segura-123",
            nome="Aluno com Coorientador Externo",
        )
        criar_trajetoria(
            aluno_externo,
            orientador=self.docente,
            coorientador_externo_nome="Profa. Externa",
            coorientador_externo_email="externa@example.com",
            coorientador_externo_instituicao="Universidade Externa",
        )

        self.client.force_login(self.servidor)
        response = self.client.get(reverse("aluno_detalhe", args=[aluno_externo.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Profa. Externa")
        self.assertContains(response, "Universidade Externa")

    def test_trocar_orientador_registra_historico(self):
        novo_orientador = Docente.objects.create(
            email="novo.orientador@example.com",
            password="senha-segura-123",
            nome="Novo Orientador",
        )
        trajetoria = self.aluno.trajetorias.get(status=TrajetoriaAcademica.Status.ATIVA)

        self.client.force_login(self.servidor)
        response = self.client.post(
            reverse("aluno_detalhe", args=[self.aluno.id]),
            {
                "acao": "editar_trajetoria",
                "trajetoria_id": trajetoria.id,
                "nivel_curso": trajetoria.nivel_curso,
                "status": trajetoria.status,
                "ingresso": trajetoria.ingresso,
                "prazo_qualificacao": trajetoria.prazo_qualificacao,
                "prazo_defesa": trajetoria.prazo_defesa,
                "orientador": novo_orientador.id,
                "tipo_coorientador": "CADASTRADO",
                "coorientador": self.coorientador.id,
                "comentario": "Troca aprovada pela coordenacao.",
            },
        )

        self.assertEqual(response.status_code, 302)
        trajetoria.refresh_from_db()
        self.assertEqual(trajetoria.orientador_id, novo_orientador.id)
        alteracao = AlteracaoAluno.objects.filter(
            aluno=self.aluno,
            tipo=AlteracaoAluno.TipoAlteracao.TRAJETORIA,
        ).latest("criado_em")
        self.assertIn("Orientador", alteracao.valor_anterior)
        self.assertIn("Novo Orientador", alteracao.valor_novo)

    def test_alterar_coorientador_externo_registra_historico(self):
        trajetoria = self.aluno.trajetorias.get(status=TrajetoriaAcademica.Status.ATIVA)
        self.client.force_login(self.servidor)
        response = self.client.post(
            reverse("aluno_detalhe", args=[self.aluno.id]),
            {
                "acao": "editar_trajetoria",
                "trajetoria_id": trajetoria.id,
                "nivel_curso": trajetoria.nivel_curso,
                "status": trajetoria.status,
                "ingresso": trajetoria.ingresso,
                "prazo_qualificacao": trajetoria.prazo_qualificacao,
                "prazo_defesa": trajetoria.prazo_defesa,
                "orientador": self.docente.id,
                "tipo_coorientador": "EXTERNO",
                "coorientador_externo_nome": "Prof. Visitante",
                "coorientador_externo_email": "visitante@example.com",
                "coorientador_externo_instituicao": "Instituto Visitante",
                "comentario": "Coorientacao externa aprovada.",
            },
        )

        self.assertEqual(response.status_code, 302)
        trajetoria.refresh_from_db()
        self.assertIsNone(trajetoria.coorientador)
        self.assertEqual(trajetoria.coorientador_externo_nome, "Prof. Visitante")
        alteracao = AlteracaoAluno.objects.filter(
            aluno=self.aluno,
            tipo=AlteracaoAluno.TipoAlteracao.TRAJETORIA,
        ).latest("criado_em")
        self.assertIn("Coorientador", alteracao.valor_anterior)
        self.assertIn("Prof. Visitante", alteracao.valor_novo)

    def test_registrar_reingresso_redefine_prazos_e_registra_historico(self):
        trajetoria = self.aluno.trajetorias.get(status=TrajetoriaAcademica.Status.ATIVA)
        self.client.force_login(self.servidor)
        response = self.client.post(
            reverse("aluno_detalhe", args=[self.aluno.id]),
            {
                "acao": "editar_trajetoria",
                "trajetoria_id": trajetoria.id,
                "nivel_curso": trajetoria.nivel_curso,
                "status": trajetoria.status,
                "ingresso": "2027.1",
                "prazo_qualificacao": "2027.2",
                "prazo_defesa": "2028.1",
                "reingressante": "on",
                "isQualificado": "on",
                "orientador": self.docente.id,
                "tipo_coorientador": "CADASTRADO",
                "coorientador": self.coorientador.id,
                "comentario": "Reingresso aprovado pelo colegiado.",
            },
        )

        self.assertEqual(response.status_code, 302)
        trajetoria.refresh_from_db()
        self.assertTrue(trajetoria.reingressante)
        self.assertEqual(trajetoria.ingresso, "2027.1")
        alteracao = AlteracaoAluno.objects.filter(
            aluno=self.aluno,
            tipo=AlteracaoAluno.TipoAlteracao.TRAJETORIA,
        ).latest("criado_em")
        self.assertIn("reingressante=Nao", alteracao.valor_anterior)
        self.assertIn("reingressante=Sim", alteracao.valor_novo)

    def test_iniciar_doutorado_conclui_mestrado_e_cria_nova_trajetoria(self):
        novo_orientador = Docente.objects.create(
            email="orientador.doutorado@example.com",
            password="senha-segura-123",
            nome="Orientador Doutorado",
        )

        self.client.force_login(self.servidor)
        response = self.client.post(
            reverse("aluno_detalhe", args=[self.aluno.id]),
            {
                "acao": "iniciar_doutorado",
                "ingresso": "2028.1",
                "prazo_qualificacao": "2029.1",
                "prazo_defesa": "2031.1",
                "orientador": novo_orientador.id,
                "comentario": "Aluno concluiu mestrado e iniciou doutorado.",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.aluno.refresh_from_db()

        self.assertEqual(
            self.aluno.trajetorias.filter(status=TrajetoriaAcademica.Status.CONCLUIDA).count(),
            1,
        )
        doutorado = self.aluno.trajetorias.get(status=TrajetoriaAcademica.Status.ATIVA)
        self.assertEqual(doutorado.nivel_curso, Aluno.NivelCurso.DOUTORADO)
        self.assertEqual(doutorado.ingresso, "2028.1")
        self.assertEqual(doutorado.prazo_qualificacao, "2029.1")
        self.assertEqual(doutorado.prazo_defesa, "2031.1")
        self.assertEqual(doutorado.orientador_id, novo_orientador.id)
        self.assertFalse(doutorado.isQualificado)
        self.assertTrue(
            AlteracaoAluno.objects.filter(
                aluno=self.aluno,
                tipo=AlteracaoAluno.TipoAlteracao.TRAJETORIA,
            ).exists()
        )

    def test_semestre_invalido_gera_erro(self):
        aluno_invalido = Aluno.objects.create(
            email="invalido@example.com",
            password="senha-segura-123",
            nome="Aluno Invalido",
        )
        with self.assertRaises(ValidationError):
            TrajetoriaAcademica.objects.create(
                aluno=aluno_invalido,
                ingresso="2026-1",
                nivel_curso=Aluno.NivelCurso.MESTRADO,
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
        trajetoria = self.aluno.trajetorias.get(status=TrajetoriaAcademica.Status.ATIVA)
        self.client.force_login(self.servidor)
        url = reverse("aluno_detalhe", args=[self.aluno.id])
        response = self.client.post(
            url,
            {
                "acao": "editar_trajetoria",
                "trajetoria_id": trajetoria.id,
                "nivel_curso": trajetoria.nivel_curso,
                "status": TrajetoriaAcademica.Status.CONCLUIDA,
                "ingresso": trajetoria.ingresso,
                "prazo_qualificacao": trajetoria.prazo_qualificacao,
                "prazo_defesa": trajetoria.prazo_defesa,
                "orientador": self.docente.id,
                "tipo_coorientador": "CADASTRADO",
                "coorientador": self.coorientador.id,
                "isQualificado": "on",
                "numero_defesa": "ATA-2026-33",
                "data_defesa": "2026-12-20",
                "comentario": "Defesa homologada.",
            },
        )
        self.assertEqual(response.status_code, 302)
        trajetoria.refresh_from_db()
        self.assertEqual(trajetoria.status, TrajetoriaAcademica.Status.CONCLUIDA)
        self.assertEqual(trajetoria.numero_defesa, "ATA-2026-33")
        self.assertEqual(str(trajetoria.data_defesa), "2026-12-20")


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
        )
        criar_trajetoria(self.aluno, orientador=self.docente)

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

    def test_dashboard_coordenador_mantem_menu_lateral_da_home(self):
        coordenador = Docente.objects.create(
            email="coordenador.frontend@example.com",
            password="senha-segura-123",
            nome="Coordenador Frontend",
            coordenador=True,
        )

        self.client.force_login(coordenador)
        response = self.client.get(reverse("coordenacao_dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Dashboard")
        self.assertContains(response, "Alunos")
        self.assertContains(response, "Processos")
        self.assertContains(response, "Caixa de processos")
        self.assertContains(response, "Meus Processos")
        self.assertContains(response, "Processos no Pleno")
        self.assertContains(response, "Processos dos orientandos")
        self.assertContains(response, "Ciencias")
        self.assertNotContains(response, "Ciencias manifestadas")
        self.assertContains(response, "Meus Orientandos")

    def test_menu_ciencias_exibe_pendencias_e_manifestadas(self):
        servidor = User.objects.create_user(
            email="servidor.ciencias@example.com",
            password="senha-segura-123",
            nome="Servidor Ciencias",
            tipo_usuario=User.TipoUsuario.SERVIDOR,
        )
        processo_pendente = Processo.objects.create(
            usuario_criado_por=self.aluno,
            tipo=Processo.TipoProcesso.OUTRO,
            assunto="Processo com ciencia pendente",
            descricao="Solicitacao",
            setor_atual=Setor.objects.get(nome="Requerente"),
        )
        processo_manifestado = Processo.objects.create(
            usuario_criado_por=self.aluno,
            tipo=Processo.TipoProcesso.OUTRO,
            assunto="Processo com ciencia manifestada",
            descricao="Solicitacao",
            setor_atual=Setor.objects.get(nome="Requerente"),
        )
        ManifestacaoProcesso.objects.create(
            processo=processo_pendente,
            tipo=ManifestacaoProcesso.TipoManifestacao.CIENTE_ORIENTADOR,
            responsavel=self.docente,
            solicitado_por=servidor,
            mensagem_solicitacao="Favor manifestar ciencia.",
        )
        manifestada = ManifestacaoProcesso.objects.create(
            processo=processo_manifestado,
            tipo=ManifestacaoProcesso.TipoManifestacao.CIENTE_ORIENTADOR,
            responsavel=self.docente,
            solicitado_por=servidor,
        )
        manifestada.registrar_manifestacao(
            autor=self.docente,
            aceito=True,
            mensagem="Ciente.",
        )

        self.client.force_login(self.docente)
        response = self.client.get(reverse("menu_ciencias_manifestadas"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "<h1 class=\"section-title\">Ciencias</h1>", html=True)
        self.assertContains(response, "Pendencias de ciencia")
        self.assertContains(response, "Ciencias ja manifestadas")
        self.assertContains(response, processo_pendente.assunto)
        self.assertContains(response, "Manifestar ciencia")
        self.assertContains(response, "Favor manifestar ciencia.")
        self.assertContains(response, processo_manifestado.assunto)
        self.assertContains(response, "Manifestacao: Ciente.")


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
        )
        criar_trajetoria(self.aluno)
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
