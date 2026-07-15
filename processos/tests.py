import tempfile
from datetime import date, datetime, time, timedelta
from io import BytesIO
from unittest.mock import patch
from zipfile import ZipFile

from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.core.exceptions import ValidationError
from django.utils import timezone

from .models import (
    AlteracaoAluno,
    Aluno,
    AulaPresencialOferta,
    Disciplina,
    DisciplinaTrajetoria,
    DisponibilidadeSala,
    Docente,
    EncontroOferta,
    ItemSolicitacaoMatricula,
    ManifestacaoProcesso,
    MembroBanca,
    OfertaDisciplina,
    PeriodoLetivo,
    Polo,
    PublicacaoTrajetoria,
    Processo,
    ReservaAmbiente,
    Sala,
    Setor,
    SetorMembro,
    SolicitacaoMatricula,
    SolicitacaoAssinatura,
    SolicitacaoBanca,
    TrajetoriaAcademica,
    TramitacaoProcesso,
    User,
)
from .services import (
    alunos_ativos_sem_matricula,
    cancelar_item_matricula,
    salvar_solicitacao_matricula,
)
from .tasks import atualizar_status_periodos_letivos


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


class MatriculaDomainTests(TestCase):
    def setUp(self):
        hoje = timezone.localdate()
        self.secretaria = User.objects.create_user(
            email="secretaria.matricula@example.com",
            password="senha-segura-123",
            nome="Secretaria",
            tipo_usuario=User.TipoUsuario.SERVIDOR,
        )
        self.docente = Docente.objects.create(
            email="docente.matricula@example.com",
            password="senha-segura-123",
            nome="Docente Matricula",
        )
        self.aluno = Aluno.objects.create(
            email="aluno.matricula@example.com",
            password="senha-segura-123",
            nome="Aluno Matricula",
        )
        criar_trajetoria(self.aluno)
        self.periodo = PeriodoLetivo.objects.create(
            nome="2026.1",
            status=PeriodoLetivo.Status.MATRICULA_ABERTA,
            prazo_cadastro_disciplinas=hoje,
            matricula_inicio=hoje,
            matricula_fim=hoje + timedelta(days=5),
            modificacao_inicio=hoje + timedelta(days=6),
            modificacao_fim=hoje + timedelta(days=10),
            criado_por=self.secretaria,
        )

    def criar_oferta(self, codigo, nome, inicio=time(8, 0), fim=time(10, 0), vagas=1):
        disciplina = Disciplina.objects.create(codigo=codigo, nome=nome)
        oferta = OfertaDisciplina.objects.create(
            periodo=self.periodo,
            disciplina=disciplina,
            docente_responsavel=self.docente,
            vagas_regulares=vagas,
            vagas_especiais=0,
            criada_por=self.secretaria,
        )
        EncontroOferta.objects.create(
            oferta=oferta,
            dia_semana=EncontroOferta.DiaSemana.SEGUNDA,
            hora_inicio=inicio,
            hora_fim=fim,
        )
        return oferta

    def test_solicitacao_impede_choque_de_horario(self):
        oferta_a = self.criar_oferta("MAT001", "Métodos I", time(8, 0), time(10, 0))
        oferta_b = self.criar_oferta("MAT002", "Métodos II", time(9, 0), time(11, 0))

        with self.assertRaises(ValidationError):
            salvar_solicitacao_matricula(
                aluno=self.aluno,
                periodo=self.periodo,
                tipo_aluno=SolicitacaoMatricula.TipoAluno.REGULAR,
                ofertas=[oferta_a, oferta_b],
            )

    def test_cancelamento_promove_primeiro_da_lista_de_espera(self):
        oferta = self.criar_oferta("MAT003", "Tópicos", vagas=1)
        aluno_espera = Aluno.objects.create(
            email="aluno.espera@example.com",
            password="senha-segura-123",
            nome="Aluno Espera",
        )
        criar_trajetoria(aluno_espera)
        solicitacao = salvar_solicitacao_matricula(
            aluno=self.aluno,
            periodo=self.periodo,
            tipo_aluno=SolicitacaoMatricula.TipoAluno.REGULAR,
            ofertas=[oferta],
        )
        item_solicitado = solicitacao.itens.get(oferta=oferta)
        self.assertEqual(item_solicitado.status, ItemSolicitacaoMatricula.Status.SOLICITADO)

        solicitacao_espera = salvar_solicitacao_matricula(
            aluno=aluno_espera,
            periodo=self.periodo,
            tipo_aluno=SolicitacaoMatricula.TipoAluno.REGULAR,
            ofertas=[oferta],
            aceitar_lista_espera=True,
        )
        item_espera = solicitacao_espera.itens.get(oferta=oferta)
        self.assertEqual(item_espera.status, ItemSolicitacaoMatricula.Status.EM_LISTA_ESPERA)

        cancelar_item_matricula(item=item_solicitado, usuario=self.secretaria)
        item_espera.refresh_from_db()
        self.assertEqual(item_espera.status, ItemSolicitacaoMatricula.Status.SOLICITADO)

    def test_task_atualiza_status_do_periodo_letivo(self):
        self.periodo.status = PeriodoLetivo.Status.PLANEJAMENTO
        self.periodo.save(update_fields=["status"])

        with patch("processos.tasks.timezone.localdate", return_value=timezone.localdate()):
            resultado = atualizar_status_periodos_letivos()

        self.periodo.refresh_from_db()
        self.assertEqual(self.periodo.status, PeriodoLetivo.Status.MATRICULA_ABERTA)
        self.assertEqual(resultado["atualizados"], 1)

    def test_aluno_pode_solicitar_matricula_vinculo_sem_disciplinas(self):
        solicitacao = salvar_solicitacao_matricula(
            aluno=self.aluno,
            periodo=self.periodo,
            tipo_matricula=SolicitacaoMatricula.TipoMatricula.VINCULO,
            tipo_aluno=SolicitacaoMatricula.TipoAluno.REGULAR,
            ofertas=[],
            observacao="Manter vínculo no semestre.",
        )

        self.assertEqual(solicitacao.tipo_matricula, SolicitacaoMatricula.TipoMatricula.VINCULO)
        self.assertEqual(solicitacao.status, SolicitacaoMatricula.Status.SOLICITADA)
        self.assertEqual(solicitacao.itens.count(), 0)

    def test_lista_alunos_ativos_sem_matricula_exclui_quem_fez_vinculo(self):
        criar_trajetoria(self.aluno)
        aluno_sem_matricula = Aluno.objects.create(
            email="sem.matricula@example.com",
            password="senha-segura-123",
            nome="Aluno Sem Matricula",
        )
        criar_trajetoria(aluno_sem_matricula)

        salvar_solicitacao_matricula(
            aluno=self.aluno,
            periodo=self.periodo,
            tipo_matricula=SolicitacaoMatricula.TipoMatricula.VINCULO,
            tipo_aluno=SolicitacaoMatricula.TipoAluno.REGULAR,
            ofertas=[],
        )

        pendentes = alunos_ativos_sem_matricula(self.periodo)
        self.assertIn(aluno_sem_matricula, pendentes)
        self.assertNotIn(self.aluno, pendentes)


