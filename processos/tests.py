from datetime import date, datetime, time, timedelta
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.core.exceptions import ValidationError
from django.utils import timezone

from .models import (
    AlteracaoAluno,
    Aluno,
    DisciplinaTrajetoria,
    DisponibilidadeSala,
    Docente,
    ManifestacaoProcesso,
    MembroBanca,
    Polo,
    PublicacaoTrajetoria,
    Processo,
    ReservaAmbiente,
    Sala,
    Setor,
    SetorMembro,
    SolicitacaoBanca,
    TrajetoriaAcademica,
    User,
)


class VersionViewTests(SimpleTestCase):
    @override_settings(
        APP_VERSION="main",
        APP_REVISION="abc123",
        APP_BUILD_RUN_ID="456",
        SECURE_SSL_REDIRECT=False,
    )
    def test_version_view_returns_build_metadata(self):
        response = self.client.get(reverse("version"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "version": "main",
                "revision": "abc123",
                "build_run_id": "456",
            },
        )


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

    def test_coordenador_cria_comissao_com_docente_e_aluno(self):
        self.client.force_login(self.coordenador)
        response = self.client.post(
            reverse("criar_comissao"),
            {
                "nome": "Comissao de Bolsas",
                "descricao": "Analise de bolsas",
                "email": "bolsas@example.com",
                "ativo": "on",
                "docentes": [self.docente.id],
                "alunos": [self.aluno.id],
            },
        )

        self.assertEqual(response.status_code, 302)
        setor = Setor.objects.get(nome="Comissao de Bolsas")
        self.assertEqual(setor.tipo, Setor.TipoSetor.COMISSAO)
        self.assertTrue(SetorMembro.objects.filter(setor=setor, usuario=self.docente, data_saida__isnull=True).exists())
        self.assertTrue(SetorMembro.objects.filter(setor=setor, usuario=self.aluno, data_saida__isnull=True).exists())

    def test_coordenador_renderiza_gestao_de_setores(self):
        self.client.force_login(self.coordenador)
        response = self.client.get(reverse("setores_comissoes"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Setores e comissões cadastrados")
        self.assertNotContains(response, "Membros alunos")

    def test_coordenador_renderiza_criacao_de_comissao(self):
        self.client.force_login(self.coordenador)
        response = self.client.get(reverse("criar_comissao"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Criar Comissão")
        self.assertContains(response, "Membros docentes")
        self.assertContains(response, "Membros servidores")
        self.assertContains(response, "Membros alunos")
        self.assertContains(response, "Alunos selecionados")
        self.assertContains(response, "resultados-alunos-comissao")

    def test_coordenador_edita_comissao_em_setores(self):
        setor = Setor.objects.create(nome="Comissao Editavel", tipo=Setor.TipoSetor.COMISSAO)
        SetorMembro.objects.create(setor=setor, usuario=self.docente, designado_por=self.coordenador)

        self.client.force_login(self.coordenador)
        get_response = self.client.get(reverse("setores_comissoes"), {"editar": setor.id})
        self.assertEqual(get_response.status_code, 200)
        self.assertContains(get_response, "Editar setor/comissão")

        post_response = self.client.post(
            reverse("setores_comissoes"),
            {
                "setor_id": setor.id,
                "nome": "Comissao Editada",
                "descricao": "Atualizada",
                "email": "",
                "ativo": "on",
                "docentes": [self.docente.id],
                "servidores": [self.servidor.id],
            },
        )
        self.assertEqual(post_response.status_code, 302)
        setor.refresh_from_db()
        self.assertEqual(setor.nome, "Comissao Editada")
        self.assertTrue(SetorMembro.objects.filter(setor=setor, usuario=self.servidor, data_saida__isnull=True).exists())

    def test_servidor_visualiza_setores_sem_acoes_de_edicao(self):
        setor = Setor.objects.create(nome="Comissao Visivel", tipo=Setor.TipoSetor.COMISSAO)
        SetorMembro.objects.create(setor=setor, usuario=self.docente, designado_por=self.coordenador)

        self.client.force_login(self.servidor)
        response = self.client.get(reverse("setores_comissoes"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Comissao Visivel")
        self.assertNotContains(response, "Membros alunos")
        self.assertNotContains(response, "Editar</a>", html=False)
        self.assertNotContains(response, "Encerrar</button>", html=False)

    def test_servidor_nao_altera_setores(self):
        self.client.force_login(self.servidor)
        response = self.client.post(
            reverse("setores_comissoes"),
            {
                "nome": "Comissao Indevida",
                "ativo": "on",
            },
        )

        self.assertEqual(response.status_code, 403)
        self.assertFalse(Setor.objects.filter(nome="Comissao Indevida").exists())

    def test_servidor_nao_acessa_criacao_de_comissao(self):
        self.client.force_login(self.servidor)
        response = self.client.get(reverse("criar_comissao"))

        self.assertEqual(response.status_code, 403)

    def test_membro_de_setor_acessa_caixa_e_detalhe_do_setor(self):
        setor = Setor.objects.create(nome="Comissao de Recursos", tipo=Setor.TipoSetor.COMISSAO)
        SetorMembro.objects.create(setor=setor, usuario=self.aluno, designado_por=self.coordenador)
        processo = Processo.objects.create(
            usuario_criado_por=self.docente,
            tipo=Processo.TipoProcesso.OUTRO,
            assunto="Processo da comissao",
            descricao="Analise pela comissao",
            setor_atual=setor,
        )

        self.client.force_login(self.aluno)
        caixa = self.client.get(reverse("coordenacao_caixa_processos"))
        self.assertEqual(caixa.status_code, 200)
        self.assertContains(caixa, processo.assunto)

        detalhe = self.client.get(reverse("processo_detalhe", args=[processo.id]))
        self.assertEqual(detalhe.status_code, 200)
        self.assertContains(detalhe, processo.assunto)

    def test_perfil_exibe_participacoes_ativas_e_historico(self):
        setor_ativo = Setor.objects.create(nome="Comissao Ativa", tipo=Setor.TipoSetor.COMISSAO)
        setor_encerrado = Setor.objects.create(nome="Comissao Encerrada", tipo=Setor.TipoSetor.COMISSAO)
        SetorMembro.objects.create(setor=setor_ativo, usuario=self.docente, designado_por=self.coordenador)
        SetorMembro.objects.create(
            setor=setor_encerrado,
            usuario=self.docente,
            designado_por=self.coordenador,
            data_saida=timezone.localdate(),
        )

        self.client.force_login(self.docente)
        response = self.client.get(reverse("me"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Setores e comissões atuais")
        self.assertContains(response, "Comissao Ativa")
        self.assertContains(response, "Histórico de participação")
        self.assertContains(response, "Comissao Encerrada")

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

    def test_aluno_acessa_propria_trajetoria_e_cadastra_publicacao(self):
        trajetoria = self.aluno.trajetorias.get(status=TrajetoriaAcademica.Status.ATIVA)
        self.client.force_login(self.aluno)
        response = self.client.post(
            reverse("aluno_detalhe", args=[self.aluno.id]),
            {
                "acao": "salvar_publicacao",
                "trajetoria_id": trajetoria.id,
                "titulo": "Artigo do discente",
                "tipo": PublicacaoTrajetoria.TipoPublicacao.ARTIGO_EVENTO,
                "autores": "Aluno Teste; Orientador",
                "veiculo": "Conferencia PPGEC",
                "ano": "2026",
                "doi_url": "https://example.com/artigo",
            },
        )

        self.assertEqual(response.status_code, 302)
        publicacao = PublicacaoTrajetoria.objects.get()
        self.assertEqual(publicacao.trajetoria_id, trajetoria.id)
        self.assertEqual(publicacao.criado_por_id, self.aluno.id)

    def test_aluno_nao_altera_disciplina(self):
        trajetoria = self.aluno.trajetorias.get(status=TrajetoriaAcademica.Status.ATIVA)
        self.client.force_login(self.aluno)
        response = self.client.post(
            reverse("aluno_detalhe", args=[self.aluno.id]),
            {
                "acao": "salvar_disciplina",
                "trajetoria_id": trajetoria.id,
                "nome": "Topicos Especiais",
                "situacao": DisciplinaTrajetoria.Situacao.CURSANDO,
            },
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(DisciplinaTrajetoria.objects.count(), 0)

    def test_servidor_cadastra_disciplina_na_trajetoria(self):
        trajetoria = self.aluno.trajetorias.get(status=TrajetoriaAcademica.Status.ATIVA)
        self.client.force_login(self.servidor)
        response = self.client.post(
            reverse("aluno_detalhe", args=[self.aluno.id]),
            {
                "acao": "salvar_disciplina",
                "trajetoria_id": trajetoria.id,
                "codigo": "PPG001",
                "nome": "Metodologia Cientifica",
                "semestre": "2026.1",
                "conceito": "A",
                "creditos": "4",
                "carga_horaria": "60",
                "situacao": DisciplinaTrajetoria.Situacao.APROVADA,
            },
        )

        self.assertEqual(response.status_code, 302)
        disciplina = DisciplinaTrajetoria.objects.get()
        self.assertEqual(disciplina.trajetoria_id, trajetoria.id)
        self.assertEqual(disciplina.nome, "Metodologia Cientifica")

    def test_servidor_edita_publicacao_na_trajetoria(self):
        trajetoria = self.aluno.trajetorias.get(status=TrajetoriaAcademica.Status.ATIVA)
        publicacao = PublicacaoTrajetoria.objects.create(
            trajetoria=trajetoria,
            titulo="Titulo antigo",
            tipo=PublicacaoTrajetoria.TipoPublicacao.OUTRO,
            criado_por=self.aluno,
        )

        self.client.force_login(self.servidor)
        response = self.client.post(
            reverse("aluno_detalhe", args=[self.aluno.id]),
            {
                "acao": "salvar_publicacao",
                "trajetoria_id": trajetoria.id,
                "publicacao_id": publicacao.id,
                "titulo": "Titulo atualizado",
                "tipo": PublicacaoTrajetoria.TipoPublicacao.ARTIGO_PERIODICO,
                "autores": "Aluno Teste",
                "veiculo": "Revista PPGEC",
                "ano": "2026",
                "doi_url": "",
            },
        )

        self.assertEqual(response.status_code, 302)
        publicacao.refresh_from_db()
        self.assertEqual(publicacao.titulo, "Titulo atualizado")
        self.assertEqual(publicacao.criado_por_id, self.aluno.id)

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
        self.assertNotContains(response, "Processos no Pleno")
        self.assertNotContains(response, 'class="nav"')
        self.assertContains(response, "Perfil")
        self.assertContains(response, "Sair")

    def test_membro_do_pleno_ve_menu_e_rota_de_processos_do_pleno(self):
        pleno = Setor.objects.get(nome="Colegiando PPGEC (Pleno)")
        SetorMembro.objects.create(setor=pleno, usuario=self.docente)

        self.client.force_login(self.docente)
        home = self.client.get(reverse("home"))
        response = self.client.get(reverse("menu_processos_pleno"))

        self.assertContains(home, "Processos no Pleno")
        self.assertContains(home, "Caixa de Processos")
        self.assertEqual(response.status_code, 200)

    def test_docente_fora_do_pleno_nao_acessa_processos_do_pleno(self):
        self.client.force_login(self.docente)
        response = self.client.get(reverse("menu_processos_pleno"))

        self.assertEqual(response.status_code, 403)

    def test_home_aluno_mantem_acesso_rapido_para_novo_processo(self):
        self.client.force_login(self.aluno)
        response = self.client.get(reverse("home"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Novo requerimento")
        self.assertContains(response, "Consultar processos")
        self.assertContains(response, "Programa de Pos-Graduacao")

    def test_home_servidor_exibe_menu_completo_de_reservas(self):
        servidor = User.objects.create_user(
            email="servidor.frontend@example.com",
            password="senha-segura-123",
            nome="Servidor Frontend",
            tipo_usuario=User.TipoUsuario.SERVIDOR,
        )

        self.client.force_login(servidor)
        response = self.client.get(reverse("home"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Dashboard")
        self.assertContains(response, "Alunos")
        self.assertContains(response, "Processos")
        self.assertContains(response, "Caixa de Processos")
        self.assertContains(response, "Reserva de Ambiente")
        self.assertContains(response, "Nova reserva de ambiente")
        self.assertContains(response, "Disponibilidade semanal")
        self.assertContains(response, "Reservas feitas")
        self.assertContains(response, "Cadastro de Salas")

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
        self.assertContains(response, "Caixa de Processos")
        self.assertContains(response, "Meus Processos")
        self.assertNotContains(response, "Processos no Pleno")
        self.assertContains(response, "Processos dos Orientandos")
        self.assertContains(response, "Ciências")
        self.assertNotContains(response, "Ciencias manifestadas")
        self.assertContains(response, "Meus Orientandos")
        self.assertContains(response, "Cadastro de Salas")

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


@override_settings(SECURE_SSL_REDIRECT=False)
class SolicitacaoBancaTests(TestCase):
    def setUp(self):
        self.docente = Docente.objects.create(
            email="orientador.banca@example.com",
            password="senha-segura-123",
            nome="Orientador Banca",
        )
        self.coorientador = Docente.objects.create(
            email="coorientador.banca@example.com",
            password="senha-segura-123",
            nome="Coorientador Banca",
        )
        self.outro_docente = Docente.objects.create(
            email="outro.banca@example.com",
            password="senha-segura-123",
            nome="Outro Docente",
        )
        self.servidor = User.objects.create_user(
            email="servidor.banca@example.com",
            password="senha-segura-123",
            nome="Servidor Banca",
            tipo_usuario=User.TipoUsuario.SERVIDOR,
        )
        self.aluno_mestrado = Aluno.objects.create(
            email="mestrando.banca@example.com",
            password="senha-segura-123",
            nome="Mestrando Banca",
            matricula="M123",
        )
        self.trajetoria_mestrado = TrajetoriaAcademica.objects.create(
            aluno=self.aluno_mestrado,
            nivel_curso=Aluno.NivelCurso.MESTRADO,
            status=TrajetoriaAcademica.Status.ATIVA,
            ingresso="2025.1",
            prazo_qualificacao="2025.2",
            prazo_defesa="2027.1",
            orientador=self.docente,
        )
        self.aluno_doutorado = Aluno.objects.create(
            email="doutorando.banca@example.com",
            password="senha-segura-123",
            nome="Doutorando Banca",
            matricula="D123",
        )
        self.trajetoria_doutorado = TrajetoriaAcademica.objects.create(
            aluno=self.aluno_doutorado,
            nivel_curso=Aluno.NivelCurso.DOUTORADO,
            status=TrajetoriaAcademica.Status.ATIVA,
            ingresso="2024.1",
            prazo_qualificacao="2025.2",
            prazo_defesa="2028.1",
            orientador=self.outro_docente,
            coorientador=self.docente,
        )

    def _dados_defesa_mestrado(self, **overrides):
        data = {
            "aluno": self.aluno_mestrado.id,
            "trajetoria": self.trajetoria_mestrado.id,
            "tipo_defesa": SolicitacaoBanca.TipoDefesa.DEFESA_MESTRADO,
            "titulo": "Arquitetura de sistemas distribuidos",
            "resumo": "Resumo da dissertacao.",
            "palavras_chave": "sistemas, distribuidos",
            "data_prevista": "2026-08-20",
            "horario_previsto": "14:00",
            "modalidade_local_link": "Sala 1",
            "requisitos_cumpridos": "on",
            "ciencia_recomendacao_mpf": "on",
            "membro_EXAMINADOR_EXTERNO_nome": "Externo Um",
            "membro_EXAMINADOR_EXTERNO_instituicao": "IES Externa",
            "membro_EXAMINADOR_EXTERNO_cpf": "529.982.247-25",
            "membro_EXAMINADOR_INTERNO_nome": "Interno Um",
            "membro_EXAMINADOR_INTERNO_cpf": "111.444.777-35",
            "membro_SUPLENTE_EXTERNO_nome": "Suplente Externo",
            "membro_SUPLENTE_EXTERNO_instituicao": "Outra IES",
            "membro_SUPLENTE_EXTERNO_cpf": "123.456.789-09",
            "membro_SUPLENTE_INTERNO_nome": "Suplente Interno",
            "membro_SUPLENTE_INTERNO_cpf": "935.411.347-80",
        }
        data.update(overrides)
        return data

    def _dados_defesa_doutorado(self, **overrides):
        data = {
            "aluno": self.aluno_doutorado.id,
            "trajetoria": self.trajetoria_doutorado.id,
            "tipo_defesa": SolicitacaoBanca.TipoDefesa.DEFESA_DOUTORADO,
            "titulo": "Tese em sistemas distribuidos",
            "resumo": "Resumo da tese.",
            "palavras_chave": "sistemas, tese",
            "data_prevista": "2026-09-20",
            "horario_previsto": "09:00",
            "modalidade_local_link": "Sala virtual",
            "requisitos_cumpridos": "on",
            "ciencia_recomendacao_mpf": "on",
            "membro_EXAMINADOR_EXTERNO_1_nome": "Externo Um",
            "membro_EXAMINADOR_EXTERNO_1_instituicao": "IES Um",
            "membro_EXAMINADOR_EXTERNO_1_cpf": "529.982.247-25",
            "membro_EXAMINADOR_EXTERNO_2_nome": "Externo Dois",
            "membro_EXAMINADOR_EXTERNO_2_instituicao": "IES Dois",
            "membro_EXAMINADOR_EXTERNO_2_cpf": "111.444.777-35",
            "membro_EXAMINADOR_INTERNO_nome": "Interno Um",
            "membro_EXAMINADOR_INTERNO_cpf": "123.456.789-09",
            "membro_SUPLENTE_EXTERNO_nome": "Suplente Externo",
            "membro_SUPLENTE_EXTERNO_instituicao": "IES Suplente",
            "membro_SUPLENTE_EXTERNO_cpf": "935.411.347-80",
            "membro_SUPLENTE_INTERNO_nome": "Suplente Interno",
        }
        data.update(overrides)
        return data

    def test_apenas_docente_acessa_solicitacoes_banca(self):
        self.client.force_login(self.servidor)
        response = self.client.get(reverse("solicitacoes_banca"))

        self.assertEqual(response.status_code, 403)

    def test_docente_visualiza_apenas_alunos_orientados_ou_coorientados(self):
        aluno_sem_vinculo = Aluno.objects.create(
            email="sem.vinculo@example.com",
            password="senha-segura-123",
            nome="Aluno Sem Vinculo",
        )
        TrajetoriaAcademica.objects.create(
            aluno=aluno_sem_vinculo,
            nivel_curso=Aluno.NivelCurso.MESTRADO,
            status=TrajetoriaAcademica.Status.ATIVA,
            ingresso="2025.1",
            orientador=self.outro_docente,
        )

        self.client.force_login(self.docente)
        response = self.client.get(reverse("solicitacao_banca_nova"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Mestrando Banca")
        self.assertContains(response, "Doutorando Banca")
        self.assertNotContains(response, "Aluno Sem Vinculo")

    def test_docente_salva_rascunho_de_solicitacao(self):
        self.client.force_login(self.docente)
        response = self.client.post(
            reverse("solicitacao_banca_nova"),
            {
                "acao": "rascunho",
                "aluno": self.aluno_mestrado.id,
                "trajetoria": self.trajetoria_mestrado.id,
                "tipo_defesa": SolicitacaoBanca.TipoDefesa.DEFESA_MESTRADO,
                "titulo": "Rascunho de dissertacao",
            },
        )

        self.assertEqual(response.status_code, 302)
        solicitacao = SolicitacaoBanca.objects.get()
        self.assertEqual(solicitacao.status, SolicitacaoBanca.Status.RASCUNHO)
        self.assertEqual(solicitacao.docente_id, self.docente.id)

    def test_docente_finaliza_solicitacao_com_membros_obrigatorios(self):
        self.client.force_login(self.docente)
        response = self.client.post(
            reverse("solicitacao_banca_nova"),
            {"acao": "finalizar", **self._dados_defesa_mestrado()},
        )

        self.assertEqual(response.status_code, 302)
        solicitacao = SolicitacaoBanca.objects.get()
        self.assertEqual(solicitacao.status, SolicitacaoBanca.Status.FINALIZADA)
        self.assertEqual(solicitacao.finalizado_por_id, self.docente.id)
        self.assertIsNotNone(solicitacao.finalizado_em)
        self.assertEqual(solicitacao.membros.count(), 4)

    def test_defesa_doutorado_finaliza_sem_quarto_examinador(self):
        self.client.force_login(self.docente)
        response = self.client.post(
            reverse("solicitacao_banca_nova"),
            {"acao": "finalizar", **self._dados_defesa_doutorado()},
        )

        self.assertEqual(response.status_code, 302)
        solicitacao = SolicitacaoBanca.objects.get()
        self.assertEqual(solicitacao.status, SolicitacaoBanca.Status.FINALIZADA)
        self.assertFalse(solicitacao.membros.filter(papel=MembroBanca.Papel.QUARTO_EXAMINADOR).exists())
        self.assertEqual(solicitacao.membros.count(), 5)

    def test_finalizacao_valida_cpf_brasileiro(self):
        self.client.force_login(self.docente)
        response = self.client.post(
            reverse("solicitacao_banca_nova"),
            {
                "acao": "finalizar",
                **self._dados_defesa_mestrado(membro_EXAMINADOR_EXTERNO_cpf="123.456.789-00"),
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Informe um CPF valido.")
        self.assertEqual(SolicitacaoBanca.objects.count(), 0)

    def test_novo_processo_docente_lista_apenas_formularios_proprios(self):
        propria = SolicitacaoBanca.objects.create(
            docente=self.docente,
            aluno=self.aluno_mestrado,
            trajetoria=self.trajetoria_mestrado,
            tipo_defesa=SolicitacaoBanca.TipoDefesa.DEFESA_MESTRADO,
            titulo="Solicitacao propria",
        )
        outra = SolicitacaoBanca.objects.create(
            docente=self.outro_docente,
            aluno=self.aluno_doutorado,
            trajetoria=self.trajetoria_doutorado,
            tipo_defesa=SolicitacaoBanca.TipoDefesa.QUALIFICACAO_DOUTORADO,
            titulo="Solicitacao de outro docente",
        )

        self.client.force_login(self.docente)
        response = self.client.get(reverse("novo_processo"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Formularios salvos")
        self.assertContains(response, str(propria))
        self.assertNotContains(response, str(outra))

    @patch("processos.views.send_email_novo_processo_secretaria.delay")
    @patch("processos.views.send_email_novo_processo_orientador.delay")
    @patch("processos.views.send_email_novo_processo_aluno.delay")
    def test_docente_anexa_solicitacao_de_banca_ao_criar_processo(
        self,
        _email_aluno,
        _email_orientador,
        _email_secretaria,
    ):
        Setor.objects.get_or_create(nome="Secretaria PPGEC", defaults={"ativo": True})
        solicitacao = SolicitacaoBanca.objects.create(
            docente=self.docente,
            aluno=self.aluno_mestrado,
            trajetoria=self.trajetoria_mestrado,
            tipo_defesa=SolicitacaoBanca.TipoDefesa.DEFESA_MESTRADO,
            titulo="Formulario para anexar",
        )

        self.client.force_login(self.docente)
        response = self.client.post(
            reverse("novo_processo"),
            {
                "tipo": Processo.TipoProcesso.DEFESA_MESTRADO,
                "assunto": "Solicitacao de banca",
                "descricao": "Processo aberto com formulario salvo.",
                "formularios_banca": [solicitacao.id],
            },
        )

        self.assertEqual(response.status_code, 302)
        solicitacao.refresh_from_db()
        self.assertIsNotNone(solicitacao.processo_id)
        self.assertEqual(solicitacao.processo.usuario_criado_por_id, self.docente.id)

        detalhe = self.client.get(reverse("processo_detalhe", args=[solicitacao.processo_id]))
        self.assertEqual(detalhe.status_code, 200)
        self.assertContains(detalhe, "Ver formulário")
        self.assertContains(detalhe, f'modal-banca-{solicitacao.id}')
        self.assertContains(detalhe, "Discente e trajetória")
        self.assertContains(detalhe, "Composição da banca")


class ReservaAmbienteTests(TestCase):
    def setUp(self):
        self.polo = Polo.objects.create(nome="Polo Centro")
        self.outro_polo = Polo.objects.create(nome="Polo Norte")
        self.sala = Sala.objects.create(polo=self.polo, nome="Sala 101", capacidade=30)
        self.outra_sala = Sala.objects.create(polo=self.outro_polo, nome="Sala 201", capacidade=20)
        DisponibilidadeSala.objects.create(
            sala=self.sala,
            dia_semana=0,
            hora_inicio=time(8, 0),
            hora_fim=time(12, 0),
        )
        DisponibilidadeSala.objects.create(
            sala=self.outra_sala,
            dia_semana=0,
            hora_inicio=time(8, 0),
            hora_fim=time(12, 0),
        )
        self.docente = Docente.objects.create(
            email="docente.reserva@example.com",
            password="senha-segura-123",
            nome="Docente Reserva",
        )
        self.servidor = User.objects.create_user(
            email="servidor.reserva@example.com",
            password="senha-segura-123",
            nome="Servidor Reserva",
            tipo_usuario=User.TipoUsuario.SERVIDOR,
            polo_atuacao=self.polo,
        )

    def _dt(self, dia, hora, minuto=0):
        return timezone.make_aware(datetime(2026, 6, dia, hora, minuto))

    def test_docente_cria_reserva_em_horario_disponivel(self):
        self.client.force_login(self.docente)
        response = self.client.post(
            reverse("reservas_ambientes"),
            {
                "sala": self.sala.id,
                "tipo": ReservaAmbiente.TipoReserva.AULA,
                "titulo": "Aula de pos-graduacao",
                "data_inicio": "2026-06-08",
                "hora_inicio": "09:00",
                "hora_fim": "10:00",
                "recorrencia": "NENHUMA",
            },
        )

        self.assertEqual(response.status_code, 302)
        reserva = ReservaAmbiente.objects.get()
        self.assertEqual(reserva.docente_id, self.docente.id)
        self.assertEqual(reserva.tipo, ReservaAmbiente.TipoReserva.AULA)

    def test_nao_permite_reserva_simultanea_mesma_sala(self):
        ReservaAmbiente.objects.create(
            sala=self.sala,
            docente=self.docente,
            criado_por=self.docente,
            tipo=ReservaAmbiente.TipoReserva.REUNIAO_PESQUISA,
            inicio=self._dt(8, 9),
            fim=self._dt(8, 10),
        )

        with self.assertRaises(ValidationError):
            ReservaAmbiente.objects.create(
                sala=self.sala,
                docente=self.docente,
                criado_por=self.docente,
                tipo=ReservaAmbiente.TipoReserva.DEFESA,
                inicio=self._dt(8, 9, 30),
                fim=self._dt(8, 10, 30),
            )

    def test_nao_permite_reserva_fora_disponibilidade(self):
        with self.assertRaises(ValidationError):
            ReservaAmbiente.objects.create(
                sala=self.sala,
                docente=self.docente,
                criado_por=self.docente,
                tipo=ReservaAmbiente.TipoReserva.AULA,
                inicio=self._dt(8, 13),
                fim=self._dt(8, 14),
            )

    def test_recorrencia_nao_pode_superar_seis_meses(self):
        with self.assertRaises(ValidationError):
            ReservaAmbiente.criar_reservas(
                sala=self.sala,
                docente=self.docente,
                criado_por=self.docente,
                tipo=ReservaAmbiente.TipoReserva.AULA,
                titulo="Aula recorrente",
                inicio=self._dt(8, 9),
                fim=self._dt(8, 10),
                recorrencia="SEMANAL",
                duracao_recorrencia_meses=7,
            )

    def test_cria_recorrencia_diaria_semanal_e_mensal(self):
        for dia_semana in range(7):
            DisponibilidadeSala.objects.get_or_create(
                sala=self.sala,
                dia_semana=dia_semana,
                defaults={"hora_inicio": time(8, 0), "hora_fim": time(12, 0)},
            )

        diaria = ReservaAmbiente.criar_reservas(
            sala=self.sala,
            docente=self.docente,
            criado_por=self.docente,
            tipo=ReservaAmbiente.TipoReserva.AULA,
            titulo="Aula diaria",
            inicio=self._dt(8, 8),
            fim=self._dt(8, 9),
            recorrencia="DIARIA",
            duracao_recorrencia_meses=1,
        )
        semanal = ReservaAmbiente.criar_reservas(
            sala=self.sala,
            docente=self.docente,
            criado_por=self.docente,
            tipo=ReservaAmbiente.TipoReserva.AULA,
            titulo="Aula semanal",
            inicio=self._dt(8, 9),
            fim=self._dt(8, 10),
            recorrencia="SEMANAL",
            duracao_recorrencia_meses=1,
        )
        mensal = ReservaAmbiente.criar_reservas(
            sala=self.sala,
            docente=self.docente,
            criado_por=self.docente,
            tipo=ReservaAmbiente.TipoReserva.AULA,
            titulo="Aula mensal",
            inicio=self._dt(8, 10),
            fim=self._dt(8, 11),
            recorrencia="MENSAL",
            duracao_recorrencia_meses=2,
        )

        self.assertEqual(len(diaria), 31)
        self.assertEqual(len(semanal), 5)
        self.assertEqual(len(mensal), 3)

    def test_servidor_enxerga_salas_de_todos_os_polos(self):
        self.client.force_login(self.servidor)
        response = self.client.get(reverse("reservas_ambientes"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Sala 101")
        self.assertContains(response, "Sala 201")
        self.assertContains(response, "Reservas feitas")
        self.assertContains(response, "Ver disponibilidade")

    def test_servidor_reserva_para_docente(self):
        self.client.force_login(self.servidor)
        response = self.client.post(
            reverse("reservas_ambientes"),
            {
                "sala": self.outra_sala.id,
                "docente": self.docente.id,
                "tipo": ReservaAmbiente.TipoReserva.DEFESA,
                "titulo": "Defesa de mestrado",
                "data_inicio": "2026-06-08",
                "hora_inicio": "10:00",
                "hora_fim": "11:00",
                "recorrencia": "NENHUMA",
            },
        )

        self.assertEqual(response.status_code, 302)
        reserva = ReservaAmbiente.objects.get()
        self.assertEqual(reserva.docente_id, self.docente.id)
        self.assertEqual(reserva.criado_por_id, self.servidor.id)
        self.assertEqual(reserva.sala_id, self.outra_sala.id)

    def test_visualiza_reservas_feitas_com_filtros(self):
        ReservaAmbiente.objects.create(
            sala=self.sala,
            docente=self.docente,
            criado_por=self.docente,
            tipo=ReservaAmbiente.TipoReserva.AULA,
            titulo="Aula de algoritmos",
            inicio=self._dt(8, 9),
            fim=self._dt(8, 10),
        )
        ReservaAmbiente.objects.create(
            sala=self.outra_sala,
            docente=self.docente,
            criado_por=self.servidor,
            tipo=ReservaAmbiente.TipoReserva.DEFESA,
            titulo="Defesa no polo norte",
            inicio=self._dt(8, 10),
            fim=self._dt(8, 11),
        )

        self.client.force_login(self.servidor)
        response = self.client.get(
            reverse("reservas_ambientes_feitas"),
            {"tipo": ReservaAmbiente.TipoReserva.DEFESA},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Defesa no polo norte")
        self.assertNotContains(response, "Aula de algoritmos")

    def test_docente_visualiza_apenas_suas_reservas(self):
        outro_docente = Docente.objects.create(
            email="outro.docente.reserva@example.com",
            password="senha-segura-123",
            nome="Outro Docente",
        )
        ReservaAmbiente.objects.create(
            sala=self.sala,
            docente=self.docente,
            criado_por=self.docente,
            tipo=ReservaAmbiente.TipoReserva.AULA,
            titulo="Minha reserva",
            inicio=self._dt(8, 9),
            fim=self._dt(8, 10),
        )
        ReservaAmbiente.objects.create(
            sala=self.outra_sala,
            docente=outro_docente,
            criado_por=outro_docente,
            tipo=ReservaAmbiente.TipoReserva.DEFESA,
            titulo="Reserva de outro docente",
            inicio=self._dt(8, 10),
            fim=self._dt(8, 11),
        )

        self.client.force_login(self.docente)
        response = self.client.get(reverse("reservas_ambientes_feitas"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Minha reserva")
        self.assertNotContains(response, "Reserva de outro docente")

    def test_coordenador_visualiza_reservas_de_todos_os_docentes(self):
        coordenador = Docente.objects.create(
            email="coordenador.reservas@example.com",
            password="senha-segura-123",
            nome="Coordenador Reservas",
            coordenador=True,
        )
        outro_docente = Docente.objects.create(
            email="outro.docente.todas.reservas@example.com",
            password="senha-segura-123",
            nome="Outro Docente Reservas",
        )
        ReservaAmbiente.objects.create(
            sala=self.sala,
            docente=self.docente,
            criado_por=self.docente,
            tipo=ReservaAmbiente.TipoReserva.AULA,
            titulo="Reserva do docente",
            inicio=self._dt(8, 9),
            fim=self._dt(8, 10),
        )
        ReservaAmbiente.objects.create(
            sala=self.outra_sala,
            docente=outro_docente,
            criado_por=outro_docente,
            tipo=ReservaAmbiente.TipoReserva.DEFESA,
            titulo="Reserva de outro docente",
            inicio=self._dt(8, 10),
            fim=self._dt(8, 11),
        )

        self.client.force_login(coordenador)
        response = self.client.get(reverse("reservas_ambientes_feitas"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Reserva do docente")
        self.assertContains(response, "Reserva de outro docente")
        self.assertContains(response, "Marcar como excluída")

    def test_coordenador_exclui_reserva_com_justificativa(self):
        coordenador = Docente.objects.create(
            email="coordenador.excluir.reserva@example.com",
            password="senha-segura-123",
            nome="Coordenador Excluir Reserva",
            coordenador=True,
        )
        reserva = ReservaAmbiente.objects.create(
            sala=self.sala,
            docente=self.docente,
            criado_por=self.docente,
            tipo=ReservaAmbiente.TipoReserva.AULA,
            titulo="Reserva a excluir",
            inicio=self._dt(8, 9),
            fim=self._dt(8, 10),
        )

        self.client.force_login(coordenador)
        response = self.client.post(
            reverse("reservas_ambientes_feitas"),
            {
                "acao": "excluir_reserva",
                "reserva_id": reserva.id,
                "justificativa": "Reserva cancelada pela coordenacao.",
            },
        )

        self.assertEqual(response.status_code, 302)
        reserva.refresh_from_db()
        self.assertEqual(reserva.status, ReservaAmbiente.StatusReserva.EXCLUIDA)
        self.assertEqual(reserva.excluida_por_id, coordenador.id)
        self.assertIsNotNone(reserva.excluida_em)
        self.assertEqual(reserva.justificativa_exclusao, "Reserva cancelada pela coordenacao.")

    def test_docente_da_reserva_pode_exclui_la(self):
        reserva = ReservaAmbiente.objects.create(
            sala=self.sala,
            docente=self.docente,
            criado_por=self.servidor,
            tipo=ReservaAmbiente.TipoReserva.AULA,
            titulo="Reserva do docente",
            inicio=self._dt(8, 9),
            fim=self._dt(8, 10),
        )

        self.client.force_login(self.docente)
        response = self.client.post(
            reverse("reservas_ambientes_feitas"),
            {
                "acao": "excluir_reserva",
                "reserva_id": reserva.id,
                "justificativa": "Cancelamento solicitado pelo docente.",
            },
        )

        self.assertEqual(response.status_code, 302)
        reserva.refresh_from_db()
        self.assertEqual(reserva.status, ReservaAmbiente.StatusReserva.EXCLUIDA)
        self.assertEqual(reserva.excluida_por_id, self.docente.id)
        self.assertEqual(reserva.justificativa_exclusao, "Cancelamento solicitado pelo docente.")

    def test_servidor_nao_exclui_reserva(self):
        reserva = ReservaAmbiente.objects.create(
            sala=self.sala,
            docente=self.docente,
            criado_por=self.docente,
            tipo=ReservaAmbiente.TipoReserva.AULA,
            titulo="Reserva protegida",
            inicio=self._dt(8, 9),
            fim=self._dt(8, 10),
        )

        self.client.force_login(self.servidor)
        response = self.client.post(
            reverse("reservas_ambientes_feitas"),
            {
                "acao": "excluir_reserva",
                "reserva_id": reserva.id,
                "justificativa": "Tentativa pela secretaria.",
            },
        )

        self.assertEqual(response.status_code, 403)
        reserva.refresh_from_db()
        self.assertEqual(reserva.status, ReservaAmbiente.StatusReserva.ATIVA)

    def test_reserva_excluida_nao_bloqueia_nova_reserva(self):
        coordenador = Docente.objects.create(
            email="coordenador.libera.reserva@example.com",
            password="senha-segura-123",
            nome="Coordenador Libera Reserva",
            coordenador=True,
        )
        reserva = ReservaAmbiente.objects.create(
            sala=self.sala,
            docente=self.docente,
            criado_por=self.docente,
            tipo=ReservaAmbiente.TipoReserva.AULA,
            titulo="Reserva original",
            inicio=self._dt(8, 9),
            fim=self._dt(8, 10),
        )
        reserva.excluir(usuario=coordenador, justificativa="Cancelamento aprovado.")

        nova_reserva = ReservaAmbiente.objects.create(
            sala=self.sala,
            docente=self.docente,
            criado_por=self.docente,
            tipo=ReservaAmbiente.TipoReserva.DEFESA,
            titulo="Nova reserva no mesmo horario",
            inicio=self._dt(8, 9, 30),
            fim=self._dt(8, 10, 30),
        )

        self.assertEqual(nova_reserva.status, ReservaAmbiente.StatusReserva.ATIVA)

    def test_exclusao_de_recorrencia_afeta_apenas_reservas_a_partir_do_dia(self):
        for dia_semana in range(7):
            DisponibilidadeSala.objects.get_or_create(
                sala=self.sala,
                dia_semana=dia_semana,
                defaults={"hora_inicio": time(8, 0), "hora_fim": time(12, 0)},
            )
        reservas = ReservaAmbiente.criar_reservas(
            sala=self.sala,
            docente=self.docente,
            criado_por=self.docente,
            tipo=ReservaAmbiente.TipoReserva.AULA,
            titulo="Aula diaria recorrente",
            inicio=self._dt(8, 9),
            fim=self._dt(8, 10),
            recorrencia="DIARIA",
            duracao_recorrencia_meses=1,
        )

        self.client.force_login(self.docente)
        with patch("processos.views.timezone.localdate", return_value=date(2026, 6, 12)):
            response = self.client.post(
                reverse("reservas_ambientes_feitas"),
                {
                    "acao": "excluir_reserva",
                    "reserva_id": reservas[0].id,
                    "justificativa": "Cancelamento da recorrencia.",
                },
            )

        self.assertEqual(response.status_code, 302)
        reservas_antes = ReservaAmbiente.objects.filter(
            grupo_recorrencia=reservas[0].grupo_recorrencia,
            inicio__date__lt=date(2026, 6, 12),
        )
        reservas_a_partir = ReservaAmbiente.objects.filter(
            grupo_recorrencia=reservas[0].grupo_recorrencia,
            inicio__date__gte=date(2026, 6, 12),
        )
        self.assertTrue(reservas_antes.exists())
        self.assertTrue(reservas_a_partir.exists())
        self.assertFalse(reservas_antes.exclude(status=ReservaAmbiente.StatusReserva.ATIVA).exists())
        self.assertFalse(reservas_a_partir.exclude(status=ReservaAmbiente.StatusReserva.EXCLUIDA).exists())

    def test_docente_visualiza_disponibilidade_semanal_com_reservas_de_outros(self):
        outro_docente = Docente.objects.create(
            email="outro.docente.calendario@example.com",
            password="senha-segura-123",
            nome="Outro Docente Calendario",
        )
        ReservaAmbiente.objects.create(
            sala=self.outra_sala,
            docente=outro_docente,
            criado_por=outro_docente,
            tipo=ReservaAmbiente.TipoReserva.DEFESA,
            titulo="Reserva privada de outro docente",
            inicio=self._dt(8, 10),
            fim=self._dt(8, 11),
        )

        self.client.force_login(self.docente)
        response = self.client.get(reverse("disponibilidade_ambientes"), {"semana": "2026-06-08"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Disponibilidade semanal")
        self.assertContains(response, "Livre 08:00-12:00")
        self.assertContains(response, "Ocupado 10:00-11:00 | Defesa")
        self.assertNotContains(response, "Reserva privada de outro docente")

    def test_formulario_informa_choque_e_nao_cria_reserva(self):
        ReservaAmbiente.objects.create(
            sala=self.sala,
            docente=self.docente,
            criado_por=self.docente,
            tipo=ReservaAmbiente.TipoReserva.AULA,
            inicio=self._dt(8, 9),
            fim=self._dt(8, 10),
        )

        self.client.force_login(self.docente)
        response = self.client.post(
            reverse("reservas_ambientes"),
            {
                "sala": self.sala.id,
                "tipo": ReservaAmbiente.TipoReserva.DEFESA,
                "titulo": "Defesa conflitante",
                "data_inicio": "2026-06-08",
                "hora_inicio": "09:30",
                "hora_fim": "10:30",
                "recorrencia": "NENHUMA",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Choque com reserva existente")
        self.assertContains(response, "08/06/2026")
        self.assertEqual(ReservaAmbiente.objects.count(), 1)

    def test_formulario_exige_inicio_e_fim_no_mesmo_dia(self):
        self.client.force_login(self.docente)
        response = self.client.post(
            reverse("reservas_ambientes"),
            {
                "sala": self.sala.id,
                "tipo": ReservaAmbiente.TipoReserva.AULA,
                "titulo": "Horario invalido",
                "data_inicio": "2026-06-08",
                "hora_inicio": "10:00",
                "hora_fim": "10:00",
                "recorrencia": "NENHUMA",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "A hora de fim deve ser posterior")
        self.assertEqual(ReservaAmbiente.objects.count(), 0)

    def test_docente_enxerga_salas_de_todos_os_polos(self):
        self.client.force_login(self.docente)
        response = self.client.get(reverse("reservas_ambientes"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Sala 101")
        self.assertContains(response, "Sala 201")

    def test_coordenador_acessa_cadastro_de_salas(self):
        coordenador = Docente.objects.create(
            email="coordenador.salas@example.com",
            password="senha-segura-123",
            nome="Coordenador Salas",
            coordenador=True,
        )

        self.client.force_login(coordenador)
        response = self.client.get(reverse("salas_ambientes"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Cadastro de Salas")
        self.assertNotContains(response, "Reservas de Salas")
        self.assertContains(response, "Sala 101")
        self.assertContains(response, "Sala 201")

    def test_servidor_edita_sala_do_proprio_polo(self):
        self.client.force_login(self.servidor)
        response = self.client.post(
            reverse("salas_ambientes"),
            {
                "acao": "editar_sala",
                "sala_id": self.sala.id,
                "sala_edit-nome": "Laboratorio 101",
                "sala_edit-capacidade": "35",
                "sala_edit-ativa": "on",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.sala.refresh_from_db()
        self.assertEqual(self.sala.nome, "Laboratorio 101")
        self.assertEqual(self.sala.capacidade, 35)
        self.assertTrue(self.sala.ativa)

    def test_servidor_adiciona_mesmo_horario_em_varios_dias(self):
        self.client.force_login(self.servidor)
        response = self.client.post(
            reverse("salas_ambientes"),
            {
                "acao": "adicionar_disponibilidade",
                "sala_id": self.sala.id,
                "disp-dias_semana": ["1", "2", "3"],
                "disp-hora_inicio": "14:00",
                "disp-hora_fim": "16:00",
            },
        )

        self.assertEqual(response.status_code, 302)
        disponibilidades = DisponibilidadeSala.objects.filter(
            sala=self.sala,
            hora_inicio=time(14, 0),
            hora_fim=time(16, 0),
        ).order_by("dia_semana")
        self.assertEqual(list(disponibilidades.values_list("dia_semana", flat=True)), [1, 2, 3])

    def test_servidor_exclui_horario_disponivel_da_sala(self):
        disponibilidade = DisponibilidadeSala.objects.get(sala=self.sala, dia_semana=0)

        self.client.force_login(self.servidor)
        response = self.client.post(
            reverse("salas_ambientes"),
            {
                "acao": "excluir_disponibilidade",
                "disponibilidade_id": disponibilidade.id,
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(DisponibilidadeSala.objects.filter(pk=disponibilidade.id).exists())
        self.assertTrue(DisponibilidadeSala.objects.filter(sala=self.outra_sala).exists())

    def test_docente_pode_reservar_salas_distintas_no_mesmo_horario(self):
        ReservaAmbiente.objects.create(
            sala=self.sala,
            docente=self.docente,
            criado_por=self.docente,
            tipo=ReservaAmbiente.TipoReserva.AULA,
            inicio=self._dt(8, 9),
            fim=self._dt(8, 10),
        )

        ReservaAmbiente.objects.create(
            sala=self.outra_sala,
            docente=self.docente,
            criado_por=self.docente,
            tipo=ReservaAmbiente.TipoReserva.AULA,
            inicio=self._dt(8, 9),
            fim=self._dt(8, 10),
        )

        self.assertEqual(ReservaAmbiente.objects.count(), 2)