@override_settings(SECURE_SSL_REDIRECT=False)
class MatriculaViewsTests(TestCase):
    def setUp(self):
        hoje = timezone.localdate()
        self.secretaria = User.objects.create_user(
            email="secretaria.views.matricula@example.com",
            password="senha-segura-123",
            nome="Secretaria Views",
            tipo_usuario=User.TipoUsuario.SERVIDOR,
        )
        self.docente = Docente.objects.create(
            email="docente.views.matricula@example.com",
            password="senha-segura-123",
            nome="Docente Views",
        )
        self.aluno = Aluno.objects.create(
            email="aluno.views.matricula@example.com",
            password="senha-segura-123",
            nome="Aluno Views",
        )
        criar_trajetoria(self.aluno)
        self.periodo = PeriodoLetivo.objects.create(
            nome="2026.2",
            status=PeriodoLetivo.Status.MATRICULA_ABERTA,
            data_inicio=hoje,
            data_fim=hoje + timedelta(days=60),
            prazo_agendamento_aulas_presenciais=hoje + timedelta(days=7),
            prazo_cadastro_disciplinas=hoje,
            matricula_inicio=hoje,
            matricula_fim=hoje + timedelta(days=5),
            modificacao_inicio=hoje + timedelta(days=6),
            modificacao_fim=hoje + timedelta(days=10),
            criado_por=self.secretaria,
        )
        self.disciplina = Disciplina.objects.create(codigo="VIS001", nome="Visualização")
        self.oferta = OfertaDisciplina.objects.create(
            periodo=self.periodo,
            disciplina=self.disciplina,
            docente_responsavel=self.docente,
            vagas_regulares=2,
            vagas_especiais=1,
            criada_por=self.secretaria,
        )
        EncontroOferta.objects.create(
            oferta=self.oferta,
            dia_semana=EncontroOferta.DiaSemana.TERCA,
            hora_inicio=time(14, 0),
            hora_fim=time(16, 0),
        )

    def test_gestao_renderiza_periodos_ofertas_e_lista_da_oferta(self):
        self.client.force_login(self.secretaria)

        periodos = self.client.get(reverse("matriculas_periodos"))
        self.assertEqual(periodos.status_code, 200)
        self.assertContains(periodos, "Períodos Letivos")
        self.assertContains(periodos, self.periodo.nome)

        ofertas = self.client.get(reverse("matriculas_ofertas"))
        self.assertEqual(ofertas.status_code, 200)
        self.assertContains(ofertas, self.disciplina.nome)
        self.assertContains(ofertas, "Horário semanal por período letivo")
        self.assertContains(ofertas, "Terça")
        self.assertNotContains(ofertas, 'class="schedule-head">Domingo</div>', html=False)
        self.assertNotContains(ofertas, "Disciplinas cadastradas")

        disciplinas = self.client.get(reverse("matriculas_disciplinas"))
        self.assertEqual(disciplinas.status_code, 200)
        self.assertContains(disciplinas, "Disciplinas cadastradas")
        self.assertContains(disciplinas, self.disciplina.nome)

        alunos = self.client.get(reverse("matricula_oferta_alunos", args=[self.oferta.pk]))
        self.assertEqual(alunos.status_code, 200)
        self.assertContains(alunos, "Exportar Excel")

    def test_ofertas_contabiliza_matriculas_solicitadas_e_lista_de_espera(self):
        aluno_espera = Aluno.objects.create(
            email="aluno.espera.views.matricula@example.com",
            password="senha-segura-123",
            nome="Aluno Espera",
        )
        aluno_solicitado = Aluno.objects.create(
            email="aluno.solicitado.views.matricula@example.com",
            password="senha-segura-123",
            nome="Aluno Solicitado",
        )
        solicitacao_pendente = SolicitacaoMatricula.objects.create(
            periodo=self.periodo,
            aluno=aluno_solicitado,
            status=SolicitacaoMatricula.Status.SOLICITADA,
        )
        ItemSolicitacaoMatricula.objects.create(
            solicitacao=solicitacao_pendente,
            oferta=self.oferta,
            status=ItemSolicitacaoMatricula.Status.SOLICITADO,
        )
        aluno_especial = Aluno.objects.create(
            email="aluno.especial.solicitado.views.matricula@example.com",
            password="senha-segura-123",
            nome="Aluno Especial Solicitado",
        )
        solicitacao_especial = SolicitacaoMatricula.objects.create(
            periodo=self.periodo,
            aluno=aluno_especial,
            tipo_aluno=SolicitacaoMatricula.TipoAluno.ESPECIAL,
            status=SolicitacaoMatricula.Status.SOLICITADA,
        )
        ItemSolicitacaoMatricula.objects.create(
            solicitacao=solicitacao_especial,
            oferta=self.oferta,
            status=ItemSolicitacaoMatricula.Status.SOLICITADO,
        )
        solicitacao_espera = SolicitacaoMatricula.objects.create(
            periodo=self.periodo,
            aluno=aluno_espera,
            status=SolicitacaoMatricula.Status.SOLICITADA,
        )
        ItemSolicitacaoMatricula.objects.create(
            solicitacao=solicitacao_espera,
            oferta=self.oferta,
            status=ItemSolicitacaoMatricula.Status.EM_LISTA_ESPERA,
        )
        aluno_especial_espera = Aluno.objects.create(
            email="aluno.especial.espera.views.matricula@example.com",
            password="senha-segura-123",
            nome="Aluno Especial Espera",
        )
        solicitacao_especial_espera = SolicitacaoMatricula.objects.create(
            periodo=self.periodo,
            aluno=aluno_especial_espera,
            tipo_aluno=SolicitacaoMatricula.TipoAluno.ESPECIAL,
            status=SolicitacaoMatricula.Status.SOLICITADA,
        )
        ItemSolicitacaoMatricula.objects.create(
            solicitacao=solicitacao_especial_espera,
            oferta=self.oferta,
            status=ItemSolicitacaoMatricula.Status.EM_LISTA_ESPERA,
        )
        self.client.force_login(self.secretaria)

        response = self.client.get(reverse("matriculas_ofertas"), {"periodo": self.periodo.pk})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Matrículas solicitadas: 2")
        self.assertContains(response, "Espera: 2")
        self.assertContains(response, "regulares: 1 | especiais: 1", count=2)

    def test_gestao_indefere_item_de_matricula(self):
        solicitacao = SolicitacaoMatricula.objects.create(
            periodo=self.periodo,
            aluno=self.aluno,
            status=SolicitacaoMatricula.Status.SOLICITADA,
        )
        item = ItemSolicitacaoMatricula.objects.create(
            solicitacao=solicitacao,
            oferta=self.oferta,
            status=ItemSolicitacaoMatricula.Status.SOLICITADO,
        )
        self.client.force_login(self.secretaria)

        response = self.client.get(reverse("matriculas_solicitacoes"), {"periodo": self.periodo.pk})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Solicitações de Matrícula")
        self.assertContains(response, self.aluno.nome)
        self.assertContains(response, self.disciplina.nome)
        self.assertNotContains(response, "Homologar")
        self.assertContains(response, "Indeferir")
        self.assertContains(response, "Matrículas solicitadas: regulares 1")
        self.assertContains(response, "Lista de espera: regulares 0")

        response = self.client.post(
            reverse("matriculas_solicitacoes"),
            {
                "acao": "indeferir_item",
                "periodo_id": self.periodo.pk,
                "item_id": item.pk,
                "motivo": "Indeferida em teste.",
            },
        )

        self.assertEqual(response.status_code, 302)
        item.refresh_from_db()
        solicitacao.refresh_from_db()
        self.assertEqual(item.status, ItemSolicitacaoMatricula.Status.INDEFERIDO)
        self.assertEqual(item.indeferido_por_id, self.secretaria.pk)
        self.assertEqual(item.motivo_indeferimento, "Indeferida em teste.")
        self.assertEqual(solicitacao.status, SolicitacaoMatricula.Status.INDEFERIDA)

    def test_ofertas_usa_periodo_mais_recente_e_permite_trocar_periodo(self):
        hoje = timezone.localdate()
        periodo_recente = PeriodoLetivo.objects.create(
            nome="2027.1",
            prazo_cadastro_disciplinas=hoje + timedelta(days=20),
            matricula_inicio=hoje + timedelta(days=30),
            matricula_fim=hoje + timedelta(days=35),
            modificacao_inicio=hoje + timedelta(days=36),
            modificacao_fim=hoje + timedelta(days=40),
            criado_por=self.secretaria,
        )
        disciplina_recente = Disciplina.objects.create(codigo="VIS888", nome="Oferta Recente")
        oferta_recente = OfertaDisciplina.objects.create(
            periodo=periodo_recente,
            disciplina=disciplina_recente,
            docente_responsavel=self.docente,
            vagas_regulares=1,
            vagas_especiais=0,
            criada_por=self.secretaria,
        )
        EncontroOferta.objects.create(
            oferta=oferta_recente,
            dia_semana=EncontroOferta.DiaSemana.QUARTA,
            hora_inicio=time(8, 0),
            hora_fim=time(10, 0),
        )
        self.client.force_login(self.secretaria)

        response = self.client.get(reverse("matriculas_ofertas"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Oferta Recente")
        self.assertNotContains(response, f"<strong>{self.disciplina.codigo} - {self.disciplina.nome}</strong>", html=True)

        response = self.client.get(reverse("matriculas_ofertas"), {"periodo": self.periodo.pk})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.disciplina.nome)
        self.assertNotContains(response, "<strong>VIS888 - Oferta Recente</strong>", html=True)

    def test_nao_permite_segundo_periodo_ativo(self):
        hoje = timezone.localdate()

        with self.assertRaises(ValidationError):
            PeriodoLetivo.objects.create(
                nome="2027.2",
                status=PeriodoLetivo.Status.MATRICULA_ABERTA,
                prazo_cadastro_disciplinas=hoje,
                matricula_inicio=hoje,
                matricula_fim=hoje + timedelta(days=5),
                modificacao_inicio=hoje + timedelta(days=6),
                modificacao_fim=hoje + timedelta(days=10),
                criado_por=self.secretaria,
            )

    def test_gestao_edita_disciplina_em_matriculas(self):
        self.client.force_login(self.secretaria)

        response = self.client.post(
            reverse("matriculas_disciplinas"),
            {
                "acao": "editar_disciplina",
                "disciplina_id": self.disciplina.pk,
                "codigo": "VIS002",
                "nome": "Visualização Atualizada",
                "creditos": "4",
                "carga_horaria": "60",
                "ativa": "on",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.disciplina.refresh_from_db()
        self.assertEqual(self.disciplina.codigo, "VIS002")
        self.assertEqual(self.disciplina.nome, "Visualização Atualizada")

    def test_editar_periodo_recalcula_status_para_matricula_aberta(self):
        hoje = timezone.localdate()
        futuro = PeriodoLetivo.objects.create(
            nome="2028.1",
            status=PeriodoLetivo.Status.PLANEJAMENTO,
            prazo_cadastro_disciplinas=hoje + timedelta(days=10),
            matricula_inicio=hoje + timedelta(days=20),
            matricula_fim=hoje + timedelta(days=25),
            modificacao_inicio=hoje + timedelta(days=26),
            modificacao_fim=hoje + timedelta(days=30),
            criado_por=self.secretaria,
        )
        self.periodo.status = PeriodoLetivo.Status.ENCERRADO
        self.periodo.encerrado_manualmente_em = timezone.now()
        self.periodo.encerrado_manualmente_por = self.secretaria
        self.periodo.save()
        self.client.force_login(self.secretaria)

        response = self.client.post(
            reverse("matriculas_periodos"),
            {
                "acao": "editar_periodo",
                "periodo_id": futuro.pk,
                "nome": futuro.nome,
                "prazo_cadastro_disciplinas": hoje.isoformat(),
                "matricula_inicio": hoje.isoformat(),
                "matricula_fim": (hoje + timedelta(days=5)).isoformat(),
                "modificacao_inicio": (hoje + timedelta(days=6)).isoformat(),
                "modificacao_fim": (hoje + timedelta(days=10)).isoformat(),
            },
        )

        self.assertEqual(response.status_code, 302)
        futuro.refresh_from_db()
        self.assertEqual(futuro.status, PeriodoLetivo.Status.MATRICULA_ABERTA)

    def test_aluno_renderiza_solicitacao_de_matricula(self):
        self.client.force_login(self.aluno)

        response = self.client.get(reverse("matricula_solicitar_periodo", args=[self.periodo.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.disciplina.nome)
        self.assertContains(response, "Matrícula vínculo")
        self.assertContains(response, "Tipo de vínculo")
        self.assertContains(response, "Confirmar matrícula")
        self.assertContains(response, "Confirmar e enviar")
        self.assertContains(response, "data-matricula-form")
        self.assertContains(response, 'document.querySelector("[data-matricula-form]")')
        self.assertNotContains(response, 'document.querySelector("form[method=&#x27;post&#x27;]")')
        self.assertContains(response, "Mestrado")
        self.assertContains(response, "aluno regular")
        self.assertNotContains(response, "Tipo de aluno</label>")
        self.assertNotContains(response, "data do servidor do AcadFlow")

    def test_aluno_sem_trajetoria_ativa_nao_visualiza_formulario_matricula(self):
        aluno_sem_trajetoria = Aluno.objects.create(
            email="sem.trajetoria.matricula@example.com",
            password="senha-segura-123",
            nome="Aluno Sem Trajetoria",
        )
        self.client.force_login(aluno_sem_trajetoria)

        response = self.client.get(reverse("matricula_solicitar_periodo", args=[self.periodo.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "trajetória acadêmica ativa")
        self.assertNotContains(response, "Enviar solicitação")

    def test_rota_base_matriculas_redireciona_para_minhas_matriculas(self):
        self.client.force_login(self.aluno)

        response = self.client.get("/matriculas/")

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("matriculas_minhas"))

    def test_aluno_visualiza_proximo_periodo_futuro_sem_formulario(self):
        hoje = timezone.localdate()
        futuro = PeriodoLetivo.objects.create(
            nome="2027.1",
            prazo_cadastro_disciplinas=hoje + timedelta(days=20),
            matricula_inicio=hoje + timedelta(days=30),
            matricula_fim=hoje + timedelta(days=35),
            modificacao_inicio=hoje + timedelta(days=36),
            modificacao_fim=hoje + timedelta(days=40),
            criado_por=self.secretaria,
        )
        self.periodo.encerrado_manualmente_em = timezone.now()
        self.periodo.encerrado_manualmente_por = self.secretaria
        self.periodo.status = PeriodoLetivo.Status.ENCERRADO
        self.periodo.save()
        self.client.force_login(self.aluno)

        response = self.client.get(reverse("matricula_solicitar_periodo", args=[futuro.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Próximo período previsto: 2027.1")
        self.assertNotContains(response, "Enviar solicitação")

    def test_docente_visualiza_horario_semanal_com_ofertas_de_outros_docentes(self):
        outro_docente = Docente.objects.create(
            email="outro.docente.views.matricula@example.com",
            password="senha-segura-123",
            nome="Outro Docente",
        )
        outra_disciplina = Disciplina.objects.create(codigo="VIS999", nome="Oferta de outro docente")
        outra_oferta = OfertaDisciplina.objects.create(
            periodo=self.periodo,
            disciplina=outra_disciplina,
            docente_responsavel=outro_docente,
            vagas_regulares=1,
            vagas_especiais=0,
            criada_por=outro_docente,
        )
        EncontroOferta.objects.create(
            oferta=outra_oferta,
            dia_semana=EncontroOferta.DiaSemana.QUARTA,
            hora_inicio=time(10, 0),
            hora_fim=time(12, 0),
        )
        self.client.force_login(self.docente)

        response = self.client.get(reverse("matriculas_ofertas"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, outra_disciplina.nome)
        self.assertContains(response, "Quarta")

    def test_gestao_cria_oferta_sem_segundo_dia_e_com_segundo_docente(self):
        colaborador = Docente.objects.create(
            email="colaborador.views.matricula@example.com",
            password="senha-segura-123",
            nome="Docente Colaborador",
        )
        disciplina = Disciplina.objects.create(codigo="VIS777", nome="Oferta com colaborador")
        self.client.force_login(self.secretaria)

        response = self.client.post(
            reverse("matriculas_ofertas"),
            {
                "acao": "criar_oferta",
                "periodo": self.periodo.pk,
                "disciplina": disciplina.pk,
                "docente_responsavel": self.docente.pk,
                "docente_colaborador": colaborador.pk,
                "modalidade": OfertaDisciplina.Modalidade.HIBRIDA,
                "vagas_regulares": "3",
                "vagas_especiais": "1",
                "dia_semana_1": EncontroOferta.DiaSemana.QUINTA,
                "hora_inicio_1": "08:00",
                "hora_fim_1": "10:00",
                "dia_semana_2": "",
                "hora_inicio_2": "",
                "hora_fim_2": "",
            },
        )

        self.assertEqual(response.status_code, 302)
        oferta = OfertaDisciplina.objects.get(disciplina=disciplina)
        self.assertEqual(oferta.docente_colaborador_id, colaborador.pk)
        self.assertEqual(oferta.encontros.count(), 1)

        response = self.client.get(reverse("matriculas_ofertas"))
        self.assertContains(response, "Docente Views / Docente Colaborador")

    def test_gestao_cria_mesma_disciplina_no_periodo_com_docente_diferente(self):
        outro_docente = Docente.objects.create(
            email="outro.docente.oferta@example.com",
            password="senha-segura-123",
            nome="Outro Docente Oferta",
        )
        self.client.force_login(self.secretaria)

        response = self.client.post(
            reverse("matriculas_ofertas"),
            {
                "acao": "criar_oferta",
                "periodo": self.periodo.pk,
                "disciplina": self.disciplina.pk,
                "docente_responsavel": outro_docente.pk,
                "docente_colaborador": "",
                "modalidade": OfertaDisciplina.Modalidade.PRESENCIAL,
                "vagas_regulares": "5",
                "vagas_especiais": "0",
                "dia_semana_1": EncontroOferta.DiaSemana.QUARTA,
                "hora_inicio_1": "08:00",
                "hora_fim_1": "10:00",
                "dia_semana_2": "",
                "hora_inicio_2": "",
                "hora_fim_2": "",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            OfertaDisciplina.objects.filter(periodo=self.periodo, disciplina=self.disciplina).count(),
            2,
        )
        response = self.client.get(reverse("matriculas_ofertas"), {"periodo": self.periodo.pk})
        self.assertContains(response, "Docente Views")
        self.assertContains(response, "Outro Docente Oferta")

    def test_oferta_nao_permite_mesma_disciplina_periodo_e_docente(self):
        oferta_duplicada = OfertaDisciplina(
            periodo=self.periodo,
            disciplina=self.disciplina,
            docente_responsavel=self.docente,
            vagas_regulares=1,
            vagas_especiais=0,
            criada_por=self.secretaria,
        )

        with self.assertRaises(ValidationError):
            oferta_duplicada.full_clean()

    def test_exportacao_xlsx_da_oferta(self):
        solicitacao = SolicitacaoMatricula.objects.create(
            periodo=self.periodo,
            aluno=self.aluno,
            status=SolicitacaoMatricula.Status.SOLICITADA,
        )
        ItemSolicitacaoMatricula.objects.create(
            solicitacao=solicitacao,
            oferta=self.oferta,
            status=ItemSolicitacaoMatricula.Status.SOLICITADO,
        )
        self.client.force_login(self.secretaria)

        response = self.client.get(reverse("matricula_oferta_exportar", args=[self.oferta.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        self.assertGreater(len(response.content), 100)
        with ZipFile(BytesIO(response.content)) as xlsx:
            sheet_xml = xlsx.read("xl/worksheets/sheet1.xml").decode()
        self.assertIn("Trajetória acadêmica mais recente", sheet_xml)
        self.assertIn("Mestrado", sheet_xml)

    @patch("processos.views.send_email_secretaria_planejamento_presencial.delay")
    def test_planejamento_presencial_cria_reserva_para_oferta_hibrida(self, mock_email):
        self.oferta.modalidade = OfertaDisciplina.Modalidade.HIBRIDA
        self.oferta.save(update_fields=["modalidade"])
        polo = Polo.objects.create(nome="Polo Matricula")
        sala = Sala.objects.create(polo=polo, nome="Sala Hibrida", capacidade=30)
        DisponibilidadeSala.objects.create(
            sala=sala,
            dia_semana=EncontroOferta.DiaSemana.TERCA,
            hora_inicio=time(13, 0),
            hora_fim=time(18, 0),
        )
        data_aula = self.periodo.data_inicio
        while data_aula.weekday() != EncontroOferta.DiaSemana.TERCA:
            data_aula += timedelta(days=1)
        encontro = self.oferta.encontros.get()
        self.client.force_login(self.docente)

        response = self.client.post(
            reverse("matricula_oferta_planejamento_presencial", args=[self.oferta.pk]),
            {
                "aula_data": data_aula.isoformat(),
                "aula_encontro": str(encontro.pk),
                "aula_hora_inicio": encontro.hora_inicio.strftime("%H:%M"),
                "aula_hora_fim": encontro.hora_fim.strftime("%H:%M"),
                "aula_sala": str(sala.pk),
            },
        )

        self.assertEqual(response.status_code, 302)
        aula = AulaPresencialOferta.objects.get(oferta=self.oferta)
        self.assertEqual(aula.sala, sala)
        self.assertEqual(aula.hora_inicio, encontro.hora_inicio)
        self.assertEqual(aula.hora_fim, encontro.hora_fim)
        self.assertEqual(aula.reserva.sala, sala)
        mock_email.assert_called_once_with(self.oferta.pk, self.docente.pk)

    def test_filtro_exibe_oferta_hibrida_nao_conforme(self):
        self.oferta.modalidade = OfertaDisciplina.Modalidade.HIBRIDA
        self.oferta.save(update_fields=["modalidade"])
        self.client.force_login(self.secretaria)

        response = self.client.get(reverse("matriculas_ofertas"), {"nao_conformes": "1"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Não conforme")

    @patch("processos.views.send_email_secretaria_planejamento_presencial.delay")
    def test_planejamento_presencial_permite_aula_avulsa_com_horario_editado(self, mock_email):
        self.oferta.modalidade = OfertaDisciplina.Modalidade.HIBRIDA
        self.oferta.save(update_fields=["modalidade"])
        polo = Polo.objects.create(nome="Polo Aula Avulsa")
        sala = Sala.objects.create(polo=polo, nome="Sala Avulsa", capacidade=20)
        data_aula = self.periodo.data_inicio + timedelta(days=2)
        DisponibilidadeSala.objects.create(
            sala=sala,
            dia_semana=data_aula.weekday(),
            hora_inicio=time(8, 0),
            hora_fim=time(18, 0),
        )
        self.client.force_login(self.docente)

        response = self.client.post(
            reverse("matricula_oferta_planejamento_presencial", args=[self.oferta.pk]),
            {
                "aula_data": data_aula.isoformat(),
                "aula_encontro": "",
                "aula_hora_inicio": "10:00",
                "aula_hora_fim": "12:30",
                "aula_sala": str(sala.pk),
            },
        )

        self.assertEqual(response.status_code, 302)
        aula = AulaPresencialOferta.objects.get(oferta=self.oferta)
        self.assertIsNone(aula.encontro)
        self.assertEqual(aula.hora_inicio, time(10, 0))
        self.assertEqual(aula.hora_fim, time(12, 30))
        self.assertEqual(aula.carga_horaria_minutos, 150)
        self.assertEqual(aula.reserva.inicio.time(), time(10, 0))
        self.assertEqual(aula.reserva.fim.time(), time(12, 30))
        mock_email.assert_called_once_with(self.oferta.pk, self.docente.pk)

    @patch("processos.views.send_email_alunos_sem_matricula.delay")
    def test_gestao_lista_alunos_sem_matricula_e_envia_email(self, mock_email):
        aluno_sem_matricula = Aluno.objects.create(
            email="pendente.matricula@example.com",
            password="senha-segura-123",
            nome="Aluno Pendente Matricula",
        )
        criar_trajetoria(aluno_sem_matricula)
        self.client.force_login(self.secretaria)
        total_pendentes = alunos_ativos_sem_matricula(self.periodo).count()

        response = self.client.get(reverse("matriculas_periodos"))
        self.assertContains(response, f"Alunos sem matrícula: {total_pendentes}")
        self.assertContains(response, "Aluno Pendente Matricula")

        post = self.client.post(
            reverse("matriculas_periodos"),
            {"acao": "enviar_email_sem_matricula", "periodo_id": self.periodo.pk},
        )

        self.assertEqual(post.status_code, 302)
        mock_email.assert_called_once_with(self.periodo.pk)

    def test_aluno_solicita_matricula_vinculo_pela_interface(self):
        self.client.force_login(self.aluno)

        response = self.client.post(
            reverse("matricula_solicitar_periodo", args=[self.periodo.pk]),
            {
                "periodo_id": self.periodo.pk,
                "matricula_vinculo": "on",
                "aceitar_lista_espera": "on",
                "observacao": "Sem disciplinas neste semestre.",
            },
        )

        self.assertEqual(response.status_code, 302)
        solicitacao = SolicitacaoMatricula.objects.get(aluno=self.aluno, periodo=self.periodo)
        self.assertEqual(solicitacao.tipo_matricula, SolicitacaoMatricula.TipoMatricula.VINCULO)
        self.assertEqual(solicitacao.itens.count(), 0)

    def test_sem_disciplinas_cria_matricula_vinculo_automaticamente(self):
        self.client.force_login(self.aluno)

        response = self.client.post(
            reverse("matricula_solicitar_periodo", args=[self.periodo.pk]),
            {
                "periodo_id": self.periodo.pk,
                "aceitar_lista_espera": "on",
            },
        )

        self.assertEqual(response.status_code, 302)
        solicitacao = SolicitacaoMatricula.objects.get(aluno=self.aluno, periodo=self.periodo)
        self.assertEqual(solicitacao.tipo_matricula, SolicitacaoMatricula.TipoMatricula.VINCULO)
        self.assertEqual(solicitacao.itens.count(), 0)

    def test_aluno_nao_consegue_solicitar_disciplinas_com_choque_de_horario(self):
        disciplina = Disciplina.objects.create(codigo="VIS002", nome="Visualização Avançada")
        oferta_conflitante = OfertaDisciplina.objects.create(
            periodo=self.periodo,
            disciplina=disciplina,
            docente_responsavel=self.docente,
            vagas_regulares=2,
            vagas_especiais=1,
            criada_por=self.secretaria,
        )
        EncontroOferta.objects.create(
            oferta=oferta_conflitante,
            dia_semana=EncontroOferta.DiaSemana.TERCA,
            hora_inicio=time(15, 0),
            hora_fim=time(17, 0),
        )
        self.client.force_login(self.aluno)

        response = self.client.post(
            reverse("matricula_solicitar_periodo", args=[self.periodo.pk]),
            {
                "periodo_id": self.periodo.pk,
                "ofertas": [str(self.oferta.pk), str(oferta_conflitante.pk)],
                "aceitar_lista_espera": "on",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Choque de horário")
        self.assertFalse(SolicitacaoMatricula.objects.filter(aluno=self.aluno, periodo=self.periodo).exists())

    def test_tipo_aluno_matricula_vem_da_trajetoria_ativa(self):
        aluno_especial = Aluno.objects.create(
            email="especial.matricula@example.com",
            password="senha-segura-123",
            nome="Aluno Especial Matricula",
        )
        criar_trajetoria(aluno_especial, nivel_curso=Aluno.NivelCurso.ALUNO_ESPECIAL)
        self.client.force_login(aluno_especial)

        response = self.client.post(
            reverse("matricula_solicitar_periodo", args=[self.periodo.pk]),
            {
                "periodo_id": self.periodo.pk,
                "ofertas": [str(self.oferta.pk)],
                "tipo_aluno": SolicitacaoMatricula.TipoAluno.REGULAR,
                "aceitar_lista_espera": "on",
            },
        )

        self.assertEqual(response.status_code, 302)
        solicitacao = SolicitacaoMatricula.objects.get(aluno=aluno_especial, periodo=self.periodo)
        self.assertEqual(solicitacao.tipo_aluno, SolicitacaoMatricula.TipoAluno.ESPECIAL)

    def test_aluno_precisa_confirmar_ciencia_para_solicitar_matricula(self):
        self.client.force_login(self.aluno)

        response = self.client.post(
            reverse("matricula_solicitar_periodo", args=[self.periodo.pk]),
            {
                "periodo_id": self.periodo.pk,
                "ofertas": [str(self.oferta.pk)],
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Confirme a ciência para enviar a solicitação.")
        self.assertFalse(SolicitacaoMatricula.objects.filter(aluno=self.aluno, periodo=self.periodo).exists())

    def test_aluno_posdoutorado_nao_visualiza_formulario_matricula(self):
        aluno_posdoc = Aluno.objects.create(
            email="posdoc.matricula@example.com",
            password="senha-segura-123",
            nome="Aluno Posdoc",
        )
        criar_trajetoria(aluno_posdoc, nivel_curso=Aluno.NivelCurso.POSDOUTORADO)
        self.client.force_login(aluno_posdoc)

        response = self.client.get(reverse("matricula_solicitar_periodo", args=[self.periodo.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Alunos de pós-doutorado não realizam matrícula")
        self.assertNotContains(response, "Enviar solicitação")


@override_settings(SECURE_SSL_REDIRECT=False)
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

    def test_secretaria_aprova_cadastro_de_aluno_em_avaliacao(self):
        aluno_pendente = Aluno.objects.create_user(
            email="pendente@example.com",
            password="senha-segura-123",
            nome="Aluno Pendente",
            status_aluno=Aluno.StatusAluno.EM_AVALIACAO,
        )
        trajetoria = criar_trajetoria(
            aluno_pendente,
            status=TrajetoriaAcademica.Status.EM_HOMOLOGACAO,
            orientador=self.docente,
        )

        self.client.force_login(self.servidor)
        home = self.client.get(reverse("home"))
        response = self.client.get(reverse("validar_cadastros_alunos"))

        self.assertContains(home, "Validar Cadastros")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Aluno Pendente")

        post = self.client.post(
            reverse("validar_cadastros_alunos"),
            {"aluno_id": aluno_pendente.id, "acao": "aprovar"},
        )

        self.assertEqual(post.status_code, 302)
        aluno_pendente.refresh_from_db()
        trajetoria.refresh_from_db()
        self.assertEqual(aluno_pendente.status_aluno, Aluno.StatusAluno.ATIVO)
        self.assertTrue(aluno_pendente.is_active)
        self.assertEqual(trajetoria.status, TrajetoriaAcademica.Status.ATIVA)

    def test_secretaria_reprova_cadastro_e_remove_trajetoria_em_homologacao(self):
        aluno_pendente = Aluno.objects.create_user(
            email="pendente.reprovado@example.com",
            password="senha-segura-123",
            nome="Aluno Reprovado",
            status_aluno=Aluno.StatusAluno.EM_AVALIACAO,
        )
        trajetoria = criar_trajetoria(
            aluno_pendente,
            status=TrajetoriaAcademica.Status.EM_HOMOLOGACAO,
            orientador=self.docente,
        )

        self.client.force_login(self.servidor)
        response = self.client.post(
            reverse("validar_cadastros_alunos"),
            {"aluno_id": aluno_pendente.id, "acao": "reprovar"},
        )

        self.assertEqual(response.status_code, 302)
        aluno_pendente.refresh_from_db()
        trajetoria.refresh_from_db()
        self.assertEqual(aluno_pendente.status_aluno, Aluno.StatusAluno.DESLIGADO)
        self.assertFalse(aluno_pendente.is_active)
        self.assertEqual(trajetoria.status, TrajetoriaAcademica.Status.REMOVIDA)
        self.assertIsNone(aluno_pendente.trajetoria_ativa())

        lista = self.client.get(reverse("coordenacao_alunos"), {"nome": "Aluno Reprovado"})
        self.assertEqual(lista.status_code, 200)
        self.assertContains(lista, "Aluno Reprovado")
        self.assertContains(lista, "Removida")

    def test_docente_nao_acessa_validacao_de_cadastros(self):
        self.client.force_login(self.docente)
        response = self.client.get(reverse("validar_cadastros_alunos"))

        self.assertEqual(response.status_code, 403)


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
        membro = Docente.objects.create(
            email="membro.comissao@example.com",
            password="senha-segura-123",
            nome="Membro Comissao",
        )
        SetorMembro.objects.create(setor=setor, usuario=membro, designado_por=self.coordenador)
        processo = Processo.objects.create(
            usuario_criado_por=self.aluno,
            tipo=Processo.TipoProcesso.OUTRO,
            assunto="Processo da comissao",
            descricao="Analise pela comissao",
            setor_atual=setor,
        )

        self.client.force_login(membro)
        caixa = self.client.get(reverse("coordenacao_caixa_processos"))
        self.assertEqual(caixa.status_code, 200)
        self.assertContains(caixa, processo.assunto)

        detalhe = self.client.get(reverse("processo_detalhe", args=[processo.id]))
        self.assertEqual(detalhe.status_code, 200)
        self.assertContains(detalhe, processo.assunto)

    def test_aluno_membro_da_secretaria_tem_acesso_de_gestao(self):
        secretaria, _ = Setor.objects.get_or_create(
            nome="Secretaria PPGEC",
            defaults={"tipo": Setor.TipoSetor.SETOR},
        )
        bolsista = Aluno.objects.create(
            email="bolsista.secretaria@example.com",
            password="senha-segura-123",
            nome="Bolsista Secretaria",
        )
        criar_trajetoria(bolsista)
        SetorMembro.objects.create(setor=secretaria, usuario=bolsista, designado_por=self.coordenador)

        self.client.force_login(bolsista)

        home = self.client.get(reverse("home"))
        self.assertEqual(home.status_code, 200)
        self.assertContains(home, "Dashboard")
        self.assertContains(home, "Validar Cadastros")
        self.assertContains(home, "Períodos letivos")
        self.assertContains(home, "Cadastro de Salas")
        self.assertContains(home, "Reserva de Ambiente")

        alunos = self.client.get(reverse("coordenacao_alunos"))
        self.assertEqual(alunos.status_code, 200)
        self.assertContains(alunos, self.aluno.nome)

        cadastros = self.client.get(reverse("validar_cadastros_alunos"))
        self.assertEqual(cadastros.status_code, 200)

        periodos = self.client.get(reverse("matriculas_periodos"))
        self.assertEqual(periodos.status_code, 200)

        setores = self.client.get(reverse("setores_comissoes"))
        self.assertEqual(setores.status_code, 200)

        reservas = self.client.get(reverse("reservas_ambientes"))
        self.assertEqual(reservas.status_code, 200)

    def test_aluno_membro_de_comissao_nao_recebe_acesso_global_de_secretaria(self):
        setor = Setor.objects.create(nome="Comissao Discente Sem Gestao", tipo=Setor.TipoSetor.COMISSAO)
        SetorMembro.objects.create(setor=setor, usuario=self.aluno, designado_por=self.coordenador)

        self.client.force_login(self.aluno)

        response = self.client.get(reverse("coordenacao_alunos"))

        self.assertEqual(response.status_code, 403)

    def test_aluno_nao_acessa_detalhe_de_processo_que_nao_criou(self):
        setor = Setor.objects.create(nome="Comissao Discente", tipo=Setor.TipoSetor.COMISSAO)
        SetorMembro.objects.create(setor=setor, usuario=self.aluno, designado_por=self.coordenador)
        processo = Processo.objects.create(
            usuario_criado_por=self.docente,
            tipo=Processo.TipoProcesso.OUTRO,
            assunto="Processo de outro usuario",
            descricao="Aluno membro nao deve visualizar",
            setor_atual=setor,
        )

        self.client.force_login(self.aluno)
        response = self.client.get(reverse("processo_detalhe", args=[processo.id]))

        self.assertEqual(response.status_code, 403)

    def test_historico_exibe_tramitacoes_da_mais_recente_para_a_mais_antiga(self):
        secretaria = Setor.objects.create(nome="Setor Historico Secretaria")
        coordenacao = Setor.objects.create(nome="Setor Historico Coordenacao")
        pleno = Setor.objects.create(nome="Setor Historico Pleno")
        processo = Processo.objects.create(
            usuario_criado_por=self.aluno,
            tipo=Processo.TipoProcesso.OUTRO,
            assunto="Processo com historico",
            descricao="Ordem de tramitacoes",
            setor_atual=pleno,
        )
        antiga = TramitacaoProcesso.objects.create(
            processo=processo,
            setor_origem=secretaria,
            setor_destino=coordenacao,
            encaminhado_por=self.servidor,
            observacao="Tramitacao antiga",
        )
        recente = TramitacaoProcesso.objects.create(
            processo=processo,
            setor_origem=coordenacao,
            setor_destino=pleno,
            encaminhado_por=self.coordenador,
            observacao="Tramitacao recente",
        )
        TramitacaoProcesso.objects.filter(pk=antiga.pk).update(
            data_encaminhamento=timezone.make_aware(datetime(2026, 6, 1, 9, 0))
        )
        TramitacaoProcesso.objects.filter(pk=recente.pk).update(
            data_encaminhamento=timezone.make_aware(datetime(2026, 6, 2, 9, 0))
        )

        self.client.force_login(self.servidor)
        response = self.client.get(reverse("processo_detalhe", args=[processo.id]))

        self.assertEqual(response.status_code, 200)
        conteudo = response.content.decode()
        self.assertLess(conteudo.index("Tramitacao recente"), conteudo.index("Tramitacao antiga"))

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

    def test_trajetoria_aluno_especial_mantem_apenas_ingresso(self):
        aluno_especial = Aluno.objects.create(
            email="aluno.especial@example.com",
            password="senha-segura-123",
            nome="Aluno Especial",
        )

        trajetoria = TrajetoriaAcademica.objects.create(
            aluno=aluno_especial,
            nivel_curso=Aluno.NivelCurso.ALUNO_ESPECIAL,
            status=TrajetoriaAcademica.Status.CONCLUIDA,
            ingresso="2026.1",
            prazo_qualificacao="2026.2",
            prazo_defesa="2027.1",
            orientador=self.docente,
            numero_defesa="ATA-IGNORADA",
            data_defesa=date(2026, 12, 20),
            deposito_versao_final=True,
            isQualificado=True,
            reingressante=True,
        )

        self.assertEqual(trajetoria.ingresso, "2026.1")
        self.assertEqual(trajetoria.prazo_qualificacao, "")
        self.assertEqual(trajetoria.prazo_defesa, "")
        self.assertIsNone(trajetoria.orientador)
        self.assertEqual(trajetoria.numero_defesa, "")
        self.assertIsNone(trajetoria.data_defesa)
        self.assertFalse(trajetoria.deposito_versao_final)
        self.assertFalse(trajetoria.isQualificado)
        self.assertFalse(trajetoria.reingressante)

    def test_posdoutorado_exige_relatorio_final_para_conclusao(self):
        posdoc = Aluno.objects.create(
            email="posdoc@example.com",
            password="senha-segura-123",
            nome="Pesquisador Posdoc",
        )

        with self.assertRaises(ValidationError):
            TrajetoriaAcademica.objects.create(
                aluno=posdoc,
                nivel_curso=Aluno.NivelCurso.POSDOUTORADO,
                status=TrajetoriaAcademica.Status.CONCLUIDA,
                ingresso="2026.1",
            )

        trajetoria = TrajetoriaAcademica.objects.create(
            aluno=posdoc,
            nivel_curso=Aluno.NivelCurso.POSDOUTORADO,
            status=TrajetoriaAcademica.Status.CONCLUIDA,
            ingresso="2026.1",
            numero_defesa="RF-2026-01",
            data_defesa=date(2026, 12, 20),
            prazo_qualificacao="2026.2",
            prazo_defesa="2027.1",
            orientador=self.docente,
            deposito_versao_final=True,
        )

        self.assertEqual(trajetoria.conclusao_label, "Relatorio final")
        self.assertEqual(trajetoria.numero_defesa, "RF-2026-01")
        self.assertEqual(trajetoria.prazo_qualificacao, "")
        self.assertEqual(trajetoria.prazo_defesa, "")
        self.assertIsNone(trajetoria.orientador)
        self.assertFalse(trajetoria.deposito_versao_final)

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
        self.polo, _ = Polo.objects.update_or_create(nome="POLI", defaults={"ativo": True})
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
        self.assertContains(response, reverse("password_reset"))
        self.assertContains(response, reverse("cadastro_aluno"))

    def test_cadastro_aluno_renderiza_identidade_acadflow(self):
        response = self.client.get(reverse("cadastro_aluno"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Cadastro de aluno")
        self.assertContains(response, "img/acadflow-logo.png")
        self.assertContains(response, "Email institucional")
        self.assertContains(response, "Polo do aluno")
        self.assertContains(response, "Sexo atribuido ao nascer")
        self.assertContains(response, 'class="card login-card"')

    def test_cadastro_aluno_cria_conta_em_avaliacao(self):
        response = self.client.post(
            reverse("cadastro_aluno"),
            {
                "nome": "Nova Aluna",
                "email": "nova.aluna@example.com",
                "password1": "senha-segura-123",
                "password2": "senha-segura-123",
                "polo_atuacao": self.polo.id,
                "sexo_atribuido_nascimento": Aluno.SexoAtribuidoNascimento.FEMININO,
                "nivel_curso": Aluno.NivelCurso.MESTRADO,
                "ingresso": "2026",
                "orientador": self.docente.id,
                "tipo_coorientador": "NENHUM",
            },
        )

        self.assertRedirects(response, reverse("cadastro_aluno_sucesso"))
        aluno = Aluno.objects.get(email="nova.aluna@example.com")
        self.assertEqual(aluno.status_aluno, Aluno.StatusAluno.EM_AVALIACAO)
        self.assertEqual(aluno.polo_atuacao_id, self.polo.id)
        self.assertEqual(aluno.sexo_atribuido_nascimento, Aluno.SexoAtribuidoNascimento.FEMININO)
        self.assertTrue(aluno.is_active)
        trajetoria = aluno.trajetorias.get()
        self.assertEqual(trajetoria.nivel_curso, Aluno.NivelCurso.MESTRADO)
        self.assertEqual(trajetoria.status, TrajetoriaAcademica.Status.EM_HOMOLOGACAO)
        self.assertEqual(trajetoria.ingresso, "2026.1")
        self.assertEqual(trajetoria.orientador_id, self.docente.id)

        self.assertTrue(self.client.login(email="nova.aluna@example.com", password="senha-segura-123"))
        home = self.client.get(reverse("home"))
        novo_processo = self.client.get(reverse("novo_processo"))

        self.assertEqual(home.status_code, 200)
        self.assertContains(home, "Minha Trajet")
        self.assertNotContains(home, "Novo requerimento")
        self.assertNotContains(home, "Novo Processo")
        self.assertEqual(novo_processo.status_code, 403)

    def test_esqueci_minha_senha_renderiza_identidade_acadflow(self):
        response = self.client.get(reverse("password_reset"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Recuperar senha")
        self.assertContains(response, "img/acadflow-logo.png")
        self.assertContains(response, 'class="card login-card"')

    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        DEFAULT_FROM_EMAIL="AcadFlow <noreply@example.com>",
    )
    def test_esqueci_minha_senha_envia_email_com_link_visual_acadflow(self):
        usuario = User.objects.create_user(
            email="recuperar.senha@example.com",
            password="senha-antiga-123",
            nome="Usuario Recuperacao",
            tipo_usuario=User.TipoUsuario.SERVIDOR,
        )

        response = self.client.post(reverse("password_reset"), {"email": usuario.email})

        self.assertRedirects(response, reverse("password_reset_done"))
        self.assertEqual(len(mail.outbox), 1)
        mensagem = mail.outbox[0]
        self.assertEqual(mensagem.to, [usuario.email])
        self.assertIn("Alteracao de senha", mensagem.subject)
        self.assertIn("/senha/redefinir/", mensagem.body)
        self.assertEqual(len(mensagem.alternatives), 1)
        html, content_type = mensagem.alternatives[0]
        self.assertEqual(content_type, "text/html")
        self.assertIn("AcadFlow - PPGEC", html)
        self.assertIn("Alterar senha", html)
        self.assertIn("/senha/redefinir/", html)

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
        self.setor_secretaria, _ = Setor.objects.get_or_create(nome="Secretaria PPGEC", defaults={"ativo": True})
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

    @patch("processos.views.send_email_novo_processo_secretaria.delay")
    @patch("processos.views.send_email_novo_processo_orientador.delay")
    @patch("processos.views.send_email_novo_processo_aluno.delay")
    def test_docente_finaliza_solicitacao_com_membros_obrigatorios(
        self,
        email_aluno,
        email_orientador,
        email_secretaria,
    ):
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
        self.assertIsNotNone(solicitacao.processo_id)
        self.assertEqual(solicitacao.processo.tipo, Processo.TipoProcesso.DEFESA_MESTRADO)
        self.assertEqual(solicitacao.processo.usuario_criado_por_id, self.docente.id)
        email_aluno.assert_called_once_with(solicitacao.processo_id)
        email_orientador.assert_called_once_with(solicitacao.processo_id)
        email_secretaria.assert_called_once_with(solicitacao.processo_id)

    @patch("processos.views.send_email_novo_processo_secretaria.delay")
    @patch("processos.views.send_email_novo_processo_orientador.delay")
    @patch("processos.views.send_email_novo_processo_aluno.delay")
    def test_defesa_doutorado_finaliza_sem_quarto_examinador(
        self,
        _email_aluno,
        _email_orientador,
        _email_secretaria,
    ):
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
        self.assertEqual(solicitacao.processo.tipo, Processo.TipoProcesso.DEFESA_DOUTORADO)

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

    def test_novo_processo_nao_lista_formularios_de_banca(self):
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
        self.assertNotContains(response, "Formularios salvos")
        self.assertNotContains(response, str(propria))
        self.assertNotContains(response, str(outra))

    @patch("processos.views.send_email_novo_processo_secretaria.delay")
    @patch("processos.views.send_email_novo_processo_orientador.delay")
    @patch("processos.views.send_email_novo_processo_aluno.delay")
    def test_solicitacao_finalizada_exibe_link_do_processo_na_listagem(
        self,
        _email_aluno,
        _email_orientador,
        _email_secretaria,
    ):
        self.client.force_login(self.docente)
        response = self.client.post(
            reverse("solicitacao_banca_nova"),
            {"acao": "finalizar", **self._dados_defesa_mestrado()},
        )

        self.assertEqual(response.status_code, 302)
        solicitacao = SolicitacaoBanca.objects.get()
        solicitacao.refresh_from_db()
        self.assertIsNotNone(solicitacao.processo_id)

        lista = self.client.get(reverse("solicitacoes_banca"))
        self.assertEqual(lista.status_code, 200)
        self.assertContains(lista, solicitacao.processo.numero)
        self.assertContains(lista, reverse("processo_detalhe", args=[solicitacao.processo_id]))

        detalhe = self.client.get(reverse("processo_detalhe", args=[solicitacao.processo_id]))
        self.assertEqual(detalhe.status_code, 200)
        self.assertContains(detalhe, "Ver formulário")
        self.assertContains(detalhe, f'modal-banca-{solicitacao.id}')
        self.assertContains(detalhe, "Discente e trajetória")
        self.assertContains(detalhe, "Composição da banca")


@override_settings(SECURE_SSL_REDIRECT=False, MEDIA_ROOT=tempfile.gettempdir())
class SolicitacaoAssinaturaTests(TestCase):
    def setUp(self):
        self.servidor = User.objects.create_user(
            email="secretaria.assinatura@example.com",
            password="senha-segura-123",
            nome="Secretaria Assinatura",
            tipo_usuario=User.TipoUsuario.SERVIDOR,
        )
        self.docente = Docente.objects.create(
            email="docente.assinatura@example.com",
            password="senha-segura-123",
            nome="Docente Assinatura",
        )
        self.membro = Docente.objects.create(
            email="membro.assinatura@example.com",
            password="senha-segura-123",
            nome="Membro Assinatura",
        )
        self.setor = Setor.objects.create(
            nome="Comissao de Assinaturas",
            tipo=Setor.TipoSetor.COMISSAO,
            email="comissao.assinatura@example.com",
        )
        SetorMembro.objects.create(setor=self.setor, usuario=self.membro, designado_por=self.servidor)

    @patch("processos.views.send_email_solicitacao_assinatura.delay")
    def test_secretaria_cria_solicitacao_para_docente_e_docente_atende_sei(self, email_task):
        self.client.force_login(self.servidor)
        response = self.client.post(
            reverse("nova_solicitacao_assinatura"),
            {
                "destinatario_tipo": SolicitacaoAssinatura.DestinatarioTipo.DOCENTE,
                "docente": self.docente.id,
                "tipo_documento": SolicitacaoAssinatura.TipoDocumento.DOCUMENTO_SEI,
                "numero_documento_sei": "12345.000001/2026-10",
                "observacao": "Assinar despacho.",
            },
        )

        self.assertEqual(response.status_code, 302)
        solicitacao = SolicitacaoAssinatura.objects.get()
        self.assertEqual(solicitacao.criado_por_id, self.servidor.id)
        self.assertEqual(solicitacao.docente_id, self.docente.id)
        self.assertEqual(solicitacao.status, SolicitacaoAssinatura.Status.PENDENTE)
        email_task.assert_called_once_with(solicitacao.id)

        lista = self.client.get(reverse("solicitacoes_assinatura"))
        nova = self.client.get(reverse("nova_solicitacao_assinatura"))
        self.assertContains(lista, "Acompanhe assinaturas")
        self.assertNotContains(lista, "Enviar solicitacao")
        self.assertContains(nova, "Nova Solicitacao de Assinatura")
        self.assertContains(nova, 'data-destinatario-field="DOCENTE"')
        self.assertContains(nova, 'data-destinatario-field="SETOR"')
        self.assertContains(nova, 'data-documento-field="DOCUMENTO_SEI"')
        self.assertContains(nova, 'data-documento-field="BLOCO_SEI"')
        self.assertContains(nova, 'data-documento-field="PDF"')
        self.assertContains(nova, "toggleDestinatario")
        self.assertContains(nova, "toggleDocumento")

        self.client.force_login(self.docente)
        detalhe = self.client.get(reverse("solicitacao_assinatura_detalhe", args=[solicitacao.id]))
        self.assertEqual(detalhe.status_code, 200)
        self.assertContains(detalhe, "12345.000001/2026-10")

        atender = self.client.post(
            reverse("solicitacao_assinatura_detalhe", args=[solicitacao.id]),
            {"observacao_assinatura": "Assinado no SEI."},
        )

        self.assertEqual(atender.status_code, 302)
        solicitacao.refresh_from_db()
        self.assertEqual(solicitacao.status, SolicitacaoAssinatura.Status.ASSINADO)
        self.assertEqual(solicitacao.assinado_por_id, self.docente.id)
        self.assertIsNotNone(solicitacao.assinado_em)

    def test_secretaria_ve_menu_de_assinaturas_com_opcoes_separadas(self):
        self.client.force_login(self.servidor)
        response = self.client.get(reverse("home"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Assinaturas")
        self.assertContains(response, "Nova solicitacao")
        self.assertContains(response, "Pendencias de assinatura")
        self.assertContains(response, "Solicitacoes feitas")

    def test_docente_ve_assinatura_pendente_na_home(self):
        solicitacao = SolicitacaoAssinatura.objects.create(
            criado_por=self.servidor,
            destinatario_tipo=SolicitacaoAssinatura.DestinatarioTipo.DOCENTE,
            docente=self.docente,
            tipo_documento=SolicitacaoAssinatura.TipoDocumento.DOCUMENTO_SEI,
            numero_documento_sei="SEI-555",
        )

        self.client.force_login(self.docente)
        response = self.client.get(reverse("home"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Assinaturas")
        self.assertContains(response, "Assinaturas pendentes")
        self.assertContains(response, "SEI-555")
        self.assertContains(response, reverse("solicitacao_assinatura_detalhe", args=[solicitacao.id]))

    def test_pendencias_de_assinatura_lista_apenas_o_que_usuario_deve_assinar(self):
        propria = SolicitacaoAssinatura.objects.create(
            criado_por=self.servidor,
            destinatario_tipo=SolicitacaoAssinatura.DestinatarioTipo.DOCENTE,
            docente=self.docente,
            tipo_documento=SolicitacaoAssinatura.TipoDocumento.DOCUMENTO_SEI,
            numero_documento_sei="SEI-PARA-MIM",
        )
        SolicitacaoAssinatura.objects.create(
            criado_por=self.servidor,
            destinatario_tipo=SolicitacaoAssinatura.DestinatarioTipo.DOCENTE,
            docente=self.membro,
            tipo_documento=SolicitacaoAssinatura.TipoDocumento.DOCUMENTO_SEI,
            numero_documento_sei="SEI-DE-OUTRO",
        )
        assinada = SolicitacaoAssinatura.objects.create(
            criado_por=self.servidor,
            destinatario_tipo=SolicitacaoAssinatura.DestinatarioTipo.DOCENTE,
            docente=self.docente,
            tipo_documento=SolicitacaoAssinatura.TipoDocumento.BLOCO_SEI,
            numero_bloco_sei="BLOCO-ASSINADO",
        )
        assinada.marcar_assinado(usuario=self.docente)

        self.client.force_login(self.docente)
        response = self.client.get(reverse("pendencias_assinatura"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Pendencias de Assinatura")
        self.assertContains(response, propria.referencia_documento)
        self.assertNotContains(response, "SEI-DE-OUTRO")
        self.assertNotContains(response, "BLOCO-ASSINADO")

    def test_membro_do_setor_atende_pdf_e_preserva_documentos(self):
        original = SimpleUploadedFile("original.pdf", b"%PDF-1.4 original", content_type="application/pdf")
        solicitacao = SolicitacaoAssinatura.objects.create(
            criado_por=self.servidor,
            destinatario_tipo=SolicitacaoAssinatura.DestinatarioTipo.SETOR,
            setor=self.setor,
            tipo_documento=SolicitacaoAssinatura.TipoDocumento.PDF,
            documento_pdf=original,
        )

        self.client.force_login(self.membro)
        detalhe = self.client.get(reverse("solicitacao_assinatura_detalhe", args=[solicitacao.id]))
        self.assertEqual(detalhe.status_code, 200)
        self.assertContains(detalhe, "Baixar PDF original")

        assinado = SimpleUploadedFile("assinado.pdf", b"%PDF-1.4 assinado", content_type="application/pdf")
        response = self.client.post(
            reverse("solicitacao_assinatura_detalhe", args=[solicitacao.id]),
            {
                "documento_assinado_pdf": assinado,
                "observacao_assinatura": "Assinado eletronicamente.",
            },
        )

        self.assertEqual(response.status_code, 302)
        solicitacao.refresh_from_db()
        self.assertEqual(solicitacao.status, SolicitacaoAssinatura.Status.ASSINADO)
        self.assertTrue(solicitacao.documento_pdf)
        self.assertTrue(solicitacao.documento_assinado_pdf)
        self.assertNotEqual(solicitacao.documento_pdf.name, solicitacao.documento_assinado_pdf.name)

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_email_de_solicitacao_e_enviado_ao_docente(self):
        from .tasks import send_email_solicitacao_assinatura

        solicitacao = SolicitacaoAssinatura.objects.create(
            criado_por=self.servidor,
            destinatario_tipo=SolicitacaoAssinatura.DestinatarioTipo.DOCENTE,
            docente=self.docente,
            tipo_documento=SolicitacaoAssinatura.TipoDocumento.BLOCO_SEI,
            numero_bloco_sei="987654",
        )

        send_email_solicitacao_assinatura(solicitacao.id)

        self.assertEqual(len(mail.outbox), 1)
        self.assertIn(self.docente.email, mail.outbox[0].to)
        self.assertIn("Solicitacao de assinatura", mail.outbox[0].subject)


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
