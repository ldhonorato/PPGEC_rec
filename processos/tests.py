import ast
import os
import re
import tempfile
from datetime import date, datetime, time, timedelta
from io import BytesIO
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

from django.conf import settings
from django.db import models
from django.core import mail
from django.templatetags.static import static
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.utils import timezone


from .templatetags.acadflow import url_protegida
from .views import _linhas_trajetoria
from django.db import IntegrityError

from .declaracoes_vinculo import (
    importar_declaracoes_de_vinculo,
    periodo_em_curso,
)
from .forms import SetorComissaoForm
from .models import (
    AlteracaoAluno,
    AlteracaoMatricula,
    Aluno,
    ApresentacaoQualificacao,
    Documento,
    AulaPresencialOferta,
    Disciplina,
    DisciplinaTrajetoria,
    ComentarioProcesso,
    DeliberacaoProcesso,
    DisponibilidadeSala,
    Docente,
    EncontroOferta,
    ItemSolicitacaoMatricula,
    LancamentoHorasComplementares,
    LoginThrottle,
    ManifestacaoProcesso,
    MembroBanca,
    OfertaDisciplina,
    DeclaracaoDeVinculo,
    PeriodoLetivo,
    Polo,
    PublicacaoTrajetoria,
    Processo,
    ProrrogacaoTrajetoria,
    ReservaAmbiente,
    Sala,
    Setor,
    SetorMembro,
    SolicitacaoMatricula,
    SolicitacaoAssinatura,
    SolicitacaoBanca,
    TrajetoriaAcademica,
    TrancamentoTrajetoria,
    TramitacaoProcesso,
    User,
)


class PrazosTrajetoriaTests(TestCase):
    def setUp(self):
        self.servidor = User.objects.create_user(
            email="prazos.servidor@example.com", password="senha", nome="Servidor",
            tipo_usuario=User.TipoUsuario.SERVIDOR,
        )
        self.aluno = Aluno.objects.create_user(
            email="prazos.aluno@example.com", password="senha", nome="Aluno",
        )

    def criar_trajetoria(self, nivel):
        return TrajetoriaAcademica.objects.create(
            aluno=self.aluno, nivel_curso=nivel, ingresso="2025.1",
            data_ingresso=date(2025, 2, 1), status=TrajetoriaAcademica.Status.ATIVA,
        )

    def test_limites_regimentais_sao_calculados_da_data_de_ingresso(self):
        mestrado = self.criar_trajetoria(Aluno.NivelCurso.MESTRADO)
        doutorado = self.criar_trajetoria(Aluno.NivelCurso.DOUTORADO)
        self.assertEqual(mestrado.data_minima_defesa, date(2026, 2, 1))
        self.assertEqual(mestrado.prazo_limite_regimental, date(2027, 2, 28))
        self.assertEqual(doutorado.data_minima_defesa, date(2027, 2, 1))
        self.assertEqual(doutorado.prazo_limite_regimental, date(2029, 2, 28))

    def test_doutorado_de_agosto_2022_tem_prazo_em_agosto_2026(self):
        trajetoria = TrajetoriaAcademica.objects.create(
            aluno=self.aluno, nivel_curso=Aluno.NivelCurso.DOUTORADO,
            ingresso="2022.2", data_ingresso=date(2022, 8, 1),
            status=TrajetoriaAcademica.Status.ATIVA,
        )
        self.assertEqual(trajetoria.prazo_limite_regimental, date(2026, 8, 31))

    def test_prorrogacao_nao_altera_limite_regimental(self):
        trajetoria = self.criar_trajetoria(Aluno.NivelCurso.MESTRADO)
        original = trajetoria.prazo_limite_regimental
        ProrrogacaoTrajetoria.objects.create(
            trajetoria=trajetoria, meses=3, registrado_por=self.servidor,
        )
        self.assertEqual(trajetoria.prazo_limite_regimental, original)
        self.assertEqual(trajetoria.meses_prorrogados, 3)

    def test_prorrogacoes_respeitam_o_total_regimental(self):
        trajetoria = self.criar_trajetoria(Aluno.NivelCurso.MESTRADO)
        ProrrogacaoTrajetoria.objects.create(trajetoria=trajetoria, meses=3, registrado_por=self.servidor)
        ProrrogacaoTrajetoria.objects.create(
            trajetoria=trajetoria, meses=3, registrado_por=self.servidor,
        )
        with self.assertRaises(ValidationError):
            ProrrogacaoTrajetoria.objects.create(
                trajetoria=trajetoria, meses=1, registrado_por=self.servidor,
            )

    def test_trancamento_desloca_apenas_limite_efetivo(self):
        trajetoria = self.criar_trajetoria(Aluno.NivelCurso.MESTRADO)
        TrancamentoTrajetoria.objects.create(
            trajetoria=trajetoria, data_inicio=date(2025, 6, 1), data_fim=date(2025, 6, 30),
            registrado_por=self.servidor,
        )
        self.assertEqual(trajetoria.prazo_limite_regimental, date(2027, 2, 28))
        self.assertEqual(trajetoria.prazo_limite_efetivo, date(2027, 3, 30))

    def test_qualificacao_doutorado_vence_no_quinto_semestre(self):
        trajetoria = TrajetoriaAcademica.objects.create(
            aluno=self.aluno, nivel_curso=Aluno.NivelCurso.DOUTORADO,
            ingresso="2022.2", data_ingresso=date(2022, 8, 1),
            status=TrajetoriaAcademica.Status.ATIVA,
        )
        self.assertEqual(trajetoria.prazo_qualificacao, "2024.2")
        linha = next(item for item in _linhas_trajetoria(trajetoria) if item["rotulo"] == "Prazo qualificação")
        self.assertEqual(linha["valor"], "2024.2")

    def test_projeto_mestrado_exige_metade_dos_creditos(self):
        trajetoria = self.criar_trajetoria(Aluno.NivelCurso.MESTRADO)
        with self.assertRaises(ValidationError):
            ApresentacaoQualificacao.objects.create(
                trajetoria=trajetoria, data_apresentacao=date(2025, 8, 1), conceito="A",
                registrado_por=self.servidor,
            )
        DisciplinaTrajetoria.objects.create(
            trajetoria=trajetoria, nome="Créditos aprovados", creditos=12,
            situacao=DisciplinaTrajetoria.Situacao.APROVADA,
        )
        ApresentacaoQualificacao.objects.create(
            trajetoria=trajetoria, data_apresentacao=date(2025, 8, 1), conceito="A",
            registrado_por=self.servidor,
        )
        trajetoria.refresh_from_db()
        self.assertTrue(trajetoria.isQualificado)

    def test_conceito_c_permite_uma_repeticao_em_ate_seis_meses_no_doutorado(self):
        trajetoria = self.criar_trajetoria(Aluno.NivelCurso.DOUTORADO)
        ApresentacaoQualificacao.objects.create(
            trajetoria=trajetoria, data_apresentacao=date(2026, 1, 10), conceito="C",
            registrado_por=self.servidor,
        )
        ApresentacaoQualificacao.objects.create(
            trajetoria=trajetoria, data_apresentacao=date(2026, 7, 10), conceito="B",
            registrado_por=self.servidor,
        )
        trajetoria.refresh_from_db()
        self.assertTrue(trajetoria.isQualificado)
        with self.assertRaises(ValidationError):
            ApresentacaoQualificacao.objects.create(
                trajetoria=trajetoria, data_apresentacao=date(2026, 7, 11), conceito="A",
                registrado_por=self.servidor,
            )
from .services import (
    alunos_ativos_sem_matricula,
    cancelar_item_matricula,
    salvar_solicitacao_matricula,
)
from .tasks import atualizar_status_periodos_letivos


@override_settings(SECURE_SSL_REDIRECT=False)
class ProcessoPlenoDeliberacaoTests(TestCase):
    def setUp(self):
        self.docente = User.objects.create_user(
            email="docente.deliberacao@example.com",
            password="senha-segura-123",
            nome="Docente Deliberação",
            tipo_usuario=User.TipoUsuario.DOCENTE,
        )
        self.aluno = User.objects.create_user(
            email="aluno.deliberacao@example.com",
            password="senha-segura-123",
            nome="Aluno Deliberação",
            tipo_usuario=User.TipoUsuario.ALUNO,
        )
        self.pleno, _ = Setor.objects.get_or_create(nome=Setor.NOME_PLENO)
        SetorMembro.objects.get_or_create(setor=self.pleno, usuario=self.docente)
        self.processo = Processo.objects.create(
            usuario_criado_por=self.aluno,
            tipo=Processo.TipoProcesso.OUTRO,
            assunto="Pleito em deliberação",
            descricao="Descrição extensa do pleito.",
            setor_atual=self.pleno,
            status=Processo.StatusProcesso.EM_DEBATE,
        )
        self.client.force_login(self.docente)

    def test_caixa_inclui_processo_em_debate_no_filtro_em_analise(self):
        response = self.client.get(
            reverse("coordenacao_caixa_processos"),
            {"caixa": self.pleno.id},
        )

        self.assertContains(response, self.processo.assunto)

    def test_observacao_nao_abre_debate_nem_envia_email(self):
        self.processo.status = Processo.StatusProcesso.EM_ANALISE
        self.processo.save(update_fields=["status"])

        with patch("processos.views.send_email_processo_comentado_pleno.delay") as enviar:
            response = self.client.post(
                reverse("processo_detalhe", args=[self.processo.id]),
                {"adicionar_comentario": "1", "tipo": "OBSERVACAO", "texto": "Registro pontual."},
            )

        self.assertRedirects(response, reverse("processo_detalhe", args=[self.processo.id]))
        self.processo.refresh_from_db()
        self.assertEqual(self.processo.status, Processo.StatusProcesso.EM_ANALISE)
        enviar.assert_not_called()

    def test_abertura_e_resposta_usam_notificacoes_distintas(self):
        self.processo.status = Processo.StatusProcesso.EM_ANALISE
        self.processo.save(update_fields=["status"])
        url = reverse("processo_detalhe", args=[self.processo.id])

        with patch("processos.views.send_email_processo_comentado_pleno.delay") as enviar:
            self.client.post(url, {"adicionar_comentario": "1", "tipo": "DEBATE", "texto": "Abro o debate."})
            self.client.post(url, {"adicionar_comentario": "1", "tipo": "DEBATE", "texto": "Complemento."})

        self.assertEqual(enviar.call_count, 2)
        self.assertNotIn("resposta", enviar.call_args_list[0].kwargs)
        self.assertTrue(enviar.call_args_list[1].kwargs["resposta"])

    def test_docente_pode_registrar_e_atualizar_manifestacao(self):
        url = reverse("processo_detalhe", args=[self.processo.id])
        self.client.post(url, {"registrar_deliberacao": "1", "posicao": "FAVORAVEL"})
        self.client.post(url, {"registrar_deliberacao": "1", "posicao": "ABSTENCAO"})

        deliberacoes = DeliberacaoProcesso.objects.filter(processo=self.processo, docente=self.docente)
        self.assertEqual(deliberacoes.count(), 1)
        self.assertEqual(deliberacoes.get().posicao, DeliberacaoProcesso.Posicao.ABSTENCAO)

    def test_detalhe_do_pleno_tem_descricao_e_historico_recolhiveis(self):
        response = self.client.get(reverse("processo_detalhe", args=[self.processo.id]))

        self.assertContains(response, "Ver descrição")
        self.assertContains(response, "Histórico do processo")
        self.assertContains(response, "Manifestação sobre o pleito")
        self.assertContains(response, "0 votos favoráveis")
        self.assertContains(response, "0 votos contrários")
        self.assertContains(response, "0 abstenções")


@override_settings(SECURE_SSL_REDIRECT=False)
class CienciaAntecipadaOrientadorTests(TestCase):
    def setUp(self):
        self.orientador = Docente.objects.create_user(
            email="orientador.ciencia@example.com",
            password="senha-segura-123",
            nome="Orientador Ciência",
        )
        self.outro_docente = Docente.objects.create_user(
            email="outro.ciencia@example.com",
            password="senha-segura-123",
            nome="Outro Docente",
        )
        self.aluno = Aluno.objects.create_user(
            email="aluno.ciencia@example.com",
            password="senha-segura-123",
            nome="Aluno Ciência",
        )
        self.setor = Setor.objects.create(nome="Secretaria Ciência")
        self.trajetoria = TrajetoriaAcademica.objects.create(
            aluno=self.aluno,
            nivel_curso=Aluno.NivelCurso.MESTRADO,
            status=TrajetoriaAcademica.Status.ATIVA,
            ingresso="2026.1",
            orientador=self.orientador,
        )
        self.processo = Processo.objects.create(
            usuario_criado_por=self.aluno,
            tipo=Processo.TipoProcesso.OUTRO,
            assunto="Pedido com ciência antecipada",
            descricao="Descrição do pedido.",
            setor_atual=self.setor,
        )
        self.url = reverse("processo_detalhe", args=[self.processo.id])

    def test_orientador_pode_dar_ciencia_antes_de_solicitacao(self):
        self.client.force_login(self.orientador)

        detalhe = self.client.get(self.url)
        self.assertContains(detalhe, "Dar ciência antecipadamente")

        response = self.client.post(
            self.url,
            {
                "manifestar_ciencia_espontanea": "1",
                "mensagem_manifestacao": "Estou ciente.",
            },
        )

        self.assertRedirects(response, self.url)
        manifestacao = self.processo.manifestacoes.get()
        self.assertEqual(manifestacao.status, ManifestacaoProcesso.StatusManifestacao.CIENTE)
        self.assertEqual(manifestacao.responsavel_id, self.orientador.id)
        self.assertEqual(manifestacao.solicitado_por_id, self.orientador.id)
        self.assertEqual(manifestacao.mensagem_manifestacao, "Estou ciente.")
        self.processo.refresh_from_db()
        self.assertEqual(self.processo.status, Processo.StatusProcesso.EM_ANALISE)
        self.assertEqual(self.processo.setor_atual_id, self.setor.id)

    def test_outro_docente_nao_pode_dar_ciencia_antecipada(self):
        self.client.force_login(self.outro_docente)
        response = self.client.post(
            self.url,
            {"manifestar_ciencia_espontanea": "1", "mensagem_manifestacao": "Ciente."},
        )

        self.assertEqual(response.status_code, 403)
        self.assertFalse(self.processo.manifestacoes.exists())

    def test_ciencia_antecipada_impede_solicitacao_posterior(self):
        self.processo.registrar_ciencia_espontanea_orientador(orientador=self.orientador)

        with self.assertRaisesMessage(ValidationError, "já manifestou ciência"):
            self.processo.solicitar_ciente_orientador(solicitado_por=self.outro_docente)

        self.assertEqual(self.processo.manifestacoes.count(), 1)

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_email_de_abertura_deixa_claro_que_e_apenas_notificacao(self):
        from .tasks import send_email_novo_processo_orientador

        send_email_novo_processo_orientador.run(self.processo.id)

        self.assertEqual(len(mail.outbox), 1)
        mensagem = mail.outbox[0]
        self.assertIn("Notificação de novo processo", mensagem.subject)
        html, content_type = mensagem.alternatives[0]
        self.assertEqual(content_type, "text/html")
        self.assertIn("apenas uma notificação de abertura", html)
        self.assertIn("pode não ser necessária nenhuma ação", html)
        self.assertIn("manifestar sua ciência antecipadamente", html)
        self.assertIn("Acessar processo", html)


@override_settings(SECURE_SSL_REDIRECT=False)
class CaixaProcessosFiltrosResumoTests(TestCase):
    def setUp(self):
        self.servidor = User.objects.create_user(
            email="servidor.caixas@example.com", password="senha-segura-123",
            nome="Servidor Caixas", tipo_usuario=User.TipoUsuario.SERVIDOR,
        )
        self.aluno = Aluno.objects.create_user(
            email="aluno.caixas@example.com", password="senha-segura-123", nome="Aluno Caixas",
        )
        self.secretaria = Setor.objects.get(nome=Setor.NOME_SECRETARIA)
        self.outra_caixa = Setor.objects.create(nome="Comissão Caixa Secundária")
        SetorMembro.objects.create(setor=self.outra_caixa, usuario=self.servidor)
        self.creditos = Processo.objects.create(
            usuario_criado_por=self.aluno, tipo=Processo.TipoProcesso.APROVEITAMENTO_DISPENSA_CREDITOS,
            assunto="Créditos na Secretaria", descricao="Processo da caixa padrão",
            setor_atual=self.secretaria, status=Processo.StatusProcesso.EM_ANALISE,
        )
        self.outro = Processo.objects.create(
            usuario_criado_por=self.aluno, tipo=Processo.TipoProcesso.OUTRO,
            assunto="Outro na Secretaria", descricao="Outro tipo da caixa padrão",
            setor_atual=self.secretaria, status=Processo.StatusProcesso.EM_ANALISE,
        )
        self.processo_outra_caixa = Processo.objects.create(
            usuario_criado_por=self.aluno, tipo=Processo.TipoProcesso.OUTRO,
            assunto="Processo da outra caixa", descricao="Não deve aparecer inicialmente",
            setor_atual=self.outra_caixa, status=Processo.StatusProcesso.EM_ANALISE,
        )
        self.client.force_login(self.servidor)

    def test_abertura_exibe_somente_a_primeira_caixa(self):
        response = self.client.get(reverse("coordenacao_caixa_processos"))

        self.assertEqual(response.context["selected_caixa"], str(self.secretaria.pk))
        self.assertContains(response, self.creditos.assunto)
        self.assertContains(response, self.outro.assunto)
        self.assertNotContains(response, self.processo_outra_caixa.assunto)

    def test_filtro_de_tipo_e_resumo_usam_a_caixa_selecionada(self):
        response = self.client.get(
            reverse("coordenacao_caixa_processos"),
            {"caixa": self.secretaria.pk, "tipo": Processo.TipoProcesso.OUTRO},
        )

        self.assertEqual(response.context["total_processos_caixa"], 2)
        self.assertEqual(
            {item["value"]: item["total"] for item in response.context["distribuicao_tipos"]},
            {Processo.TipoProcesso.APROVEITAMENTO_DISPENSA_CREDITOS: 1, Processo.TipoProcesso.OUTRO: 1},
        )
        self.assertContains(response, self.outro.assunto)
        self.assertNotContains(response, self.creditos.assunto)
        self.assertContains(response, "Tipo de processo")
        self.assertContains(response, "Total na caixa")


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


class SessionExpirationSettingsTests(SimpleTestCase):
    def test_login_expira_apos_vinte_minutos_sem_atividade(self):
        self.assertEqual(settings.SESSION_COOKIE_AGE, 20 * 60)
        self.assertTrue(settings.SESSION_SAVE_EVERY_REQUEST)


@override_settings(
    SECURE_SSL_REDIRECT=False,
    LOGIN_MAX_FAILURES=5,
    LOGIN_FAILURE_WINDOW_SECONDS=900,
    LOGIN_LOCKOUT_SECONDS=900,
)
class LoginSecurityTests(TestCase):
    def setUp(self):
        self.password = "senha-segura-123"
        self.user = User.objects.create_user(
            email="login.security@example.com",
            password=self.password,
            nome="Login Security",
        )
        self.url = reverse("login")

    def _post(self, password="incorreta", ip="192.0.2.10"):
        return self.client.post(
            self.url,
            {"username": self.user.email, "password": password},
            REMOTE_ADDR=ip,
        )

    def test_quinta_falha_bloqueia_e_informa_retry_after(self):
        for _ in range(4):
            self.assertEqual(self._post().status_code, 200)

        response = self._post()

        self.assertEqual(response.status_code, 429)
        self.assertIn("Retry-After", response)
        self.assertContains(response, "Muitas tentativas", status_code=429)
        self.assertEqual(LoginThrottle.objects.count(), 2)

    def test_bloqueio_por_conta_vale_para_outro_ip(self):
        for _ in range(5):
            self._post()

        response = self._post(password=self.password, ip="198.51.100.25")

        self.assertEqual(response.status_code, 429)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_login_valido_limpa_falhas_anteriores(self):
        for _ in range(4):
            self._post()

        response = self._post(password=self.password)

        self.assertEqual(response.status_code, 302)
        self.assertFalse(LoginThrottle.objects.exists())

    @override_settings(SECURE_SSL_REDIRECT=True)
    def test_post_de_login_em_http_e_redirecionado_sem_validar_credenciais(self):
        response = self.client.post(
            self.url,
            {"username": self.user.email, "password": self.password},
            secure=False,
        )

        self.assertEqual(response.status_code, 301)
        self.assertTrue(response["Location"].startswith("https://"))
        self.assertFalse(LoginThrottle.objects.exists())


class TelaDeLoginTests(TestCase):
    """A rota /login/ acumula duas responsabilidades que chegaram separadas.

    A limitacao de tentativas mora na RateLimitedLoginView; o desvio de quem ja
    tem sessao e o sumico da moldura moram nos argumentos do as_view(). As duas
    coisas ocupam a mesma linha do urls.py, e ja se perderam uma vez num merge:
    trocar a classe da view derruba silenciosamente os argumentos, e acrescentar
    argumentos derruba silenciosamente a classe. Nenhuma das duas quedas aparece
    em teste de tela.
    """

    def setUp(self):
        self.url = reverse("login")

    def test_quem_ja_tem_sessao_vai_para_o_inicio(self):
        """Sem isto, /login/ logado mostrava o formulario de entrada com a barra
        da sessao ativa por cima -- nome, avisos e tudo."""
        user = User.objects.create_user(
            email="ja.logado@example.com",
            password="senha-segura-123",
            nome="Ja Logado",
        )
        self.client.force_login(user)

        resposta = self.client.get(self.url)

        self.assertRedirects(resposta, reverse(settings.LOGIN_REDIRECT_URL))

    def test_anonimo_recebe_o_formulario_sem_a_moldura_da_sessao(self):
        resposta = self.client.get(self.url)

        self.assertEqual(resposta.status_code, 200)
        self.assertFalse(resposta.context["mostra_moldura"])
        self.assertNotContains(resposta, 'class="user-menu"')

    def test_o_limite_de_tentativas_continua_valendo_nesta_rota(self):
        """Guarda a convivencia: os argumentos do as_view() nao substituiram a
        view que conta as falhas."""
        User.objects.create_user(
            email="tentativas@example.com",
            password="senha-segura-123",
            nome="Tentativas",
        )
        for _ in range(settings.LOGIN_MAX_FAILURES):
            self.client.post(self.url, {"username": "tentativas@example.com", "password": "errada"})

        resposta = self.client.post(
            self.url, {"username": "tentativas@example.com", "password": "senha-segura-123"}
        )

        self.assertEqual(resposta.status_code, 429)


@override_settings(SECURE_SSL_REDIRECT=False, DEBUG=False)
class ArquivoEnviadoTests(TestCase):
    """Um arquivo de /media/ so e entregue a quem pode ver o registro dono dele.

    A entrega era feita por django.views.static.serve atras de
    @login_required: exigia estar logado e nada mais. Documento tem regra por
    objeto -- pode_visualizar_arquivo, com oito classificacoes de sigilo -- e o
    template a respeitava, escondendo o link de quem nao pode ver. O arquivo
    nao. Bastava conhecer o caminho.

    Reproduzido antes da correcao: com um documento marcado
    INFORMACAO_PESSOAL, pode_visualizar_arquivo(aluno) devolvia False e
    GET /media/documentos/processos/<arquivo> como aquele aluno devolvia 200
    com o conteudo.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._media = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls._media.cleanup)
        cls._override = override_settings(MEDIA_ROOT=cls._media.name)
        cls._override.enable()
        cls.addClassCleanup(cls._override.disable)

    def setUp(self):
        senha = "senha-segura-123"
        self.servidor = User.objects.create_user(
            email="servidor.arquivo@example.com", password=senha,
            nome="Servidor Arquivo", tipo_usuario=User.TipoUsuario.SERVIDOR,
        )
        self.aluno = Aluno.objects.create_user(
            email="aluno.arquivo@example.com", password=senha, nome="Aluno Arquivo",
            tipo_usuario=User.TipoUsuario.ALUNO, matricula="2026F0001",
        )
        self.setor = Setor.objects.create(nome="Setor de Arquivo", descricao="Teste")
        self.processo = Processo.objects.create(
            tipo=Processo.TipoProcesso.OUTRO, assunto="Processo de teste de arquivo",
            descricao="Teste", usuario_criado_por=self.aluno, setor_atual=self.setor,
        )

    def _documento(self, *, restricao, conteudo=b"conteudo confidencial"):
        return Documento.objects.create(
            processo=self.processo, titulo="Documento de teste",
            enviado_por=self.servidor, restricao_tipo=restricao,
            arquivo=SimpleUploadedFile("documento-teste.txt", conteudo),
        )

    def test_documento_livre_e_entregue_a_quem_esta_logado(self):
        documento = self._documento(restricao=Documento.RestricaoAcesso.NAO)
        self.client.force_login(self.aluno)

        resposta = self.client.get(documento.arquivo.url)

        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(b"".join(resposta.streaming_content), b"conteudo confidencial")

    def test_documento_restrito_nao_e_entregue_a_quem_nao_pode_ver(self):
        """O caso que estava aberto."""
        documento = self._documento(restricao=Documento.RestricaoAcesso.INFORMACAO_PESSOAL)
        self.assertFalse(documento.pode_visualizar_arquivo(self.aluno))
        self.client.force_login(self.aluno)

        resposta = self.client.get(documento.arquivo.url)

        # 404 e nao 403: um 403 confirmaria que existe arquivo naquele caminho,
        # e estes documentos carregam classificacao de sigilo.
        self.assertEqual(resposta.status_code, 404)

    def test_documento_restrito_e_entregue_a_quem_pode_ver(self):
        """A regra fecha o acesso indevido sem fechar o devido."""
        documento = self._documento(restricao=Documento.RestricaoAcesso.INFORMACAO_PESSOAL)
        self.assertTrue(documento.pode_visualizar_arquivo(self.servidor))
        self.client.force_login(self.servidor)

        self.assertEqual(self.client.get(documento.arquivo.url).status_code, 200)

    def test_arquivo_sem_registro_nao_e_entregue(self):
        """Sobra no disco nao e conteudo do sistema.

        Antes, qualquer caminho existente sob MEDIA_ROOT era servido -- inclusive
        arquivo de registro apagado, ou deixado ali por engano.
        """
        orfao = Path(settings.MEDIA_ROOT) / "documentos" / "processos" / "orfao.txt"
        orfao.parent.mkdir(parents=True, exist_ok=True)
        orfao.write_bytes(b"arquivo sem dono")
        self.client.force_login(self.servidor)

        resposta = self.client.get("/media/documentos/processos/orfao.txt")

        self.assertEqual(resposta.status_code, 404)

    def test_anonimo_vai_para_o_login(self):
        """Comportamento preservado de quando a rota foi criada."""
        documento = self._documento(restricao=Documento.RestricaoAcesso.NAO)
        url = documento.arquivo.url

        self.assertRedirects(self.client.get(url), f"{reverse('login')}?next={url}")

    def test_o_link_da_tela_aponta_para_a_rota_com_verificacao(self):
        """O template nao pode linkar o arquivo direto.

        Enquanto o armazenamento e disco, ``arquivo.url`` produz "/media/..." --
        que por acaso e a rota verificada. Com o S3, o mesmo ``.url`` passa a
        devolver o endereco assinado do bucket, que abre sem passar por lugar
        nenhum do sistema. O filtro url_protegida devolve sempre a rota interna.
        """
        from processos.templatetags.acadflow import url_protegida

        documento = self._documento(restricao=Documento.RestricaoAcesso.NAO)

        self.assertEqual(
            url_protegida(documento.arquivo),
            reverse("media_file", kwargs={"path": documento.arquivo.name}),
        )

    def test_sem_arquivo_o_filtro_nao_inventa_endereco(self):
        self.assertEqual(url_protegida(None), "")

    def test_todo_campo_de_arquivo_do_sistema_tem_regra_declarada(self):
        """Campo novo nasce protegido, ou nao e servido.

        O registro em _regras_de_arquivo e explicito. Este teste existe para que
        um FileField acrescentado a qualquer modelo apareca aqui como falha, em
        vez de ficar sem regra sem que ninguem note.
        """
        from django.apps import apps

        from processos.views import _regras_de_arquivo

        declarados = {(modelo, campo) for modelo, campo, _ in _regras_de_arquivo()}
        no_sistema = {
            (modelo, campo.name)
            for modelo in apps.get_app_config("processos").get_models()
            for campo in modelo._meta.get_fields()
            if isinstance(campo, models.FileField)
        }

        faltando = sorted(f"{m.__name__}.{c}" for m, c in no_sistema - declarados)
        self.assertEqual(faltando, [], f"campos de arquivo sem regra de acesso: {faltando}")


class ArmazenamentoS3Tests(SimpleTestCase):
    """Configuracao do bucket e entrega por URL assinada.

    O bucket e privado: quem tem a chave e a aplicacao. A URL de leitura e
    assinada e de vida curta, emitida so depois de a view conferir quem esta
    pedindo -- e o que mantem a regra de sigilo valendo tambem do lado do S3.
    """

    PADRAO = dict(bucket="ppgec-documentos", regiao="us-east-1",
                  chave="AKIAEXEMPLO", segredo="segredo", expiracao_da_url=300)

    def _config(self, **ajustes):
        from ppgec.storage import configuracao_s3

        return configuracao_s3(**{**self.PADRAO, **ajustes})

    def test_sem_credencial_a_aplicacao_nao_sobe(self):
        """Falhar na subida, e nao no primeiro upload com o usuario esperando."""
        for faltando in ("chave", "segredo"):
            with self.subTest(vazio=faltando):
                with self.assertRaises(ImproperlyConfigured):
                    self._config(**{faltando: ""})

    def test_toda_url_sai_assinada_e_expira(self):
        """Sem assinatura nao ha leitura: e o que sustenta o bucket privado."""
        opcoes = self._config()["OPTIONS"]

        self.assertTrue(opcoes["querystring_auth"])
        self.assertEqual(opcoes["querystring_expire"], 300)

    def test_o_endereco_assinado_aponta_para_o_host_da_regiao(self):
        """Sem isto, a leitura responde 403 fora de us-east-1.

        O padrao do boto3 monta "bucket.s3.amazonaws.com" -- o host global, que
        e us-east-1 -- e assina para a regiao configurada. A AWS recalcula a
        assinatura com us-east-1, os valores nao batem, e vem
        SignatureDoesNotMatch.

        O defeito e traicoeiro por dois motivos: nao aparece em us-east-1, onde
        global e regional sao o mesmo host, e nao aparece na gravacao, que passa
        pela API e nao pelo endereco assinado. O arquivo sobe, aparece no console
        da AWS, e so quem clica em "Abrir" descobre.

        Medido contra um bucket real em us-east-2: sem a opcao, 403; com ela, o
        arquivo abre.
        """
        for regiao in ("us-east-1", "us-east-2", "sa-east-1"):
            with self.subTest(regiao=regiao):
                opcoes = self._config(regiao=regiao)["OPTIONS"]
                self.assertEqual(opcoes["addressing_style"], "virtual")
                self.assertEqual(opcoes["region_name"], regiao)

    def test_arquivo_de_mesmo_nome_nao_sobrescreve_o_anterior(self):
        """"declaracao.pdf" enviado duas vezes sao dois documentos.

        Com file_overwrite ligado -- o padrao da biblioteca -- o segundo envio
        apagaria o primeiro em silencio, e o registro antigo passaria a apontar
        para o arquivo novo.
        """
        self.assertFalse(self._config()["OPTIONS"]["file_overwrite"])

    def test_nao_manda_acl_no_upload(self):
        """Bucket novo vem com ACL desabilitada e recusa a chamada com ACL."""
        self.assertIsNone(self._config()["OPTIONS"]["default_acl"])

    def test_regiao_vem_de_fora(self):
        self.assertEqual(self._config(regiao="sa-east-1")["OPTIONS"]["region_name"], "sa-east-1")

    def test_a_view_redireciona_para_a_url_assinada(self):
        """No S3 a view nao transmite o arquivo: assina e redireciona.

        Baixar do bucket para reenviar faria todo o trafego passar pela
        aplicacao. O redirecionamento so acontece depois da verificacao, entao
        nao afrouxa a regra: sem passar pela view, nao ha endereco valido.
        """
        from processos.views import _entregar_arquivo

        assinada = "https://ppgec-documentos.s3.amazonaws.com/doc.pdf?X-Amz-Signature=abc"
        with override_settings(USA_S3=True):
            with patch("processos.views.default_storage") as armazenamento:
                armazenamento.url.return_value = assinada
                resposta = _entregar_arquivo(None, "documentos/processos/doc.pdf")

        self.assertEqual(resposta.status_code, 302)
        self.assertEqual(resposta["Location"], assinada)
        armazenamento.url.assert_called_once_with("documentos/processos/doc.pdf")


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
            nome="Docente Matrícula",
        )
        self.aluno = Aluno.objects.create(
            email="aluno.matricula@example.com",
            password="senha-segura-123",
            nome="Aluno Matrícula",
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

    def test_modificacao_registra_eventos_e_reinclui_item_cancelado(self):
        oferta = self.criar_oferta("MAT004", "Auditoria")
        solicitacao = salvar_solicitacao_matricula(
            aluno=self.aluno,
            periodo=self.periodo,
            tipo_aluno=SolicitacaoMatricula.TipoAluno.REGULAR,
            ofertas=[oferta],
        )
        item = solicitacao.itens.get(oferta=oferta)
        self.assertEqual(item.incluido_na_fase, ItemSolicitacaoMatricula.FaseInclusao.MATRICULA)

        self.periodo.status = PeriodoLetivo.Status.MODIFICACAO_MATRICULA
        self.periodo.save(update_fields=["status"])
        cancelar_item_matricula(item=item, usuario=self.secretaria)
        salvar_solicitacao_matricula(
            aluno=self.aluno,
            periodo=self.periodo,
            tipo_aluno=SolicitacaoMatricula.TipoAluno.REGULAR,
            ofertas=[oferta],
        )

        item.refresh_from_db()
        self.assertEqual(item.status, ItemSolicitacaoMatricula.Status.SOLICITADO)
        self.assertEqual(item.incluido_na_fase, ItemSolicitacaoMatricula.FaseInclusao.MATRICULA)
        eventos = list(solicitacao.alteracoes.values_list("acao", "fase", flat=False))
        self.assertIn(
            (AlteracaoMatricula.Acao.DISCIPLINA_CANCELADA, AlteracaoMatricula.Fase.MODIFICACAO),
            eventos,
        )
        self.assertIn(
            (AlteracaoMatricula.Acao.DISCIPLINA_REINCLUIDA, AlteracaoMatricula.Fase.MODIFICACAO),
            eventos,
        )

    def test_modificacao_remove_e_adiciona_disciplinas(self):
        oferta_original = self.criar_oferta("MAT005", "Original", time(8, 0), time(10, 0))
        oferta_nova = self.criar_oferta("MAT006", "Nova", time(10, 0), time(12, 0))
        solicitacao = salvar_solicitacao_matricula(
            aluno=self.aluno,
            periodo=self.periodo,
            tipo_aluno=SolicitacaoMatricula.TipoAluno.REGULAR,
            ofertas=[oferta_original],
        )
        self.periodo.status = PeriodoLetivo.Status.MODIFICACAO_MATRICULA
        self.periodo.save(update_fields=["status"])

        salvar_solicitacao_matricula(
            aluno=self.aluno,
            periodo=self.periodo,
            tipo_aluno=SolicitacaoMatricula.TipoAluno.REGULAR,
            ofertas=[oferta_nova],
        )

        self.assertEqual(
            solicitacao.itens.get(oferta=oferta_original).status,
            ItemSolicitacaoMatricula.Status.CANCELADO,
        )
        self.assertEqual(
            solicitacao.itens.get(oferta=oferta_nova).status,
            ItemSolicitacaoMatricula.Status.SOLICITADO,
        )

    def test_modificacao_sem_disciplinas_converte_para_matricula_vinculo(self):
        oferta = self.criar_oferta("MAT007", "Vínculo", time(8, 0), time(10, 0))
        solicitacao = salvar_solicitacao_matricula(
            aluno=self.aluno,
            periodo=self.periodo,
            tipo_aluno=SolicitacaoMatricula.TipoAluno.REGULAR,
            ofertas=[oferta],
        )
        self.periodo.status = PeriodoLetivo.Status.MODIFICACAO_MATRICULA
        self.periodo.save(update_fields=["status"])

        salvar_solicitacao_matricula(
            aluno=self.aluno,
            periodo=self.periodo,
            tipo_aluno=SolicitacaoMatricula.TipoAluno.REGULAR,
            ofertas=[],
            tipo_matricula=SolicitacaoMatricula.TipoMatricula.VINCULO,
        )

        solicitacao.refresh_from_db()
        self.assertEqual(solicitacao.tipo_matricula, SolicitacaoMatricula.TipoMatricula.VINCULO)
        self.assertEqual(solicitacao.status, SolicitacaoMatricula.Status.SOLICITADA)
        self.assertEqual(solicitacao.itens.get().status, ItemSolicitacaoMatricula.Status.CANCELADO)

    def test_modificacao_bloqueia_aluno_sem_solicitacao_no_prazo(self):
        oferta = self.criar_oferta("MAT008", "Fora do prazo", time(8, 0), time(10, 0))
        self.periodo.status = PeriodoLetivo.Status.MODIFICACAO_MATRICULA
        self.periodo.save(update_fields=["status"])

        with self.assertRaisesMessage(ValidationError, "enviou a solicitação no prazo"):
            salvar_solicitacao_matricula(
                aluno=self.aluno,
                periodo=self.periodo,
                tipo_aluno=SolicitacaoMatricula.TipoAluno.REGULAR,
                ofertas=[oferta],
            )

    def test_modificacao_permite_matricula_vinculo_sem_solicitacao_no_prazo(self):
        self.periodo.status = PeriodoLetivo.Status.MODIFICACAO_MATRICULA
        self.periodo.save(update_fields=["status"])

        solicitacao = salvar_solicitacao_matricula(
            aluno=self.aluno,
            periodo=self.periodo,
            tipo_aluno=SolicitacaoMatricula.TipoAluno.REGULAR,
            ofertas=[],
            tipo_matricula=SolicitacaoMatricula.TipoMatricula.VINCULO,
            observacao="Manter vínculo durante a modificação.",
        )

        self.assertEqual(solicitacao.tipo_matricula, SolicitacaoMatricula.TipoMatricula.VINCULO)
        self.assertEqual(solicitacao.status, SolicitacaoMatricula.Status.SOLICITADA)
        self.assertEqual(solicitacao.itens.count(), 0)
        self.assertTrue(
            solicitacao.alteracoes.filter(
                acao=AlteracaoMatricula.Acao.MATRICULA_VINCULO_SOLICITADA,
                fase=AlteracaoMatricula.Fase.MODIFICACAO,
            ).exists()
        )

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
            nome="Aluno Sem Matrícula",
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
        # A linha separava os campos por pipe, inclusive dentro dos parenteses
        # ("(regulares: 1 | especiais: 1)"), o que tornava a contagem ilegivel.
        # Os numeros conferidos aqui sao os mesmos; muda so como sao escritos.
        self.assertContains(response, "Solicitadas: 2")
        self.assertContains(response, "Espera: 2")
        self.assertContains(response, "1 regulares, 1 especiais", count=2)

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

        response = self.client.get(
            reverse("matriculas_solicitacoes"),
            {"periodo": self.periodo.pk, "disciplina": self.disciplina.pk},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Solicitações de Matrícula")
        self.assertContains(response, self.aluno.nome)
        self.assertContains(response, self.disciplina.nome)
        self.assertNotContains(response, "Homologar")
        self.assertContains(response, "Indeferir")
        # O resumo virou grade rotulada: o rotulo esta no <dt> e os numeros no
        # <dd>, em vez de uma frase com pipes separando os pares.
        self.assertContains(response, "Matrículas solicitadas")
        self.assertContains(response, "1 regulares")
        self.assertContains(response, "Lista de espera")
        self.assertContains(response, "0 regulares")

        response = self.client.post(
            reverse("matriculas_solicitacoes"),
            {
                "acao": "indeferir_item",
                "periodo_id": self.periodo.pk,
                "disciplina": self.disciplina.pk,
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

    def test_gestao_visualiza_alunos_com_matricula_vinculo_nas_solicitacoes(self):
        aluno_vinculo = Aluno.objects.create(
            email="aluno.vinculo.solicitacoes@example.com",
            password="senha-segura-123",
            nome="Aluno com Matrícula Vínculo",
            matricula="202600123",
        )
        solicitacao_vinculo = SolicitacaoMatricula.objects.create(
            periodo=self.periodo,
            aluno=aluno_vinculo,
            tipo_matricula=SolicitacaoMatricula.TipoMatricula.VINCULO,
            status=SolicitacaoMatricula.Status.SOLICITADA,
            observacao_aluno="Manutenção de vínculo no semestre.",
            solicitada_em=timezone.now(),
        )
        self.client.force_login(self.secretaria)

        response = self.client.get(
            reverse("matriculas_solicitacoes"),
            {"periodo": self.periodo.pk, "disciplina": "vinculo"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Matrículas vínculo")
        self.assertContains(response, aluno_vinculo.nome)
        self.assertContains(response, aluno_vinculo.email)
        self.assertContains(response, aluno_vinculo.matricula)
        self.assertContains(response, solicitacao_vinculo.observacao_aluno)
        self.assertContains(response, reverse("matricula_minha_solicitacao", args=[solicitacao_vinculo.pk]))

    def test_solicitacoes_sem_disciplina_nao_carrega_lista_de_alunos(self):
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

        response = self.client.get(reverse("matriculas_solicitacoes"), {"periodo": self.periodo.pk})

        self.assertContains(response, "Selecione uma disciplina")
        self.assertContains(response, self.disciplina.nome)
        self.assertNotContains(response, self.aluno.email)

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
        self.assertContains(response, 'id="periodo"')
        self.assertContains(response, 'addEventListener("change"')
        self.assertNotContains(response, "Alterar período")
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
            nome="Aluno Sem Trajetória",
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

    def test_exportacao_xlsx_de_todas_as_disciplinas_inclui_aba_de_vinculo(self):
        polo = Polo.objects.create(nome="Polo Exportação")
        self.aluno.polo_atuacao = polo
        self.aluno.save(update_fields=["polo_atuacao"])
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
        aluno_vinculo = Aluno.objects.create(
            email="exportacao.vinculo@example.com",
            password="senha-segura-123",
            nome="Aluno Exportação Vínculo",
            polo_atuacao=polo,
        )
        SolicitacaoMatricula.objects.create(
            periodo=self.periodo,
            aluno=aluno_vinculo,
            tipo_matricula=SolicitacaoMatricula.TipoMatricula.VINCULO,
            status=SolicitacaoMatricula.Status.SOLICITADA,
        )
        self.client.force_login(self.secretaria)

        response = self.client.get(
            reverse("matriculas_solicitacoes_exportar"),
            {"periodo": self.periodo.pk},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        with ZipFile(BytesIO(response.content)) as xlsx:
            workbook = xlsx.read("xl/workbook.xml").decode()
            planilhas = [
                xlsx.read(nome).decode()
                for nome in xlsx.namelist()
                if nome.startswith("xl/worksheets/sheet")
            ]
        self.assertIn(self.disciplina.nome, workbook)
        self.assertIn("Matrícula vínculo", workbook)
        self.assertTrue(all("Polo" in planilha and "E-mail" in planilha and "Nível" in planilha for planilha in planilhas))
        self.assertTrue(any(self.aluno.nome in planilha for planilha in planilhas))
        self.assertTrue(any("Mestrado" in planilha for planilha in planilhas))
        self.assertTrue(any(self.aluno.email in planilha and polo.nome in planilha for planilha in planilhas))
        self.assertTrue(any(aluno_vinculo.nome in planilha for planilha in planilhas))
        self.assertTrue(any(aluno_vinculo.email in planilha and polo.nome in planilha for planilha in planilhas))

    def test_exportacoes_separam_originais_modificacoes_e_consolidada(self):
        solicitacao = salvar_solicitacao_matricula(
            aluno=self.aluno,
            periodo=self.periodo,
            tipo_aluno=SolicitacaoMatricula.TipoAluno.REGULAR,
            ofertas=[self.oferta],
        )
        self.periodo.status = PeriodoLetivo.Status.MODIFICACAO_MATRICULA
        self.periodo.save(update_fields=["status"])
        cancelar_item_matricula(item=solicitacao.itens.get(), usuario=self.secretaria)
        self.client.force_login(self.secretaria)

        originais = self.client.get(
            reverse("matriculas_solicitacoes_exportar"),
            {"periodo": self.periodo.pk, "estado": "originais"},
        )
        modificacoes = self.client.get(
            reverse("matriculas_solicitacoes_exportar"),
            {"periodo": self.periodo.pk, "estado": "modificacoes"},
        )
        consolidada = self.client.get(
            reverse("matriculas_solicitacoes_exportar"),
            {"periodo": self.periodo.pk, "estado": "consolidada"},
        )

        def xml_planilhas(response):
            with ZipFile(BytesIO(response.content)) as xlsx:
                return "".join(
                    xlsx.read(nome).decode()
                    for nome in xlsx.namelist()
                    if nome.startswith("xl/worksheets/sheet")
                )

        self.assertIn(self.aluno.nome, xml_planilhas(originais))
        self.assertIn("Mestrado", xml_planilhas(originais))
        self.assertIn("Disciplina cancelada", xml_planilhas(modificacoes))
        self.assertIn("Nível", xml_planilhas(modificacoes))
        self.assertIn("Mestrado", xml_planilhas(modificacoes))
        self.assertNotIn(self.aluno.nome, xml_planilhas(consolidada))

    @patch("processos.views.send_email_secretaria_planejamento_presencial.delay")
    def test_planejamento_presencial_cria_reserva_para_oferta_hibrida(self, mock_email):
        self.oferta.modalidade = OfertaDisciplina.Modalidade.HIBRIDA
        self.oferta.save(update_fields=["modalidade"])
        polo = Polo.objects.create(nome="Polo Matrícula")
        sala = Sala.objects.create(polo=polo, nome="Sala Híbrida", capacidade=30)
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
        # A reserva e gravada em UTC; compara no fuso local, como faz a aplicacao.
        self.assertEqual(timezone.localtime(aula.reserva.inicio).time(), time(10, 0))
        self.assertEqual(timezone.localtime(aula.reserva.fim).time(), time(12, 30))
        mock_email.assert_called_once_with(self.oferta.pk, self.docente.pk)

    @patch("processos.views.send_email_alunos_sem_matricula.delay")
    def test_gestao_lista_alunos_sem_matricula_e_envia_email(self, mock_email):
        aluno_sem_matricula = Aluno.objects.create(
            email="pendente.matricula@example.com",
            password="senha-segura-123",
            nome="Aluno Pendente Matrícula",
        )
        criar_trajetoria(aluno_sem_matricula)
        self.client.force_login(self.secretaria)
        total_pendentes = alunos_ativos_sem_matricula(self.periodo).count()

        response = self.client.get(reverse("matriculas_periodos"))
        self.assertContains(response, f"{total_pendentes} alunos sem matrícula")
        self.assertContains(response, "Aluno Pendente Matrícula")

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

    def test_sem_disciplinas_exige_selecao_explicita_de_matricula_vinculo(self):
        self.client.force_login(self.aluno)

        response = self.client.post(
            reverse("matricula_solicitar_periodo", args=[self.periodo.pk]),
            {
                "periodo_id": self.periodo.pk,
                "aceitar_lista_espera": "on",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Selecione ao menos uma disciplina ou marque matrícula vínculo.")
        self.assertFalse(SolicitacaoMatricula.objects.filter(aluno=self.aluno, periodo=self.periodo).exists())

    def test_modificacao_exibe_disciplinas_ativas_previamente_selecionadas(self):
        salvar_solicitacao_matricula(
            aluno=self.aluno,
            periodo=self.periodo,
            tipo_aluno=SolicitacaoMatricula.TipoAluno.REGULAR,
            ofertas=[self.oferta],
        )
        self.periodo.status = PeriodoLetivo.Status.MODIFICACAO_MATRICULA
        self.periodo.save(update_fields=["status"])
        self.client.force_login(self.aluno)

        response = self.client.get(reverse("matricula_solicitar_periodo", args=[self.periodo.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Modificar matrícula")
        self.assertContains(
            response,
            f'name="ofertas" value="{self.oferta.pk}"',
            html=False,
        )
        self.assertContains(response, "checked", html=False)

    def test_modificacao_exibe_apenas_matricula_vinculo_para_aluno_sem_solicitacao_original(self):
        self.periodo.status = PeriodoLetivo.Status.MODIFICACAO_MATRICULA
        self.periodo.save(update_fields=["status"])
        self.client.force_login(self.aluno)

        response = self.client.get(reverse("matricula_solicitar_periodo", args=[self.periodo.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Solicitar matrícula vínculo")
        self.assertContains(response, "não houve solicitação no prazo regular")
        self.assertNotContains(
            response,
            f'name="ofertas" value="{self.oferta.pk}"',
            html=False,
        )
        self.assertContains(response, 'name="matricula_vinculo"', html=False)
        self.assertContains(response, "checked", html=False)

        post = self.client.post(
            reverse("matricula_solicitar_periodo", args=[self.periodo.pk]),
            {
                "periodo_id": self.periodo.pk,
                "matricula_vinculo": "on",
                "aceitar_lista_espera": "on",
                "observacao": "Solicitação feita na modificação.",
            },
        )

        self.assertEqual(post.status_code, 302)
        solicitacao = SolicitacaoMatricula.objects.get(aluno=self.aluno, periodo=self.periodo)
        self.assertEqual(solicitacao.tipo_matricula, SolicitacaoMatricula.TipoMatricula.VINCULO)

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
            nome="Aluno Especial Matrícula",
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
        criar_trajetoria(
            aluno_posdoc,
            nivel_curso=Aluno.NivelCurso.POSDOUTORADO,
            orientador=self.docente,
        )
        self.client.force_login(aluno_posdoc)

        response = self.client.get(reverse("matricula_solicitar_periodo", args=[self.periodo.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Alunos de Pós-Doutorado não realizam matrícula")
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

    def test_cadastros_pendentes_sao_paginados_filtrados_por_polo_e_contados(self):
        polo_a = Polo.objects.create(nome="Polo Aprovação A")
        polo_b = Polo.objects.create(nome="Polo Aprovação B")
        for indice in range(21):
            Aluno.objects.create_user(
                email=f"pendente.paginado.{indice}@example.com",
                password="senha-segura-123",
                nome=f"Aluno Pendente {indice:02d}",
                status_aluno=Aluno.StatusAluno.EM_AVALIACAO,
                polo_atuacao=polo_a if indice < 20 else polo_b,
            )
        self.client.force_login(self.servidor)

        response = self.client.get(reverse("validar_cadastros_alunos"))
        self.assertEqual(response.context["total_pendentes"], 21)
        self.assertEqual(len(response.context["alunos_pendentes"]), 20)
        self.assertContains(response, "alunos aguardando aprovação")
        self.assertContains(response, "Página 1 de 2")

        response = self.client.get(reverse("validar_cadastros_alunos"), {"polo": polo_b.pk})
        self.assertEqual(response.context["total_filtrado"], 1)
        self.assertContains(response, "Aluno Pendente 20")
        self.assertNotContains(response, "Aluno Pendente 00")

    def test_aprova_cadastro_na_pagina_do_aluno_e_conclui_aluno_especial(self):
        polo = Polo.objects.create(nome="Polo Cadastro Individual")
        aluno_pendente = Aluno.objects.create_user(
            email="especial.pendente@example.com",
            password="senha-segura-123",
            nome="Aluno Especial Pendente",
            status_aluno=Aluno.StatusAluno.EM_AVALIACAO,
            polo_atuacao=polo,
            cpf="52998224725",
            genero=Aluno.Genero.NAO_BINARIO,
            sexo_atribuido_nascimento=Aluno.SexoAtribuidoNascimento.FEMININO,
        )
        trajetoria = criar_trajetoria(
            aluno_pendente,
            nivel_curso=Aluno.NivelCurso.ALUNO_ESPECIAL,
            status=TrajetoriaAcademica.Status.EM_HOMOLOGACAO,
        )
        self.client.force_login(self.servidor)
        url = reverse("aluno_detalhe", args=[aluno_pendente.pk])

        response = self.client.get(url)
        self.assertContains(response, "Aprovar cadastro")
        self.assertNotContains(response, '<p class="aluno-kv"><strong>CPF:</strong>', html=False)
        self.assertNotContains(response, '<p class="aluno-kv"><strong>Gênero:</strong>', html=False)
        self.assertContains(response, 'name="cpf"')
        self.assertContains(response, 'name="genero"')
        self.assertContains(response, 'name="sexo_atribuido_nascimento"')
        self.assertContains(response, 'name="polo_atuacao"')

        response = self.client.post(url, {"acao": "aprovar_cadastro"})
        self.assertRedirects(response, url)
        aluno_pendente.refresh_from_db()
        trajetoria.refresh_from_db()
        self.assertEqual(aluno_pendente.status_aluno, Aluno.StatusAluno.ATIVO)
        self.assertEqual(trajetoria.status, TrajetoriaAcademica.Status.CONCLUIDA)

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

    def test_coordenador_acessa_validacao_e_ve_menu_de_cadastro(self):
        self.client.force_login(self.coordenador)

        response = self.client.get(reverse("validar_cadastros_alunos"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Cadastro de ingressantes")
        self.assertContains(response, reverse("importar_ingressantes"))

    def test_importacao_csv_cadastra_e_resume_linhas_duplicadas(self):
        existente = Aluno.objects.create_user(
            email="existente.importacao@example.com",
            password=None,
            nome="Aluno Já Existente",
            cpf="11144477735",
        )
        criar_trajetoria(existente, ingresso="2026.2")
        arquivo = SimpleUploadedFile(
            "ingressantes.csv",
            (
                "nome;cpf;e-mail;orientador\n"
                "Nova Ingressante;52998224725;nova.ingressante@example.com;Orientador\n"
                "Aluno Já Existente;11144477735;outro@example.com;Orientador\n"
            ).encode("utf-8"),
            content_type="text/csv",
        )
        self.client.force_login(self.coordenador)

        response = self.client.post(
            reverse("importar_ingressantes"),
            {"arquivo": arquivo, "nivel_curso": Aluno.NivelCurso.MESTRADO, "ingresso": "2026.2"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Nova Ingressante")
        self.assertContains(response, "O aluno já possui cadastro")
        nova = Aluno.objects.get(email="nova.ingressante@example.com")
        self.assertFalse(nova.has_usable_password())
        self.assertTrue(
            nova.trajetorias.filter(
                nivel_curso=Aluno.NivelCurso.MESTRADO,
                ingresso="2026.2",
                orientador=self.docente,
            ).exists()
        )

    def test_importacao_rejeita_nome_com_mesma_trajetoria_mesmo_sem_cpf_igual(self):
        existente = Aluno.objects.create_user(
            email="nome.duplicado@example.com",
            password=None,
            nome="Nome Duplicado",
            cpf="11144477735",
        )
        criar_trajetoria(existente, ingresso="2026.2")
        arquivo = SimpleUploadedFile(
            "ingressantes.csv",
            (
                "nome,cpf,e-mail,orientador\n"
                "Nome Duplicado,52998224725,novo.cpf@example.com,Orientador\n"
            ).encode("utf-8"),
            content_type="text/csv",
        )
        self.client.force_login(self.servidor)

        response = self.client.post(
            reverse("importar_ingressantes"),
            {"arquivo": arquivo, "nivel_curso": Aluno.NivelCurso.DOUTORADO, "ingresso": "2026.2"},
        )

        self.assertContains(response, "O aluno já possui cadastro")
        self.assertFalse(Aluno.objects.filter(email="novo.cpf@example.com").exists())

    def test_coordenador_cria_comissao_com_docente_e_aluno(self):
        self.client.force_login(self.coordenador)
        response = self.client.post(
            reverse("criar_comissao"),
            {
                "nome": "Comissão de Bolsas",
                "descricao": "Análise de bolsas",
                "email": "bolsas@example.com",
                "ativo": "on",
                "docentes": [self.docente.id],
                "alunos": [self.aluno.id],
            },
        )

        self.assertEqual(response.status_code, 302)
        setor = Setor.objects.get(nome="Comissão de Bolsas")
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
        setor = Setor.objects.create(nome="Comissão Editavel", tipo=Setor.TipoSetor.COMISSAO)
        SetorMembro.objects.create(setor=setor, usuario=self.docente, designado_por=self.coordenador)

        self.client.force_login(self.coordenador)
        get_response = self.client.get(reverse("setores_comissoes"), {"editar": setor.id})
        self.assertEqual(get_response.status_code, 200)
        self.assertContains(get_response, "Editar setor/comissão")

        post_response = self.client.post(
            reverse("setores_comissoes"),
            {
                "setor_id": setor.id,
                "nome": "Comissão Editada",
                "descricao": "Atualizada",
                "email": "",
                "ativo": "on",
                "docentes": [self.docente.id],
                "servidores": [self.servidor.id],
            },
        )
        self.assertEqual(post_response.status_code, 302)
        setor.refresh_from_db()
        self.assertEqual(setor.nome, "Comissão Editada")
        self.assertTrue(SetorMembro.objects.filter(setor=setor, usuario=self.servidor, data_saida__isnull=True).exists())

    def test_servidor_visualiza_setores_sem_acoes_de_edicao(self):
        setor = Setor.objects.create(nome="Comissão Visivel", tipo=Setor.TipoSetor.COMISSAO)
        SetorMembro.objects.create(setor=setor, usuario=self.docente, designado_por=self.coordenador)

        self.client.force_login(self.servidor)
        response = self.client.get(reverse("setores_comissoes"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Comissão Visivel")
        self.assertNotContains(response, "Membros alunos")
        self.assertNotContains(response, "Editar</a>", html=False)
        self.assertNotContains(response, "Encerrar</button>", html=False)

    def test_servidor_nao_altera_setores(self):
        self.client.force_login(self.servidor)
        response = self.client.post(
            reverse("setores_comissoes"),
            {
                "nome": "Comissão Indevida",
                "ativo": "on",
            },
        )

        self.assertEqual(response.status_code, 403)
        self.assertFalse(Setor.objects.filter(nome="Comissão Indevida").exists())

    def test_servidor_nao_acessa_criacao_de_comissao(self):
        self.client.force_login(self.servidor)
        response = self.client.get(reverse("criar_comissao"))

        self.assertEqual(response.status_code, 403)

    def test_membro_de_setor_acessa_caixa_e_detalhe_do_setor(self):
        setor = Setor.objects.create(nome="Comissão de Recursos", tipo=Setor.TipoSetor.COMISSAO)
        membro = Docente.objects.create(
            email="membro.comissao@example.com",
            password="senha-segura-123",
            nome="Membro Comissão",
        )
        SetorMembro.objects.create(setor=setor, usuario=membro, designado_por=self.coordenador)
        processo = Processo.objects.create(
            usuario_criado_por=self.aluno,
            tipo=Processo.TipoProcesso.OUTRO,
            assunto="Processo da comissão",
            descricao="Análise pela comissão",
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
            nome=Setor.NOME_SECRETARIA,
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

        processo_de_outro_usuario = Processo.objects.create(
            usuario_criado_por=self.aluno,
            tipo=Processo.TipoProcesso.OUTRO,
            assunto="Processo acessível pela Secretaria",
            descricao="Criado por outro usuário",
            setor_atual=secretaria,
        )
        processos = self.client.get(reverse("coordenacao_processos"))
        self.assertEqual(processos.status_code, 200)
        self.assertContains(processos, processo_de_outro_usuario.assunto)

        detalhe = self.client.get(reverse("processo_detalhe", args=[processo_de_outro_usuario.id]))
        self.assertEqual(detalhe.status_code, 200)
        self.assertContains(detalhe, processo_de_outro_usuario.assunto)

    def test_todo_membro_ativo_da_coordenacao_tem_menu_e_acesso_de_gestao(self):
        coordenacao, _ = Setor.objects.get_or_create(
            nome=Setor.NOME_COORDENACAO,
            defaults={"tipo": Setor.TipoSetor.SETOR},
        )
        membro = Aluno.objects.create(
            email="membro.coordenacao@example.com",
            password="senha-segura-123",
            nome="Membro Coordenação",
        )
        criar_trajetoria(membro)
        SetorMembro.objects.create(setor=coordenacao, usuario=membro, designado_por=self.coordenador)

        self.client.force_login(membro)

        home = self.client.get(reverse("home"))
        self.assertEqual(home.status_code, 200)
        self.assertContains(home, "Dashboard")
        self.assertContains(home, "Validar Cadastros")
        self.assertContains(home, "Períodos letivos")
        self.assertContains(home, "Cadastro de Salas")
        self.assertContains(home, "Reserva de Ambiente")

        for rota in (
            "coordenacao_dashboard",
            "coordenacao_alunos",
            "coordenacao_processos",
            "matriculas_periodos",
            "setores_comissoes",
            "reservas_ambientes",
        ):
            with self.subTest(rota=rota):
                self.assertEqual(self.client.get(reverse(rota)).status_code, 200)

    def test_vinculo_encerrado_com_coordenacao_nao_concede_acesso_de_gestao(self):
        coordenacao, _ = Setor.objects.get_or_create(nome=Setor.NOME_COORDENACAO)
        SetorMembro.objects.create(
            setor=coordenacao,
            usuario=self.aluno,
            designado_por=self.coordenador,
            data_saida=timezone.localdate(),
        )

        self.client.force_login(self.aluno)
        home = self.client.get(reverse("home"))
        self.assertNotContains(home, "Dashboard")
        self.assertEqual(self.client.get(reverse("coordenacao_dashboard")).status_code, 403)

    def test_aluno_membro_de_comissao_nao_recebe_acesso_global_de_secretaria(self):
        setor = Setor.objects.create(nome="Comissão Discente Sem Gestão", tipo=Setor.TipoSetor.COMISSAO)
        SetorMembro.objects.create(setor=setor, usuario=self.aluno, designado_por=self.coordenador)

        self.client.force_login(self.aluno)

        response = self.client.get(reverse("coordenacao_alunos"))

        self.assertEqual(response.status_code, 403)

    def test_aluno_nao_acessa_detalhe_de_processo_que_nao_criou(self):
        setor = Setor.objects.create(nome="Comissão Discente", tipo=Setor.TipoSetor.COMISSAO)
        SetorMembro.objects.create(setor=setor, usuario=self.aluno, designado_por=self.coordenador)
        processo = Processo.objects.create(
            usuario_criado_por=self.docente,
            tipo=Processo.TipoProcesso.OUTRO,
            assunto="Processo de outro usuário",
            descricao="Aluno membro não deve visualizar",
            setor_atual=setor,
        )

        self.client.force_login(self.aluno)
        response = self.client.get(reverse("processo_detalhe", args=[processo.id]))

        self.assertEqual(response.status_code, 403)

    def test_historico_exibe_tramitacoes_da_mais_recente_para_a_mais_antiga(self):
        secretaria = Setor.objects.create(nome="Setor Histórico Secretaria")
        coordenacao = Setor.objects.create(nome="Setor Histórico Coordenação")
        pleno = Setor.objects.create(nome="Setor Histórico Pleno")
        processo = Processo.objects.create(
            usuario_criado_por=self.aluno,
            tipo=Processo.TipoProcesso.OUTRO,
            assunto="Processo com histórico",
            descricao="Ordem de tramitações",
            setor_atual=pleno,
        )
        antiga = TramitacaoProcesso.objects.create(
            processo=processo,
            setor_origem=secretaria,
            setor_destino=coordenacao,
            encaminhado_por=self.servidor,
            observacao="Tramitação antiga",
        )
        recente = TramitacaoProcesso.objects.create(
            processo=processo,
            setor_origem=coordenacao,
            setor_destino=pleno,
            encaminhado_por=self.coordenador,
            observacao="Tramitação recente",
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
        self.assertLess(conteudo.index("Tramitação recente"), conteudo.index("Tramitação antiga"))

    def test_perfil_exibe_participacoes_ativas_e_historico(self):
        setor_ativo = Setor.objects.create(nome="Comissão Ativa", tipo=Setor.TipoSetor.COMISSAO)
        setor_encerrado = Setor.objects.create(nome="Comissão Encerrada", tipo=Setor.TipoSetor.COMISSAO)
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
        self.assertContains(response, "Comissão Ativa")
        self.assertContains(response, "Histórico de participação")
        self.assertContains(response, "Comissão Encerrada")

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

    def test_filtra_alunos_com_trajetoria_ativa_sem_matricula_no_periodo(self):
        hoje = timezone.localdate()
        periodo = PeriodoLetivo.objects.create(
            nome="2026.2",
            prazo_cadastro_disciplinas=hoje - timedelta(days=30),
            matricula_inicio=hoje - timedelta(days=29),
            matricula_fim=hoje - timedelta(days=25),
            modificacao_inicio=hoje - timedelta(days=24),
            modificacao_fim=hoje - timedelta(days=20),
            criado_por=self.servidor,
        )
        aluno_sem_matricula = Aluno.objects.create(
            email="ativo.sem.matricula@example.com",
            password="senha-segura-123",
            nome="Ativo Sem Matrícula 2026.2",
        )
        criar_trajetoria(aluno_sem_matricula)
        aluno_com_matricula = Aluno.objects.create(
            email="ativo.com.matricula@example.com",
            password="senha-segura-123",
            nome="Ativo Com Matrícula 2026.2",
        )
        criar_trajetoria(aluno_com_matricula)
        SolicitacaoMatricula.objects.create(
            periodo=periodo,
            aluno=aluno_com_matricula,
            tipo_matricula=SolicitacaoMatricula.TipoMatricula.VINCULO,
            status=SolicitacaoMatricula.Status.SOLICITADA,
        )
        self.client.force_login(self.servidor)

        response = self.client.get(
            reverse("coordenacao_alunos"),
            {"sem_matricula_periodo": periodo.pk},
        )

        self.assertEqual(response.status_code, 200)
        # O rotulo encurtou junto com a barra de filtros: acima de um select que
        # lista periodos, "Sem matrícula em" + "2026.2" diz o mesmo que "Sem
        # matrícula no período" + "2026.2" sem repetir a palavra periodo.
        self.assertContains(response, "Sem matrícula em")
        self.assertContains(response, "trajetória ativa e sem matrícula registrada em")
        self.assertContains(response, "Ativo Sem Matrícula 2026.2")
        self.assertNotContains(response, "Ativo Com Matrícula 2026.2")

    def test_docente_nao_coordenador_nao_tem_acesso(self):
        self.client.force_login(self.docente)
        response = self.client.get(reverse("coordenacao_alunos"))
        self.assertEqual(response.status_code, 403)

    def test_aluno_detalhe_exibe_processos(self):
        processo = Processo.objects.create(
            usuario_criado_por=self.aluno,
            tipo=Processo.TipoProcesso.QUALIFICACAO_DOUTORADO,
            assunto="Exame de qualificação",
            descricao="Solicitação de banca",
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
            titulo="Título antigo",
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
                "titulo": "Título atualizado",
                "tipo": PublicacaoTrajetoria.TipoPublicacao.ARTIGO_PERIODICO,
                "autores": "Aluno Teste",
                "veiculo": "Revista PPGEC",
                "ano": "2026",
                "doi_url": "",
            },
        )

        self.assertEqual(response.status_code, 302)
        publicacao.refresh_from_db()
        self.assertEqual(publicacao.titulo, "Título atualizado")
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

    def test_lista_alunos_filtra_por_polo(self):
        polo_alvo = Polo.objects.create(nome="Polo da listagem")
        polo_outro = Polo.objects.create(nome="Outro polo da listagem")
        self.aluno.polo_atuacao = polo_alvo
        self.aluno.save(update_fields=["polo_atuacao"])
        aluno_outro = Aluno.objects.create_user(
            email="aluno.outro.polo@example.com",
            password="senha-segura-123",
            nome="Aluno de Outro Polo",
            polo_atuacao=polo_outro,
        )
        criar_trajetoria(aluno_outro)

        self.client.force_login(self.servidor)
        response = self.client.get(reverse("coordenacao_alunos"), {"polo": polo_alvo.pk})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.aluno.nome)
        self.assertContains(response, polo_alvo.nome)
        self.assertNotContains(response, aluno_outro.nome)
        self.assertEqual(response.context["filtro_polo"], str(polo_alvo.pk))

    def test_lista_alunos_usa_ultima_conclusao_sem_trajetoria_ativa(self):
        aluno_concluido = Aluno.objects.create(
            email="aluno.concluido@example.com",
            password="senha-segura-123",
            nome="Aluno Concluído",
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
        # A listagem virou tabela: os rotulos ficam no cabecalho das colunas e a
        # linha carrega so os valores. O que o teste prova continua o mesmo --
        # sem trajetoria ativa, os dados vem da ultima concluida.
        self.assertContains(response, "Aluno Concluído")
        self.assertContains(response, "2025A0002")
        self.assertContains(response, "Doutorado")
        self.assertContains(response, "2025.1")
        self.assertContains(response, "Concluída")
        # dados de outras secoes nao vazam para a listagem
        self.assertNotContains(response, "Prazo defesa")
        self.assertNotContains(response, "Qualifica")
        self.assertNotContains(response, "Coorientador:")

    def test_dashboard_exibe_apenas_trajetorias_ativas(self):
        aluno_concluido = Aluno.objects.create(
            email="aluno.dashboard.concluido@example.com",
            password="senha-segura-123",
            nome="Aluno Dashboard Concluído",
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

    def test_dashboard_separa_supervisao_de_pos_doutorado_das_orientacoes(self):
        posdoc = Aluno.objects.create(
            email="posdoc.dashboard@example.com",
            password="senha-segura-123",
            nome="Pesquisadora Pós-Doutorado",
        )
        trajetoria = criar_trajetoria(
            posdoc,
            nivel_curso=Aluno.NivelCurso.POSDOUTORADO,
            orientador=self.docente,
        )

        self.client.force_login(self.servidor)
        response = self.client.get(reverse("coordenacao_dashboard"))

        docente = next(item for item in response.context["docentes"] if item.pk == self.docente.pk)
        self.assertEqual(docente.total_orientandos, 1)
        self.assertEqual(docente.total_supervisoes, 1)
        self.assertIn(trajetoria, docente.trajetorias_supervisionadas_ativas)
        self.assertNotIn(trajetoria, docente.trajetorias_orientadas_ativas)
        self.assertContains(response, "1 supervisão")
        self.assertContains(response, posdoc.nome)

    def test_ficha_pos_doutorado_exibe_supervisor_e_oculta_horas_complementares(self):
        posdoc = Aluno.objects.create(
            email="posdoc.ficha@example.com",
            password="senha-segura-123",
            nome="Pesquisador Pós-Doutorado",
        )
        criar_trajetoria(
            posdoc,
            nivel_curso=Aluno.NivelCurso.POSDOUTORADO,
            orientador=self.docente,
        )
        self.client.force_login(self.servidor)

        response = self.client.get(reverse("aluno_detalhe", args=[posdoc.pk]))

        self.assertContains(response, "Pós-Doutorado")
        self.assertContains(response, "Supervisor")
        self.assertContains(response, self.docente.nome)
        self.assertNotContains(response, "Horas complementares")
        self.assertNotContains(response, "Adicionar lançamento")

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
            nome="Aluno Vínculo Concluído",
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
        self.assertContains(response, "Orientações ativas")
        self.assertContains(response, "Coorientações")
        self.assertContains(response, "Concluídas")
        self.assertContains(response, self.aluno.nome)
        self.assertContains(response, aluno_coorientado.nome)
        self.assertContains(response, aluno_concluido.nome)
        # As tres secoes viraram tabelas com colunas proprias. Na de
        # coorientacoes, a coluna "Orientador" diz quem orienta de fato -- antes
        # a linha so trazia a palavra "Coorientador" solta entre pipes, sem
        # nomear o orientador. Na de concluidas, a coluna "Seu papel" diz qual
        # foi o vinculo do docente naquela trajetoria.
        self.assertContains(response, "Orientador")
        self.assertContains(response, self.coordenador.nome)
        self.assertContains(response, "Seu papel")

    def test_coorientador_cadastrado_acessa_processo_do_aluno(self):
        processo = Processo.objects.create(
            usuario_criado_por=self.aluno,
            tipo=Processo.TipoProcesso.OUTRO,
            assunto="Solicitação com coorientador",
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
                "comentario": "Troca aprovada pela coordenação.",
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
                "comentario": "Coorientação externa aprovada.",
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
                "data_ingresso": "2027-03",
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
        self.assertEqual(doutorado.prazo_qualificacao, "2030.1")
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
            nome="Aluno Inválido",
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
        self.assertEqual(trajetoria.status, TrajetoriaAcademica.Status.CONCLUIDA)
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

        self.assertEqual(trajetoria.conclusao_label, "Relatório final")
        self.assertEqual(trajetoria.numero_defesa, "RF-2026-01")
        self.assertEqual(trajetoria.prazo_qualificacao, "")
        self.assertEqual(trajetoria.prazo_defesa, "")
        self.assertEqual(trajetoria.orientador, self.docente)
        self.assertFalse(trajetoria.deposito_versao_final)

    def test_pos_doutorado_rejeita_lancamento_de_horas_complementares(self):
        posdoc = Aluno.objects.create(
            email="posdoc.sem.horas@example.com",
            password="senha-segura-123",
            nome="Pós-Doutorado sem Horas",
        )
        trajetoria = criar_trajetoria(
            posdoc,
            nivel_curso=Aluno.NivelCurso.POSDOUTORADO,
            orientador=self.docente,
        )

        with self.assertRaisesMessage(
            ValidationError,
            "Trajetórias de Pós-Doutorado não possuem horas complementares.",
        ):
            LancamentoHorasComplementares(trajetoria=trajetoria).clean()

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
                "comentario": "Desligamento por solicitação formal.",
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
                "data_defesa": "2027-12-20",
                "comentario": "Defesa homologada.",
            },
        )
        self.assertEqual(response.status_code, 302)
        trajetoria.refresh_from_db()
        self.assertEqual(trajetoria.status, TrajetoriaAcademica.Status.CONCLUIDA)
        self.assertEqual(trajetoria.numero_defesa, "ATA-2026-33")
        self.assertEqual(str(trajetoria.data_defesa), "2027-12-20")


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
        # resolve pelo mesmo caminho do template: com collectstatic o nome
        # ganha o hash do conteudo (css/app.<hash>.css)
        self.assertContains(response, static("css/app.css"))
        self.assertContains(response, "img/acadflow-wordmark")
        self.assertContains(response, 'rel="icon"')
        self.assertContains(response, "auth-shell")
        self.assertContains(response, "auth-cartao")
        self.assertContains(response, reverse("password_reset"))
        self.assertContains(response, reverse("cadastro_aluno"))
        # rodape institucional presente em todas as telas
        self.assertContains(response, "Todos os direitos reservados ao PPGEC")
        self.assertContains(response, f"v{settings.APP_VERSION}")

    def test_logout_sem_usuario_redireciona_para_login(self):
        response = self.client.get(reverse("logout"))

        self.assertRedirects(response, reverse("login"))

    def test_logout_autenticado_continua_exigindo_post(self):
        self.client.force_login(self.aluno)

        response_get = self.client.get(reverse("logout"))
        self.assertEqual(response_get.status_code, 405)
        self.assertIn("_auth_user_id", self.client.session)

        response_post = self.client.post(reverse("logout"))
        self.assertRedirects(response_post, reverse("login"))
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_cadastro_aluno_renderiza_identidade_acadflow(self):
        response = self.client.get(reverse("cadastro_aluno"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "cadastro de aluno")
        self.assertContains(response, "img/acadflow-wordmark")
        self.assertContains(response, "e-mail institucional")
        self.assertContains(response, "Polo do aluno")
        self.assertContains(response, "CPF")
        self.assertContains(response, "Gênero")
        self.assertContains(response, "Sexo atribuído ao nascer")
        # os dados estatisticos seguem marcados como opcionais e com o aviso
        # de confidencialidade, agora no cabecalho do proprio grupo de campos
        self.assertContains(response, "Dados estatísticos")
        self.assertContains(response, "opcional")
        self.assertContains(response, "tratados de forma confidencial")
        self.assertContains(response, "auth-shell")

    def test_cadastro_aluno_cria_conta_em_avaliacao(self):
        response = self.client.post(
            reverse("cadastro_aluno"),
            {
                "nome": "Nova Aluna",
                "email": "nova.aluna@example.com",
                "cpf": "529.982.247-25",
                "genero": Aluno.Genero.MULHER,
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
        self.assertEqual(aluno.cpf, "52998224725")
        self.assertEqual(aluno.genero, Aluno.Genero.MULHER)
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

    def test_cadastro_aceita_genero_e_sexo_em_branco(self):
        response = self.client.post(
            reverse("cadastro_aluno"),
            {
                "nome": "Aluno Dados Opcionais",
                "email": "dados.opcionais@example.com",
                "cpf": "111.444.777-35",
                "password1": "senha-segura-123",
                "password2": "senha-segura-123",
                "polo_atuacao": self.polo.id,
                "nivel_curso": Aluno.NivelCurso.ALUNO_ESPECIAL,
                "ingresso": "2026",
                "tipo_coorientador": "NENHUM",
            },
        )

        self.assertRedirects(response, reverse("cadastro_aluno_sucesso"))
        aluno = Aluno.objects.get(email="dados.opcionais@example.com")
        self.assertEqual(aluno.genero, "")
        self.assertEqual(aluno.sexo_atribuido_nascimento, "")

    def test_aluno_sem_cpf_recebe_modal_e_pode_informar_cpf(self):
        self.client.force_login(self.aluno)

        response = self.client.get(reverse("home"))
        self.assertContains(response, 'id="modal-informar-cpf"')
        self.assertContains(response, "Atualizar cadastro")
        self.assertContains(response, "data-close-cpf-modal")

        response = self.client.post(
            reverse("aluno_informar_cpf"),
            {"cpf": "529.982.247-25", "next": reverse("home")},
        )
        self.assertRedirects(response, reverse("home"))
        self.aluno.refresh_from_db()
        self.assertEqual(self.aluno.cpf, "52998224725")

        response = self.client.get(reverse("home"))
        self.assertNotContains(response, 'id="modal-informar-cpf"')

    def test_cpf_invalido_mantem_modal_pendente(self):
        self.client.force_login(self.aluno)

        response = self.client.post(
            reverse("aluno_informar_cpf"),
            {"cpf": "123.456.789-00", "next": reverse("home")},
            follow=True,
        )

        self.aluno.refresh_from_db()
        self.assertIsNone(self.aluno.cpf)
        self.assertContains(response, 'id="modal-informar-cpf"')
        self.assertContains(response, "Informe um CPF válido")

    def test_dados_sensiveis_aparecem_apenas_no_modal_de_gestao(self):
        self.aluno.cpf = "529.982.247-25"
        self.aluno.genero = Aluno.Genero.NAO_BINARIO
        self.aluno.save()

        self.client.force_login(self.aluno)
        response = self.client.get(reverse("aluno_detalhe", args=[self.aluno.pk]))
        self.assertNotContains(response, "52998224725")
        self.assertNotContains(response, "Não binário")

        secretaria = User.objects.create_user(
            email="secretaria.frontend@example.com",
            password="senha-segura-123",
            nome="Secretaria Frontend",
            tipo_usuario=User.TipoUsuario.SERVIDOR,
        )
        self.client.force_login(secretaria)
        response = self.client.get(reverse("aluno_detalhe", args=[self.aluno.pk]))
        self.assertNotContains(response, '<p class="aluno-kv"><strong>CPF:</strong>', html=False)
        self.assertNotContains(response, '<p class="aluno-kv"><strong>Gênero:</strong>', html=False)
        self.assertContains(response, "52998224725")
        self.assertContains(response, "Não binário")

    def test_esqueci_minha_senha_renderiza_identidade_acadflow(self):
        response = self.client.get(reverse("password_reset"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Recuperar senha")
        self.assertContains(response, "img/acadflow-wordmark")
        self.assertContains(response, "auth-shell")
        self.assertContains(response, "Todos os direitos reservados ao PPGEC")

    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        DEFAULT_FROM_EMAIL="AcadFlow <noreply@example.com>",
    )
    def test_esqueci_minha_senha_envia_email_com_link_visual_acadflow(self):
        usuario = User.objects.create_user(
            email="recuperar.senha@example.com",
            password="senha-antiga-123",
            nome="Usuário Recuperacao",
            tipo_usuario=User.TipoUsuario.SERVIDOR,
        )

        with self.assertLogs("acadflow.password_reset", level="INFO") as logs:
            response = self.client.post(reverse("password_reset"), {"email": usuario.email})

        self.assertRedirects(response, reverse("password_reset_done"))
        self.assertEqual(len(mail.outbox), 1)
        mensagem = mail.outbox[0]
        self.assertEqual(mensagem.to, [usuario.email])
        self.assertIn("Alteração de senha", mensagem.subject)
        self.assertIn("/senha/redefinir/", mensagem.body)
        self.assertEqual(len(mensagem.alternatives), 1)
        html, content_type = mensagem.alternatives[0]
        self.assertEqual(content_type, "text/html")
        self.assertIn("AcadFlow - PPGEC", html)
        self.assertIn("Alterar senha", html)
        self.assertIn("/senha/redefinir/", html)
        self.assertTrue(any("password_reset_requested" in mensagem for mensagem in logs.output))
        self.assertTrue(any("password_reset_account_eligible" in mensagem for mensagem in logs.output))
        self.assertTrue(any("password_reset_email_sent" in mensagem for mensagem in logs.output))

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_reset_registra_conta_com_senha_inutilizavel(self):
        usuario = User.objects.create_user(
            email="sem.senha.utilizavel@example.com",
            password="senha-temporaria-123",
            nome="Usuário sem senha utilizável",
        )
        usuario.set_unusable_password()
        usuario.save(update_fields=["password"])

        with self.assertLogs("acadflow.password_reset", level="INFO") as logs:
            response = self.client.post(reverse("password_reset"), {"email": usuario.email})

        self.assertRedirects(response, reverse("password_reset_done"))
        self.assertEqual(len(mail.outbox), 0)
        self.assertTrue(any("password_reset_account_ineligible" in mensagem for mensagem in logs.output))
        self.assertTrue(any("usable_password=False" in mensagem for mensagem in logs.output))

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.smtp.EmailBackend")
    @patch("ppgec.views.EmailMultiAlternatives.send", side_effect=RuntimeError("SMTP indisponível"))
    def test_reset_registra_falha_do_backend_de_email(self, _mock_send):
        usuario = User.objects.create_user(
            email="falha.smtp@example.com",
            password="senha-segura-123",
            nome="Usuário Falha SMTP",
        )

        with self.assertLogs("acadflow.password_reset", level="INFO") as logs:
            response = self.client.post(reverse("password_reset"), {"email": usuario.email})

        self.assertRedirects(response, reverse("password_reset_done"))
        self.assertTrue(any("password_reset_email_attempt" in mensagem for mensagem in logs.output))
        self.assertTrue(any("password_reset_email_failed" in mensagem for mensagem in logs.output))

    def test_home_renderiza_shell_e_dashboard_acadflow(self):
        self.client.force_login(self.docente)
        response = self.client.get(reverse("home"))

        self.assertEqual(response.status_code, 200)
        # A faixa de abertura deixou de repetir a marca -- que ja esta na barra
        # lateral, em toda tela -- e passou a cumprimentar pelo nome.
        self.assertContains(response, "Olá,")
        self.assertContains(response, self.docente.nome.split()[0])
        self.assertContains(response, "img/acadflow-wordmark")
        self.assertContains(response, 'class="sidebar"')
        self.assertContains(response, 'class="metric-grid"')
        self.assertContains(response, "overdue-link")
        self.assertContains(response, 'class="user-menu"')
        self.assertContains(response, "Meus Processos")
        self.assertNotContains(response, "Processos no Pleno")
        self.assertNotContains(response, 'class="nav"')
        # O painel da conta ganhou identidade e descricoes: os rotulos passaram
        # de "Perfil" e "Sair" para "Meu perfil" e "Sair da conta". O que este
        # teste verifica -- que os dois destinos existem no painel -- continua.
        self.assertContains(response, "Meu perfil")
        self.assertContains(response, "Sair da conta")
        self.assertContains(response, self.docente.email)

    def test_membro_do_pleno_ve_menu_e_rota_de_processos_do_pleno(self):
        pleno = Setor.objects.get(nome=Setor.NOME_PLENO)
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
        """A home do aluno leva a abrir processo e a consultar os existentes.

        O bloco "Acesso rapido" foi removido: os tres atalhos dele repetiam o
        menu lateral, e ficavam 420px abaixo da dobra. Os caminhos seguem na
        tela, pelos cartoes da visao geral -- por isso a verificacao e pelo
        destino, nao pelo rotulo que existia no bloco antigo.
        """
        self.client.force_login(self.aluno)
        response = self.client.get(reverse("home"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse("novo_processo"))
        self.assertContains(response, reverse("menu_meus_processos"))
        self.assertContains(response, "Pós-Graduação em Engenharia de Computação")

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
        self.assertNotContains(response, "Ciências manifestadas")
        self.assertContains(response, "Meus Orientandos")
        self.assertContains(response, "Cadastro de Salas")

    def test_menu_ciencias_exibe_pendencias_e_manifestadas(self):
        servidor = User.objects.create_user(
            email="servidor.ciencias@example.com",
            password="senha-segura-123",
            nome="Servidor Ciências",
            tipo_usuario=User.TipoUsuario.SERVIDOR,
        )
        processo_pendente = Processo.objects.create(
            usuario_criado_por=self.aluno,
            tipo=Processo.TipoProcesso.OUTRO,
            assunto="Processo com ciência pendente",
            descricao="Solicitacao",
            setor_atual=Setor.objects.get(nome="Requerente"),
        )
        processo_manifestado = Processo.objects.create(
            usuario_criado_por=self.aluno,
            tipo=Processo.TipoProcesso.OUTRO,
            assunto="Processo com ciência manifestada",
            descricao="Solicitacao",
            setor_atual=Setor.objects.get(nome="Requerente"),
        )
        ManifestacaoProcesso.objects.create(
            processo=processo_pendente,
            tipo=ManifestacaoProcesso.TipoManifestacao.CIENTE_ORIENTADOR,
            responsavel=self.docente,
            solicitado_por=servidor,
            mensagem_solicitacao="Favor manifestar ciência.",
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
        self.assertContains(response, "<h1 class=\"section-title\">Ciências</h1>", html=True)
        self.assertContains(response, "Pendências de ciência")
        self.assertContains(response, "Ciências já manifestadas")
        self.assertContains(response, processo_pendente.assunto)
        self.assertContains(response, "Manifestar ciência")
        self.assertContains(response, "Favor manifestar ciência.")
        self.assertContains(response, processo_manifestado.assunto)
        self.assertContains(response, "Manifestação: Ciente.")


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
        self.assertContains(home, "processo atrasado")
        self.assertNotContains(home, "1 processos atrasados")

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
            "resumo": "Resumo da dissertação.",
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
            nome="Aluno Sem Vínculo",
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
        self.assertNotContains(response, "Aluno Sem Vínculo")

    def test_docente_salva_rascunho_de_solicitacao(self):
        self.client.force_login(self.docente)
        response = self.client.post(
            reverse("solicitacao_banca_nova"),
            {
                "acao": "rascunho",
                "aluno": self.aluno_mestrado.id,
                "trajetoria": self.trajetoria_mestrado.id,
                "tipo_defesa": SolicitacaoBanca.TipoDefesa.DEFESA_MESTRADO,
                "titulo": "Rascunho de dissertação",
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
        self.assertEqual(solicitacao.processo.aluno_interessado_id, self.aluno_mestrado.id)
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
        self.assertContains(response, "Informe um CPF válido.")
        self.assertEqual(SolicitacaoBanca.objects.count(), 0)

    def test_novo_processo_nao_lista_formularios_de_banca(self):
        propria = SolicitacaoBanca.objects.create(
            docente=self.docente,
            aluno=self.aluno_mestrado,
            trajetoria=self.trajetoria_mestrado,
            tipo_defesa=SolicitacaoBanca.TipoDefesa.DEFESA_MESTRADO,
            titulo="Solicitação própria",
        )
        outra = SolicitacaoBanca.objects.create(
            docente=self.outro_docente,
            aluno=self.aluno_doutorado,
            trajetoria=self.trajetoria_doutorado,
            tipo_defesa=SolicitacaoBanca.TipoDefesa.QUALIFICACAO_DOUTORADO,
            titulo="Solicitação de outro docente",
        )

        self.client.force_login(self.docente)
        response = self.client.get(reverse("novo_processo"))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Formulários salvos")
        self.assertNotContains(response, str(propria))
        self.assertNotContains(response, str(outra))

    @patch("processos.views.send_email_novo_processo_secretaria.delay")
    @patch("processos.views.send_email_novo_processo_orientador.delay")
    @patch("processos.views.send_email_novo_processo_aluno.delay")
    def test_docente_abre_processo_normal_para_orientando(
        self,
        _email_aluno,
        _email_orientador,
        _email_secretaria,
    ):
        self.client.force_login(self.docente)

        response = self.client.post(
            reverse("novo_processo"),
            {
                "aluno_interessado": self.aluno_mestrado.id,
                "tipo": Processo.TipoProcesso.DEFESA_MESTRADO,
                "assunto": "Documentação da banca do orientando",
                "descricao": "Processo normal aberto pelo orientador.",
            },
        )

        self.assertRedirects(response, reverse("home"))
        processo = Processo.objects.get(assunto="Documentação da banca do orientando")
        self.assertEqual(processo.usuario_criado_por_id, self.docente.id)
        self.assertEqual(processo.aluno_interessado_id, self.aluno_mestrado.id)
        self.assertEqual(processo.obter_orientador_responsavel().id, self.docente.id)

    def test_docente_nao_pode_selecionar_aluno_sem_vinculo(self):
        aluno_sem_vinculo = Aluno.objects.create_user(
            email="sem.vinculo.processo@example.com",
            password="senha-segura-123",
            nome="Aluno sem vínculo para processo",
        )
        self.client.force_login(self.docente)

        response = self.client.post(
            reverse("novo_processo"),
            {
                "aluno_interessado": aluno_sem_vinculo.id,
                "tipo": Processo.TipoProcesso.OUTRO,
                "assunto": "Processo inválido",
                "descricao": "Não deve ser criado.",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Faça uma escolha válida")
        self.assertFalse(Processo.objects.filter(assunto="Processo inválido").exists())

    @patch("processos.tasks._send_email")
    def test_email_do_pleno_usa_aluno_interessado_e_orientador(self, enviar_email):
        from .tasks import send_email_movimentacao_pleno

        processo = Processo.objects.create(
            usuario_criado_por=self.docente,
            aluno_interessado=self.aluno_mestrado,
            tipo=Processo.TipoProcesso.DEFESA_MESTRADO,
            assunto="Banca encaminhada ao Pleno",
            descricao="Documentação anexada.",
            setor_atual=self.setor_secretaria,
        )

        send_email_movimentacao_pleno.run(processo.id)

        self.assertTrue(enviar_email.called)
        contexto = enviar_email.call_args.kwargs["contexto"]
        self.assertEqual(contexto["aluno"].id, self.aluno_mestrado.id)
        self.assertEqual(contexto["orientador"].id, self.docente.id)
        self.assertIn(self.aluno_mestrado.nome, enviar_email.call_args.kwargs["subject"])

    @patch("processos.views.send_email_novo_processo_secretaria.delay")
    @patch("processos.views.send_email_novo_processo_orientador.delay")
    @patch("processos.views.send_email_novo_processo_aluno.delay")
    def test_aluno_cria_novo_processo_com_documento_anexado(
        self,
        _email_aluno,
        _email_orientador,
        _email_secretaria,
    ):
        arquivo = SimpleUploadedFile("requerimento.pdf", b"conteudo-pdf", content_type="application/pdf")
        self.client.force_login(self.aluno_mestrado)

        with self.assertLogs("acadflow.processo_abertura", level="INFO") as logs:
            response = self.client.post(
                reverse("novo_processo"),
                {
                    "tipo": Processo.TipoProcesso.OUTRO,
                    "assunto": "Processo com documento",
                    "descricao": "Teste de abertura com arquivo anexado.",
                    "doc_0_titulo": "Requerimento",
                    "doc_0_tipo_documento": Documento.TipoDocumento.REQUERIMENTO,
                    "doc_0_restricao_tipo": Documento.RestricaoAcesso.NAO,
                    "doc_0_arquivo": arquivo,
                },
            )

        self.assertRedirects(response, reverse("home"))
        processo = Processo.objects.get(assunto="Processo com documento")
        self.assertEqual(processo.aluno_interessado_id, self.aluno_mestrado.id)
        documento = processo.documentos.get()
        self.assertEqual(documento.titulo, "Requerimento")
        self.assertEqual(documento.enviado_por_id, self.aluno_mestrado.id)
        self.assertEqual(Path(documento.arquivo.name).suffix, ".pdf")
        self.assertTrue(any("processo_abertura_anexo_salvo" in mensagem for mensagem in logs.output))
        self.assertTrue(any("processo_abertura_concluida" in mensagem for mensagem in logs.output))

    @patch("processos.views.send_email_novo_processo_secretaria.delay")
    @patch("processos.views.send_email_novo_processo_orientador.delay")
    @patch("processos.views.send_email_novo_processo_aluno.delay")
    def test_falha_ao_salvar_anexo_nao_deixa_processo_orfao(
        self,
        _email_aluno,
        _email_orientador,
        _email_secretaria,
    ):
        arquivo = SimpleUploadedFile("sem-permissao.pdf", b"conteudo-pdf", content_type="application/pdf")
        armazenamento = Documento._meta.get_field("arquivo").storage
        self.client.force_login(self.aluno_mestrado)
        self.client.raise_request_exception = False

        with (
            patch.object(armazenamento, "save", side_effect=PermissionError("media sem permissao")),
            self.assertLogs("acadflow.processo_abertura", level="ERROR") as logs,
        ):
            response = self.client.post(
                reverse("novo_processo"),
                {
                    "tipo": Processo.TipoProcesso.OUTRO,
                    "assunto": "Processo que deve ser revertido",
                    "descricao": "Teste de falha no armazenamento.",
                    "doc_0_titulo": "Requerimento",
                    "doc_0_tipo_documento": Documento.TipoDocumento.REQUERIMENTO,
                    "doc_0_restricao_tipo": Documento.RestricaoAcesso.NAO,
                    "doc_0_arquivo": arquivo,
                },
            )

        self.assertEqual(response.status_code, 500)
        self.assertFalse(Processo.objects.filter(assunto="Processo que deve ser revertido").exists())
        self.assertTrue(any("processo_abertura_falhou" in mensagem for mensagem in logs.output))

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
            nome="Comissão de Assinaturas",
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
        self.assertNotContains(lista, "Enviar solicitação")
        self.assertContains(nova, "Nova Solicitação de Assinatura")
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
        self.assertContains(response, "Nova solicitação")
        self.assertContains(response, "Pendências de assinatura")
        self.assertContains(response, "Solicitações feitas")

    def test_busca_solicitacoes_por_referencias_pdf_e_observacoes(self):
        casos = [
            {
                "tipo_documento": SolicitacaoAssinatura.TipoDocumento.DOCUMENTO_SEI,
                "numero_documento_sei": "DOC-LOCALIZAVEL-123",
                "observacao": "Primeiro pedido",
            },
            {
                "tipo_documento": SolicitacaoAssinatura.TipoDocumento.BLOCO_SEI,
                "numero_bloco_sei": "BLOCO-LOCALIZAVEL-456",
                "observacao": "Segundo pedido",
            },
            {
                "tipo_documento": SolicitacaoAssinatura.TipoDocumento.PDF,
                "documento_pdf": "assinaturas/originais/2026/08/ata_localizavel.pdf",
                "observacao": "Terceiro pedido",
            },
        ]
        solicitacoes = [
            SolicitacaoAssinatura.objects.create(
                criado_por=self.servidor,
                destinatario_tipo=SolicitacaoAssinatura.DestinatarioTipo.DOCENTE,
                docente=self.docente,
                **dados,
            )
            for dados in casos
        ]
        solicitacoes[2].observacao_assinatura = "Conferência final localizável"
        solicitacoes[2].save(update_fields=["observacao_assinatura"])
        self.client.force_login(self.servidor)

        for termo, esperado in (
            ("DOC-LOCALIZAVEL", solicitacoes[0]),
            ("BLOCO-LOCALIZAVEL", solicitacoes[1]),
            ("ata_localizavel.pdf", solicitacoes[2]),
            ("Conferência final", solicitacoes[2]),
        ):
            with self.subTest(termo=termo):
                response = self.client.get(reverse("solicitacoes_assinatura"), {"q": termo})
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, reverse("solicitacao_assinatura_detalhe", args=[esperado.pk]))
                self.assertEqual(list(response.context["solicitacoes"]), [esperado])

    def test_busca_preserva_filtro_de_status(self):
        self.client.force_login(self.servidor)

        response = self.client.get(
            reverse("solicitacoes_assinatura"),
            {"status": SolicitacaoAssinatura.Status.PENDENTE, "q": "documento importante"},
        )

        self.assertContains(response, 'name="status" value="PENDENTE"', html=False)
        self.assertContains(response, "q=documento%20importante", html=False)

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
        self.assertContains(response, "Pendências de Assinatura")
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
        self.assertIn("Solicitação de assinatura", mail.outbox[0].subject)


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
                "titulo": "Aula de pós-graduação",
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
                "justificativa": "Reserva cancelada pela coordenação.",
            },
        )

        self.assertEqual(response.status_code, 302)
        reserva.refresh_from_db()
        self.assertEqual(reserva.status, ReservaAmbiente.StatusReserva.EXCLUIDA)
        self.assertEqual(reserva.excluida_por_id, coordenador.id)
        self.assertIsNotNone(reserva.excluida_em)
        self.assertEqual(reserva.justificativa_exclusao, "Reserva cancelada pela coordenação.")

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
            titulo="Nova reserva no mesmo horário",
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
            nome="Outro Docente Calendário",
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
        self.assertContains(response, "Ocupado 10:00–11:00 · Defesa")
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
                "titulo": "Horário inválido",
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

    def test_coordenador_com_polo_de_atuacao_pode_cadastrar_sala_em_outro_polo(self):
        coordenador = Docente.objects.create(
            email="coordenador.polo.salas@example.com",
            password="senha-segura-123",
            nome="Coordenador com Polo",
            coordenador=True,
            polo_atuacao=self.polo,
        )
        self.client.force_login(coordenador)

        response = self.client.post(
            reverse("salas_ambientes"),
            {
                "acao": "criar_sala",
                "sala-polo": self.outro_polo.pk,
                "sala-nome": "Sala do Polo Norte",
                "sala-capacidade": "18",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            Sala.objects.filter(polo=self.outro_polo, nome="Sala do Polo Norte").exists()
        )

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


@override_settings(SECURE_SSL_REDIRECT=False)
class BolsistaVoluntarioAccessTests(TestCase):
    def setUp(self):
        self.usuario = User.objects.create_user(
            email="bolsista.voluntario@example.com",
            password="senha-segura-123",
            nome="Bolsista Voluntário",
            tipo_usuario=User.TipoUsuario.BOLSISTA_VOLUNTARIO,
        )
        self.client.force_login(self.usuario)

    def test_recebe_menu_e_telas_de_gestao_do_servidor(self):
        home = self.client.get(reverse("home"))

        self.assertEqual(home.status_code, 200)
        self.assertContains(home, "Caixa de Processos")
        self.assertContains(home, "Dashboard")
        self.assertContains(home, "Alunos")
        self.assertContains(home, "Cadastro de Salas")
        self.assertNotContains(home, "Novo Processo")

        for rota in (
            "coordenacao_dashboard",
            "coordenacao_alunos",
            "coordenacao_processos",
            "coordenacao_caixa_processos",
            "matriculas_periodos",
            "reservas_ambientes",
            "salas_ambientes",
        ):
            with self.subTest(rota=rota):
                self.assertEqual(self.client.get(reverse(rota)).status_code, 200)

        self.assertEqual(self.client.get(reverse("menu_meus_processos")).status_code, 403)

    def test_pode_ser_selecionado_como_membro_de_setor(self):
        form = SetorComissaoForm()

        self.assertIn(self.usuario, form.fields["servidores"].queryset)


class MenuLateralIconesTests(SimpleTestCase):
    """Garante que todo icone referenciado no menu existe no sprite SVG.

    Um icone inexistente nao quebra nada: o <use> simplesmente nao renderiza e
    o item aparece sem icone -- falha silenciosa, facil de passar em revisao.

    A verificacao le a arvore sintatica do context_processors em vez de montar
    menus para usuarios de teste. Motivo: o menu tem varios ramos condicionais
    (vinculo com setor, coordenador, membro do Pleno) e uma bateria de perfis
    sinteticos nunca cobre todos -- a primeira versao deste teste passava com
    um icone comprovadamente quebrado.
    """

    @staticmethod
    def _icones_do_sprite():
        caminho = Path(settings.BASE_DIR) / "templates" / "includes" / "icons.html"
        return set(re.findall(r'<symbol id="i-([\w-]+)"', caminho.read_text(encoding="utf-8")))

    @staticmethod
    def _icones_declarados():
        """Todo 4o argumento posicional de _menu_item(...), em qualquer ramo."""
        origem = Path(settings.BASE_DIR) / "processos" / "context_processors.py"
        arvore = ast.parse(origem.read_text(encoding="utf-8"))
        icones = {}
        for no in ast.walk(arvore):
            if not isinstance(no, ast.Call):
                continue
            if not (isinstance(no.func, ast.Name) and no.func.id == "_menu_item"):
                continue
            if len(no.args) < 4:
                continue
            rotulo, icone = no.args[0], no.args[3]
            if isinstance(icone, ast.Constant) and isinstance(icone.value, str):
                nome = rotulo.value if isinstance(rotulo, ast.Constant) else f"linha {no.lineno}"
                icones[(nome, no.lineno)] = icone.value
        return icones

    def test_todos_os_icones_do_menu_existem_no_sprite(self):
        disponiveis = self._icones_do_sprite()
        declarados = self._icones_declarados()

        self.assertTrue(disponiveis, "sprite de icones vazio ou nao encontrado")
        self.assertGreaterEqual(
            len(declarados), 25,
            "poucos itens de menu encontrados -- a leitura do context_processors falhou?",
        )

        ausentes = {
            f"{rotulo} (linha {linha}) -> {icone}"
            for (rotulo, linha), icone in declarados.items()
            if icone not in disponiveis
        }
        self.assertEqual(
            ausentes, set(),
            "icones sem <symbol> correspondente em templates/includes/icons.html:\n  "
            + "\n  ".join(sorted(ausentes)),
        )

    def test_nomes_de_icone_seguem_o_padrao(self):
        fora_do_padrao = {
            f"{rotulo} -> {icone!r}"
            for (rotulo, _), icone in self._icones_declarados().items()
            if not re.fullmatch(r"[a-z][a-z-]*", icone)
        }
        self.assertEqual(
            fora_do_padrao, set(),
            "nome de icone deve ser minusculo com hifens (ex.: novo-processo):\n  "
            + "\n  ".join(sorted(fora_do_padrao)),
        )


class TemplatesSintaxeTests(SimpleTestCase):
    """Guardas de sintaxe que o Django nao acusa como erro."""

    @staticmethod
    def _templates():
        return sorted(Path(settings.BASE_DIR).joinpath("templates").rglob("*.html"))

    def test_comentario_de_uma_linha_nao_abre_sem_fechar(self):
        """{# ... #} e sempre de uma linha so.

        Se abrir numa linha e fechar em outra, o Django nao acusa erro: o texto
        do "comentario" e renderizado na pagina. Dentro de um container flex ele
        ainda vira um item de layout e desloca a tela inteira. Use
        {% templatetag openblock %} comment {% templatetag closeblock %} para
        varias linhas.
        """
        infracoes = []
        for caminho in self._templates():
            for numero, linha in enumerate(caminho.read_text(encoding="utf-8").split("\n"), 1):
                if "{#" in linha and "#}" not in linha:
                    relativo = caminho.relative_to(settings.BASE_DIR)
                    infracoes.append(f"{relativo}:{numero}: {linha.strip()[:70]}")
        self.assertEqual(
            infracoes, [],
            "comentario {# #} aberto sem fechar na mesma linha:\n  " + "\n  ".join(infracoes),
        )

    def test_nome_de_bloco_nao_se_repete_no_mesmo_template(self):
        """Bloco repetido derruba o template com TemplateSyntaxError."""
        infracoes = []
        for caminho in self._templates():
            nomes = re.findall(r"{%\s*block\s+([\w-]+)\s*%}", caminho.read_text(encoding="utf-8"))
            repetidos = {n for n in nomes if nomes.count(n) > 1}
            if repetidos:
                infracoes.append(f"{caminho.relative_to(settings.BASE_DIR)}: {sorted(repetidos)}")
        self.assertEqual(infracoes, [], "nomes de bloco repetidos:\n  " + "\n  ".join(infracoes))


class PaginacaoListagensTests(TestCase):
    """Paginacao das listagens de gestao.

    Com poucos registros a navegacao nem aparece, entao os testes reduzem o
    tamanho da pagina para exercitar o comportamento real: preservar filtros
    ao trocar de pagina, tolerar page invalido e contar o total do filtro --
    nao o tamanho da pagina.
    """

    def setUp(self):
        self.servidor = User.objects.create_user(
            email="servidor.paginacao@example.com", password="senha-segura-123",
            nome="Servidor Paginacao", tipo_usuario=User.TipoUsuario.SERVIDOR,
        )
        self.setor = Setor.objects.create(nome="Setor Paginacao")
        for i in range(12):
            Processo.objects.create(
                numero=f"202607-99{i:04d}",
                usuario_criado_por=self.servidor,
                tipo=Processo.TipoProcesso.OUTRO,
                assunto=("Alvo do filtro" if i < 4 else f"Processo comum {i}"),
                descricao="Descricao",
                status_inicial="ABERTO",
                status=Processo.StatusProcesso.EM_ANALISE,
                setor_atual=self.setor,
            )
        self.client.force_login(self.servidor)
        # a seed da migracao 0003 ja deixa processos no banco de teste, entao o
        # total nao e so o que este setUp criou
        self.total = Processo.objects.count()

    def _pagina(self, resposta):
        return resposta.context["pagina"]

    @patch("processos.views.ITENS_POR_PAGINA", 5)
    def test_divide_em_paginas_e_conta_o_total(self):
        resposta = self.client.get(reverse("coordenacao_processos"))
        pagina = self._pagina(resposta)

        self.assertGreater(self.total, 5, "o cenario precisa de mais de uma pagina")
        self.assertEqual(pagina.number, 1)
        self.assertEqual(pagina.paginator.num_pages, -(-self.total // 5))
        self.assertEqual(pagina.paginator.count, self.total)
        self.assertEqual(len(pagina.object_list), 5)
        # a contagem exibida e do total filtrado, nao das 5 linhas da pagina
        self.assertContains(resposta, f"{self.total} processos")

    @patch("processos.views.ITENS_POR_PAGINA", 5)
    def test_filtro_sobrevive_a_troca_de_pagina(self):
        """O erro classico e o filtro sumir ao clicar em "proxima"."""
        primeira = self.client.get(reverse("coordenacao_processos"), {"q": "Alvo do filtro"})
        self.assertEqual(self._pagina(primeira).paginator.count, 4)

        segunda = self.client.get(reverse("coordenacao_processos"), {"q": "Alvo do filtro", "page": 1})
        self.assertEqual(self._pagina(segunda).paginator.count, 4)
        # o link de paginacao carrega o filtro junto
        self.assertNotContains(segunda, 'href="?page=')

    @patch("processos.views.ITENS_POR_PAGINA", 5)
    def test_pagina_invalida_cai_na_primeira(self):
        """page vem da URL: pode chegar editada a mao ou apontando para uma
        pagina que sumiu depois de um filtro."""
        for valor in ["99", "abc", "0", "-3", ""]:
            with self.subTest(page=valor):
                resposta = self.client.get(reverse("coordenacao_processos"), {"page": valor})
                self.assertEqual(resposta.status_code, 200)
                self.assertEqual(self._pagina(resposta).number, 1)

    @patch("processos.views.ITENS_POR_PAGINA", 5)
    def test_navegacao_so_aparece_com_mais_de_uma_pagina(self):
        com_varias = self.client.get(reverse("coordenacao_processos"))
        self.assertContains(com_varias, "paginacao-passo")

        uma_so = self.client.get(reverse("coordenacao_processos"), {"q": "Alvo do filtro"})
        self.assertEqual(self._pagina(uma_so).paginator.num_pages, 1)
        self.assertNotContains(uma_so, "paginacao-passo")


class OrientadorAcessaOrientandoTests(TestCase):
    """O orientador precisa alcancar a ficha do proprio orientando.

    A tela "Meus Orientandos" lista os alunos dele, mas abrir qualquer um dava
    403: a verificacao so considerava gestao e o proprio aluno. Nao havia
    caminho nenhum para a trajetoria do orientando.

    O acesso e de leitura -- alterar a ficha continua sendo da coordenacao.
    """

    def setUp(self):
        senha = "senha-segura-123"
        self.orientador = Docente.objects.create_user(
            email="orientador.acesso@example.com", password=senha, nome="Orientador Acesso",
            tipo_usuario=User.TipoUsuario.DOCENTE,
        )
        self.coorientador = Docente.objects.create_user(
            email="coorientador.acesso@example.com", password=senha, nome="Coorientador Acesso",
            tipo_usuario=User.TipoUsuario.DOCENTE,
        )
        self.outro_docente = Docente.objects.create_user(
            email="alheio.acesso@example.com", password=senha, nome="Docente Alheio",
            tipo_usuario=User.TipoUsuario.DOCENTE,
        )
        self.aluno = Aluno.objects.create_user(
            email="orientando.acesso@example.com", password=senha, nome="Orientando Acesso",
            tipo_usuario=User.TipoUsuario.ALUNO, matricula="2026B0001",
        )
        self.trajetoria = TrajetoriaAcademica.objects.create(
            aluno=self.aluno, orientador=self.orientador, coorientador=self.coorientador,
            nivel_curso="MESTRADO", ingresso="2026.1",
            status=TrajetoriaAcademica.Status.ATIVA,
        )
        self.url = reverse("aluno_detalhe", args=[self.aluno.id])

    def test_orientador_abre_a_ficha(self):
        self.client.force_login(self.orientador)
        self.assertEqual(self.client.get(self.url).status_code, 200)

    def test_coorientador_abre_a_ficha(self):
        self.client.force_login(self.coorientador)
        self.assertEqual(self.client.get(self.url).status_code, 200)

    def test_docente_sem_vinculo_continua_barrado(self):
        self.client.force_login(self.outro_docente)
        self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_orientacao_encerrada_mantem_o_acesso(self):
        """O orientador continua respondendo pelo historico de quem concluiu."""
        self.trajetoria.status = TrajetoriaAcademica.Status.CONCLUIDA
        self.trajetoria.numero_defesa = "ATA-2026-07"
        self.trajetoria.data_defesa = timezone.localdate()
        self.trajetoria.save()

        self.client.force_login(self.orientador)
        self.assertEqual(self.client.get(self.url).status_code, 200)

    def test_orientador_nao_altera_a_ficha(self):
        """Leitura nao pode virar escrita: o aluno pode editar as proprias
        publicacoes, e sem a guarda explicita o orientador herdaria isso."""
        self.client.force_login(self.orientador)
        resposta = self.client.post(self.url, {"acao": "salvar_publicacao"})
        self.assertEqual(resposta.status_code, 403)

    # Formularios so da coordenacao, identificados pelo valor de "acao" que
    # enviam. O modal-close nao serve: ele existe nos modais de publicacao, que
    # o proprio aluno pode abrir legitimamente.
    ACOES_DA_COORDENACAO = [
        "nova_trajetoria",
        "alterar_trajetoria_campo",
        "registrar_horas_complementares",
        "novo_estagio_docencia",
    ]

    def _formularios_de_coordenacao(self, corpo):
        return [acao for acao in self.ACOES_DA_COORDENACAO if f'value="{acao}"' in corpo]

    def test_leitor_nao_recebe_os_formularios_da_coordenacao(self):
        """Os modais de edicao nao podem ir no HTML de quem so le.

        Os botoes que abrem esses modais sempre estiveram atras de
        can_manage_aluno, mas o modal em si nao estava: o markup ia para todo
        mundo que abrisse a ficha. O POST e barrado no servidor -- o que vazava
        era o conteudo dos formularios, entre eles o <select> de orientador com
        a lista de docentes cadastrados.
        """
        for quem, usuario in (("orientador", self.orientador), ("proprio aluno", self.aluno)):
            with self.subTest(leitor=quem):
                self.client.force_login(usuario)
                corpo = self.client.get(self.url).content.decode()
                self.assertEqual(
                    self._formularios_de_coordenacao(corpo), [],
                    f"{quem} recebeu formularios que so a coordenacao usa",
                )
                self.assertNotContains(
                    self.client.get(self.url), self.outro_docente.nome,
                    msg_prefix="a lista de docentes vazou pelo select de orientador",
                )

    def test_coordenacao_continua_recebendo_os_formularios(self):
        """A guarda nao pode ter escondido os modais de quem edita de fato."""
        servidor = User.objects.create_user(
            email="servidor.modais@example.com", password="senha-segura-123",
            nome="Servidor Modais", tipo_usuario=User.TipoUsuario.SERVIDOR,
        )
        self.client.force_login(servidor)
        corpo = self.client.get(self.url).content.decode()
        self.assertEqual(sorted(self._formularios_de_coordenacao(corpo)), sorted(self.ACOES_DA_COORDENACAO))

    def test_lista_de_orientandos_leva_a_ficha(self):
        """Antes o nome era texto solto -- nao havia link para lugar nenhum."""
        self.client.force_login(self.orientador)
        resposta = self.client.get(reverse("menu_meus_orientandos"))
        self.assertContains(resposta, f'href="{self.url}"')


class MensagemDeAcessoNegadoTests(TestCase):
    """A tela de 403 deve dizer o motivo, nao so negar.

    As views levantam PermissionDenied com uma mensagem que identifica quem
    tem acesso; ela era descartada e o usuario via um texto generico.
    """

    def setUp(self):
        self.aluno = Aluno.objects.create_user(
            email="aluno.negado@example.com", password="senha-segura-123",
            nome="Aluno Negado", tipo_usuario=User.TipoUsuario.ALUNO, matricula="2026B0002",
        )

    def test_403_mostra_o_motivo_da_recusa(self):
        self.client.force_login(self.aluno)
        with self.settings(DEBUG=False):
            resposta = self.client.get(reverse("coordenacao_dashboard"))

        self.assertEqual(resposta.status_code, 403)
        self.assertContains(resposta, "Acesso restrito", status_code=403)

    def test_403_do_pleno_identifica_o_colegiado(self):
        self.client.force_login(self.aluno)
        with self.settings(DEBUG=False):
            resposta = self.client.get(reverse("menu_processos_pleno"))

        self.assertContains(resposta, "Colegiado", status_code=403)


class TodasAsTelasRenderizamTests(TestCase):
    """Abre cada tela com cada perfil e verifica que ela responde.

    Existe porque a suite passou verde com /menu/meus-processos/ quebrada: uma
    conversao de template deixou marcacao orfa e a pagina levantava
    TemplateSyntaxError. Nenhum teste abria aquela URL -- o defeito so
    apareceu quando um usuario tentou usar o sistema.

    Nao verifica conteudo, so que a tela nao explode e que o codigo de resposta
    e o esperado para aquele perfil. E a rede de seguranca mais rasa possivel,
    e teria pego aquele caso.
    """

    @classmethod
    def setUpTestData(cls):
        senha = "senha-segura-123"
        cls.aluno = Aluno.objects.create_user(
            email="aluno.telas@example.com", password=senha, nome="Aluno Telas",
            tipo_usuario=User.TipoUsuario.ALUNO, matricula="2026C0001",
        )
        cls.docente = Docente.objects.create_user(
            email="docente.telas@example.com", password=senha, nome="Docente Telas",
            tipo_usuario=User.TipoUsuario.DOCENTE,
        )
        cls.servidor = User.objects.create_user(
            email="servidor.telas@example.com", password=senha, nome="Servidor Telas",
            tipo_usuario=User.TipoUsuario.SERVIDOR,
        )
        TrajetoriaAcademica.objects.create(
            aluno=cls.aluno, orientador=cls.docente, nivel_curso="MESTRADO",
            ingresso="2026.1", status=TrajetoriaAcademica.Status.ATIVA,
        )

    # (rota, perfis que devem receber 200)
    TELAS = [
        ("home", {"aluno", "docente", "servidor"}),
        ("me", {"aluno", "docente", "servidor"}),
        ("menu_meus_processos", {"aluno", "docente"}),
        ("novo_processo", {"aluno", "docente"}),
        ("matriculas_minhas", {"aluno"}),
        ("matricula_solicitar", {"aluno"}),
        ("aluno_documento_vinculo", {"aluno"}),
        ("menu_meus_orientandos", {"docente"}),
        ("menu_processos_orientandos", {"docente"}),
        ("menu_ciencias_manifestadas", {"docente"}),
        ("solicitacoes_banca", {"docente"}),
        ("solicitacao_banca_nova", {"docente"}),
        ("reservas_ambientes", {"docente", "servidor"}),
        ("disponibilidade_ambientes", {"docente", "servidor"}),
        ("reservas_ambientes_feitas", {"docente", "servidor"}),
        ("salas_ambientes", {"servidor"}),
        ("coordenacao_dashboard", {"servidor"}),
        ("coordenacao_alunos", {"servidor"}),
        ("validar_cadastros_alunos", {"servidor"}),
        ("importar_ingressantes", {"servidor"}),
        ("coordenacao_processos", {"servidor"}),
        ("coordenacao_caixa_processos", {"servidor"}),
        ("setores_comissoes", {"servidor"}),
        ("solicitacoes_assinatura", {"docente", "servidor"}),
        ("nova_solicitacao_assinatura", {"servidor"}),
        ("pendencias_assinatura", {"docente", "servidor"}),
        ("matriculas_periodos", {"servidor"}),
        ("matriculas_solicitacoes", {"servidor"}),
        ("matriculas_disciplinas", {"servidor"}),
        ("matriculas_ofertas", {"docente", "servidor"}),
    ]

    def test_toda_tela_de_detalhe_responde_para_todo_perfil(self):
        """As telas que precisam de um id, que a lista acima nao alcanca.

        Existe pelo mesmo motivo da lista, e por uma falha concreta: o filtro
        url_protegida foi acrescentado a processo_detalhe.html e a
        solicitacao_assinatura_detalhe.html, e nenhum teste abria essas duas
        telas -- elas exigem um objeto, entao ficaram de fora da lista de rotas
        sem argumento. Um {% templatetag openblock %} load {% templatetag closeblock %} esquecido teria passado pela suite
        inteira.

        Filtro invalido estoura na compilacao do template, mesmo em ramo que nao
        seja tomado; abrir a tela ja e suficiente para pegar esse caso.
        """
        setor = Setor.objects.create(nome="Setor das Telas", descricao="Teste")
        processo = Processo.objects.create(
            tipo=Processo.TipoProcesso.OUTRO, assunto="Processo das telas",
            descricao="Teste de renderizacao", usuario_criado_por=self.aluno,
            setor_atual=setor,
        )
        # Com arquivo: e o documento com arquivo que exercita o link protegido.
        Documento.objects.create(
            processo=processo, titulo="Anexo das telas", enviado_por=self.servidor,
            restricao_tipo=Documento.RestricaoAcesso.NAO,
            arquivo=SimpleUploadedFile("anexo-telas.txt", b"conteudo"),
        )
        assinatura = SolicitacaoAssinatura.objects.create(
            criado_por=self.servidor,
            destinatario_tipo=SolicitacaoAssinatura.DestinatarioTipo.DOCENTE,
            docente=self.docente,
            tipo_documento=SolicitacaoAssinatura.TipoDocumento.DOCUMENTO_SEI,
            numero_documento_sei="SEI-0001",
        )

        telas = [
            ("processo_detalhe", [processo.id], {"aluno", "docente", "servidor"}),
            ("aluno_detalhe", [self.aluno.id], {"aluno", "docente", "servidor"}),
            ("solicitacao_assinatura_detalhe", [assinatura.id], {"docente", "servidor"}),
        ]
        usuarios = {"aluno": self.aluno, "docente": self.docente, "servidor": self.servidor}

        for nome_rota, argumentos, perfis_com_acesso in telas:
            for perfil, usuario in usuarios.items():
                with self.subTest(tela=nome_rota, perfil=perfil):
                    self.client.force_login(usuario)
                    resposta = self.client.get(reverse(nome_rota, args=argumentos))
                    esperado = 200 if perfil in perfis_com_acesso else 403
                    self.assertEqual(
                        resposta.status_code, esperado,
                        f"{nome_rota} devolveu {resposta.status_code} para {perfil}",
                    )

    def test_toda_tela_responde_para_todo_perfil(self):
        usuarios = {"aluno": self.aluno, "docente": self.docente, "servidor": self.servidor}

        for nome_rota, perfis_com_acesso in self.TELAS:
            for perfil, usuario in usuarios.items():
                with self.subTest(tela=nome_rota, perfil=perfil):
                    self.client.force_login(usuario)
                    resposta = self.client.get(reverse(nome_rota))
                    esperado = 200 if perfil in perfis_com_acesso else 403
                    self.assertEqual(
                        resposta.status_code, esperado,
                        f"{nome_rota} devolveu {resposta.status_code} para {perfil}",
                    )


class TrajetoriasNaFichaDoAlunoTests(TestCase):
    """Como as trajetorias sao apresentadas na ficha.

    Um aluno pode ter varias: mestrado concluido e doutorado em curso, um
    trancamento, um reingresso. Todas vinham abertas, uma embaixo da outra, cada
    uma com onze linhas de dados mais publicacoes, disciplinas e horas
    complementares -- a trajetoria em curso, que e o motivo da visita, ficava
    soterrada pelo historico.
    """

    @classmethod
    def setUpTestData(cls):
        senha = "senha-segura-123"
        cls.docente = Docente.objects.create_user(
            email="orientador.traj@example.com", password=senha, nome="Orientador Traj",
            tipo_usuario=User.TipoUsuario.DOCENTE,
        )
        cls.aluno = Aluno.objects.create_user(
            email="aluno.traj@example.com", password=senha, nome="Aluno Traj",
            tipo_usuario=User.TipoUsuario.ALUNO, matricula="2026T0001",
        )
        cls.servidor = User.objects.create_user(
            email="servidor.traj@example.com", password=senha, nome="Servidor Traj",
            tipo_usuario=User.TipoUsuario.SERVIDOR,
        )
        cls.concluida = TrajetoriaAcademica.objects.create(
            aluno=cls.aluno, orientador=cls.docente, nivel_curso="MESTRADO",
            ingresso="2021.1", status=TrajetoriaAcademica.Status.CONCLUIDA,
            numero_defesa="ATA-2023-14", data_defesa=date(2023, 8, 18),
        )
        cls.ativa = TrajetoriaAcademica.objects.create(
            aluno=cls.aluno, orientador=cls.docente, nivel_curso="DOUTORADO",
            ingresso="2024.1", status=TrajetoriaAcademica.Status.ATIVA,
        )
        cls.url = reverse("aluno_detalhe", args=[cls.aluno.id])

    def test_as_trajetorias_comecam_fechadas(self):
        """A tela abre mostrando quantas existem, nao o conteudo de todas."""
        self.client.force_login(self.aluno)
        corpo = self.client.get(self.url).content.decode()

        blocos = re.findall(r"<details class=\"trajetoria\"[^>]*>", corpo)
        self.assertEqual(len(blocos), 2, "as duas trajetorias devem estar na tela")
        self.assertEqual(
            [bloco for bloco in blocos if "open" in bloco], [],
            f"nenhuma trajetoria abre sozinha; abertas: {blocos}",
        )

    def test_a_trajetoria_ativa_vem_primeiro(self):
        """Fechadas, a ordem e o que coloca a trajetoria em curso a um clique.

        A ordenacao era so por data de criacao; a que esta em curso podia cair
        no fim, atras de mestrados concluidos anos antes.
        """
        self.client.force_login(self.aluno)
        resposta = self.client.get(self.url)
        ordem = [card["obj"].id for card in resposta.context["trajetoria_cards"]]
        self.assertEqual(ordem[0], self.ativa.id, "a trajetoria ativa deve ser a primeira")

    def test_o_resumo_fechado_identifica_a_trajetoria(self):
        """Fechada, a trajetoria ainda precisa dizer qual e.

        Sem isso o retraimento troca uma tela longa por uma lista de blocos
        indistinguiveis.
        """
        self.client.force_login(self.aluno)
        corpo = self.client.get(self.url).content.decode()

        resumos = re.findall(r"<summary class=\"trajetoria-resumo\">(.*?)</summary>", corpo, re.S)
        self.assertEqual(len(resumos), 2)
        for resumo, trajetoria in zip(resumos, (self.ativa, self.concluida)):
            with self.subTest(trajetoria=trajetoria.id):
                self.assertIn(trajetoria.get_nivel_curso_display(), resumo)
                self.assertIn(trajetoria.get_status_display(), resumo)
                self.assertIn(trajetoria.ingresso, resumo)
                self.assertIn(trajetoria.orientador.nome, resumo)

    def test_leitor_recebe_grade_em_vez_de_linhas_com_botao(self):
        """Quem nao edita nao precisa de uma coluna de acoes vazia por linha.

        Os onze campos cabem em quatro linhas de tres colunas. Como lista
        editavel, sao onze linhas com o lado direito em branco.
        """
        for quem, usuario in (("aluno", self.aluno), ("orientador", self.docente)):
            with self.subTest(leitor=quem):
                self.client.force_login(usuario)
                corpo = self.client.get(self.url).content.decode()
                self.assertNotIn("info-list-editavel", corpo)
                self.assertIn("dados-grid", corpo)

    def test_coordenacao_continua_editando_campo_por_campo(self):
        self.client.force_login(self.servidor)
        corpo = self.client.get(self.url).content.decode()
        self.assertIn("info-list-editavel", corpo)
        self.assertIn(f"modal-trajetoria-orientador-{self.ativa.id}", corpo)

    def test_as_duas_leituras_mostram_os_mesmos_campos(self):
        """A lista de campos vem da view, nao escrita duas vezes no template.

        Enquanto o template listava os campos na mao em cada ramo, era possivel
        acrescentar um campo para a coordenacao e esquecer o do aluno.
        """
        rotulos = [linha["rotulo"] for linha in _linhas_trajetoria(self.ativa)]

        corpos = {}
        for quem, usuario in (("aluno", self.aluno), ("servidor", self.servidor)):
            self.client.force_login(usuario)
            corpos[quem] = self.client.get(self.url).content.decode()

        for rotulo in rotulos:
            for quem, corpo in corpos.items():
                with self.subTest(campo=rotulo, leitor=quem):
                    self.assertIn(rotulo, corpo)

    def test_campos_seguem_o_nivel_do_curso(self):
        """Nivel sem prazos academicos nao ganha linhas de prazo em branco."""
        rotulos_doutorado = {linha["rotulo"] for linha in _linhas_trajetoria(self.ativa)}
        self.assertIn("Orientador", rotulos_doutorado)
        self.assertIn("Prazo defesa", rotulos_doutorado)
        # "Nivel" saiu: o titulo do bloco ja e o nivel do curso.
        self.assertNotIn("Nível", rotulos_doutorado)

    def test_conclusao_junta_numero_e_data_da_ata(self):
        linhas = {linha["rotulo"]: linha["valor"] for linha in _linhas_trajetoria(self.concluida)}
        valor = linhas[self.concluida.conclusao_label]
        self.assertIn("ATA-2023-14", valor)
        self.assertIn("18/08/2023", valor)

    def test_campo_sem_valor_mostra_travessao(self):
        """Nao string vazia nem "None": a ficha e lida como documento."""
        linhas = {linha["rotulo"]: linha["valor"] for linha in _linhas_trajetoria(self.ativa)}
        self.assertEqual(linhas["Coorientador"], "—")


class CategoriaDaDisciplinaTests(TestCase):
    """A categoria da disciplina passou a ser um conjunto fechado.

    Era um CharField de 120 caracteres sem choices, digitado a mao no cadastro.
    Em 47 disciplinas isso produziu cinco grafias para tres categorias --
    "Disciplina Basica" sem acento ao lado de "Disciplina Básica", mais um
    "Obrigatória" solto. Enquanto o tipo for texto livre nao ha como filtrar por
    categoria nem contar eletivas cursadas, que a integralizacao precisa saber.
    """

    def test_o_campo_nao_aceita_texto_livre(self):
        """A garantia que impede as grafias de voltarem."""
        with self.assertRaises(ValidationError):
            Disciplina.objects.create(codigo="PPGEC900", nome="Teste", tipo="Disciplina Basica")

    def test_as_tres_categorias_do_programa(self):
        self.assertEqual(
            [valor for valor, _ in Disciplina.Tipo.choices],
            ["BASICA", "ELETIVA_GERAL", "ELETIVA_ESPECIFICA"],
        )

    def test_a_tela_mostra_o_rotulo_e_nao_a_chave(self):
        """Sem get_tipo_display a tabela mostraria "ELETIVA_ESPECIFICA"."""
        Disciplina.objects.create(
            codigo="PPGEC901", nome="Disciplina de teste",
            tipo=Disciplina.Tipo.ELETIVA_ESPECIFICA,
        )
        servidor = User.objects.create_user(
            email="servidor.disc@example.com", password="senha-segura-123",
            nome="Servidor Disc", tipo_usuario=User.TipoUsuario.SERVIDOR,
        )
        self.client.force_login(servidor)
        resposta = self.client.get(reverse("matriculas_disciplinas"))

        # A chave aparece legitimamente no value do <option> do formulario de
        # edicao; o que nao pode e vazar para a celula da tabela.
        celulas = re.findall(r"<td[^>]*>(.*?)</td>", resposta.content.decode(), re.S)
        self.assertTrue(
            any("Eletiva específica" in celula for celula in celulas),
            "a tabela deve mostrar o rotulo da categoria",
        )
        self.assertFalse(
            any("ELETIVA_ESPECIFICA" in celula for celula in celulas),
            "a chave do choice nao deve aparecer na tabela",
        )

    def test_categoria_em_branco_continua_valida(self):
        """Disciplina antiga sem classificacao nao bloqueia o cadastro.

        A migracao deixa em branco o que nao reconhece, para que a coordenacao
        veja o que falta classificar em vez de a leitura quebrar.
        """
        disciplina = Disciplina.objects.create(codigo="PPGEC902", nome="Sem categoria")
        self.assertEqual(disciplina.tipo, "")
        self.assertEqual(disciplina.get_tipo_display(), "")


class NomeDoColegiadoTests(TestCase):
    """O colegiado pleno e reconhecido pelo nome, e o nome estava errado.

    "Colegiando" e erro de digitacao -- a descricao do proprio setor sempre disse
    "Deliberacoes do colegiado pleno." O erro entrou pela migracao 0005, que
    listava o nome correto entre os apelidos a renomear.
    """

    ARQUIVOS_DE_CODIGO = ["views.py", "context_processors.py", "models.py", "forms.py", "services.py"]

    def test_nenhum_codigo_compara_com_a_grafia_errada(self):
        """O nome tem um lugar so: Setor.NOME_PLENO.

        Enquanto estava escrito a mao em quatro comparacoes, corrigir o dado sem
        corrigir todas as copias faria o sistema deixar de reconhecer o pleno --
        nenhum docente veria "Processos no Pleno" e ninguem poderia deliberar.
        """
        base = Path(settings.BASE_DIR) / "processos"
        culpados = []
        for nome in self.ARQUIVOS_DE_CODIGO:
            caminho = base / nome
            if not caminho.exists():
                continue
            for numero, linha in enumerate(caminho.read_text(encoding="utf-8").splitlines(), 1):
                if "Colegiando" in linha and not linha.lstrip().startswith("#"):
                    culpados.append(f"{nome}:{numero}")
        self.assertEqual(culpados, [], f"grafia errada no codigo: {culpados}")

    def test_a_constante_diz_colegiado(self):
        self.assertEqual(Setor.NOME_PLENO, "Colegiado PPGEC (Pleno)")

    def test_o_pleno_continua_sendo_reconhecido(self):
        """A correcao do dado nao pode ter desligado o acesso ao pleno."""
        docente = Docente.objects.create_user(
            email="docente.pleno.nome@example.com", password="senha-segura-123",
            nome="Docente Pleno", tipo_usuario=User.TipoUsuario.DOCENTE,
        )
        pleno, _ = Setor.objects.get_or_create(
            nome=Setor.NOME_PLENO,
            defaults={"descricao": "Deliberações do colegiado pleno."},
        )
        SetorMembro.objects.create(setor=pleno, usuario=docente)

        self.client.force_login(docente)
        resposta = self.client.get(reverse("menu_processos_pleno"))

        self.assertEqual(resposta.status_code, 200)
        rotulos = [
            item["label"]
            for secao in resposta.context["nav_menu_sections"]
            for item in secao["items"]
        ]
        self.assertIn("Processos no Pleno", rotulos)


class CampoDeDataEHoraTests(SimpleTestCase):
    """Data e hora sao escritas em formato brasileiro, seja qual for o navegador.

    <input type="date"> e <input type="time"> sao desenhados pelo navegador, e o
    formato segue o idioma da interface dele -- nao o lang da pagina, nao o
    Accept-Language. Num Chrome em ingles, uma data aparece "03/15/2026" e uma
    hora "02:30 PM" num sistema em portugues. Testei lang no input, lang no
    elemento pai e locale do contexto: nenhum dos tres muda o formato.

    A solucao mostra um campo de texto sob nosso controle e mantem o nativo ao
    lado, invisivel, para enviar o valor em ISO e abrir o seletor pelo
    showPicker(). Estes testes protegem as duas pontas disso.
    """

    DIRETORIO = Path(settings.BASE_DIR) / "templates"

    def test_o_script_de_formatacao_esta_na_base(self):
        base = (self.DIRETORIO / "base.html").read_text(encoding="utf-8")
        self.assertIn("campo-datahora", base)
        self.assertIn("showPicker", base)

    def test_os_templates_mantem_o_campo_nativo(self):
        """A troca e progressiva: sem JavaScript, o campo nativo continua valendo.

        Se alguem "resolver" o formato trocando type="date" por type="text" no
        template, perde-se o seletor de calendario, o teclado numerico do
        celular e o envio em ISO -- e o campo passa a depender do script para
        funcionar, em vez de ser melhorado por ele.
        """
        suspeitos = []
        for caminho in sorted(self.DIRETORIO.rglob("*.html")):
            if "emails" in caminho.parts:
                continue
            texto = caminho.read_text(encoding="utf-8")
            for campo in re.findall(r'<input[^>]*name="[^"]*(?:data|hora)[^"]*"[^>]*>', texto):
                if 'type="text"' in campo and "dd/mm" not in campo:
                    suspeitos.append(f"{caminho.name}: {campo[:70]}")
        self.assertEqual(suspeitos, [], f"campo de data/hora como texto no template: {suspeitos}")
class LayoutResponsivoTests(SimpleTestCase):
    """As grades de cartoes colapsam para uma coluna em tela estreita.

    .dashboard-grid ficou preso, por engano, na lista de seletores de uma regra
    de "display: inline-flex" -- a que transforma o botao do menu em gaveta
    abaixo de 920px. Como caixa inline-flex a grade passa a ter a largura do
    proprio conteudo, e nao a do pai: em 412px de tela ela media 430px, e o
    segundo cartao aparecia cortado na borda da tela.

    O defeito e dificil de ver de outra forma: nao ha transbordo de pagina para
    medir (o corte acontece dentro do container) e a tela so quebra em largura de
    celular. Aqui a intencao fica escrita -- estas grades sao grade, e em tela
    estreita sao de uma coluna so.
    """

    GRADES = (".dashboard-grid", ".metric-grid", ".grid-two")
    LARGURA_DE_CELULAR = 920

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        css = (Path(settings.BASE_DIR) / "static" / "css" / "app.css").read_text(encoding="utf-8")
        cls.css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)

    def _regras(self, dentro_de_media=None):
        """(seletores, declaracoes) das regras do arquivo.

        dentro_de_media=None percorre o arquivo inteiro; um inteiro restringe aos
        blocos @media (max-width: N) com N menor ou igual ao valor dado.
        """
        if dentro_de_media is None:
            fonte = re.sub(r"@media[^{]*\{", "", self.css)
        else:
            fonte = ""
            for largura, bloco in re.findall(r"@media\s*\(max-width:\s*(\d+)px\)\s*\{(.*?)\n\}", self.css, re.S):
                if int(largura) <= dentro_de_media:
                    fonte += bloco
        for seletores, declaracoes in re.findall(r"([^{}]+)\{([^{}]*)\}", fonte):
            yield [s.strip() for s in seletores.split(",") if s.strip()], declaracoes

    def test_as_grades_nunca_deixam_de_ser_grade(self):
        """Nenhuma regra pode dar a elas um display que nao seja grid.

        E a asercao que teria pego o defeito: o seletor caiu numa regra de
        display, e a grade virou flex.
        """
        culpados = []
        for seletores, declaracoes in self._regras():
            display = re.search(r"(?:^|;)\s*display\s*:\s*([^;]+)", declaracoes)
            if not display or display.group(1).strip() == "grid":
                continue
            for grade in self.GRADES:
                if grade in seletores:
                    culpados.append(f"{grade} recebe display:{display.group(1).strip()}")
        self.assertEqual(culpados, [], f"grade com display trocado: {culpados}")

    def test_as_grades_viram_uma_coluna_em_tela_estreita(self):
        de_uma_coluna = set()
        for seletores, declaracoes in self._regras(dentro_de_media=self.LARGURA_DE_CELULAR):
            colunas = re.search(r"grid-template-columns\s*:\s*([^;]+)", declaracoes)
            if colunas and colunas.group(1).strip() == "1fr":
                de_uma_coluna.update(seletores)

        faltando = [g for g in self.GRADES if g not in de_uma_coluna]
        self.assertEqual(
            faltando, [],
            f"grades sem colapso para uma coluna ate {self.LARGURA_DE_CELULAR}px: {faltando}",
        )

    # Blocos que nao cabem num celular e nao devem encolher: uma tabela de sete
    # colunas e a grade da semana, onde um dia de 40px nao mostra nome nenhum.
    # A saida e rolarem dentro de si, e nao alargarem a pagina.
    ENVOLVENTES_QUE_ROLAM = (".tabela-envolvido", ".grade-horario-envolvido")

    # Grades cujos itens tem largura imprevisivel. Item de grade nasce com
    # min-width: auto e nao encolhe abaixo do proprio conteudo -- o que sobra
    # vira largura de pagina.
    GRADES_QUE_ENCOLHEM = (".pilha-trajetorias > *", ".stack > *")

    def _declaracoes_de(self, seletor):
        for seletores, declaracoes in self._regras():
            if seletor in seletores:
                yield declaracoes

    def test_o_que_e_largo_demais_rola_dentro_de_si(self):
        """Pagina mais larga que a tela agora custa a navegacao.

        Ate a barra flutuante existir, transbordar na horizontal custava uma
        rolagem lateral. Com ela, custa a propria barra: em celular o
        position: fixed se ancora na pagina, e nao no pedaco visivel dela, entao
        uma pagina de 714px numa tela de 390 leva a barra para fora do campo de
        visao. Foi assim que o defeito apareceu -- como sumico de navegacao.
        """
        for seletor in self.ENVOLVENTES_QUE_ROLAM:
            with self.subTest(envolvente=seletor):
                rola = any(
                    re.search(r"overflow(-x)?\s*:\s*(auto|scroll)", d)
                    for d in self._declaracoes_de(seletor)
                )
                self.assertTrue(rola, f"{seletor} nao rola: o excedente vira largura de pagina")

    def test_o_envolvente_da_tabela_contem_os_absolutos(self):
        """Descendente absoluto so e recortado por quem for bloco de contencao.

        O rotulo "Acoes" da coluna de botoes e escondido com .apenas-leitor, que
        e position: absolute. Sem position no envolvente, ele subia para o bloco
        inicial: 1px de largura, invisivel, ancorado a 714px do inicio -- e a
        pagina media 714px com a tabela rolando certinho dentro do cartao.
        """
        posicionado = any(
            re.search(r"position\s*:\s*(relative|sticky)", d)
            for d in self._declaracoes_de(".tabela-envolvido")
        )
        self.assertTrue(posicionado, ".tabela-envolvido sem position: os absolutos escapam da rolagem")

    def test_a_folha_entra_e_sai_pelo_mesmo_caminho(self):
        """Fechar era um corte seco, o oposto do que a abertura ensinava.

        Com @keyframes so havia entrada. Transicao serve aos dois sentidos, mas
        num <dialog> ela sozinha nao basta: display e overlay sao discretos, e
        sem allow-discrete o navegador aplica display:none no primeiro quadro --
        a saida existe no papel e nao chega a ser vista. @starting-style e o
        outro lado: sem ele o elemento nasce ja no estado final e a entrada nao
        tem de onde partir.
        """
        declaracoes = list(self._declaracoes_de(".folha-navegacao"))
        transicao = " ".join(declaracoes)
        self.assertRegex(transicao, r"transition\s*:", "a folha nao declara transicao")
        for discreta in ("display", "overlay"):
            with self.subTest(propriedade=discreta):
                self.assertRegex(
                    transicao, rf"{discreta}\s+[^;,]*allow-discrete",
                    f"{discreta} sem allow-discrete: a saida nao aparece",
                )
        self.assertIn("@starting-style", self.css, "sem @starting-style a folha nao tem entrada")

    def test_os_itens_de_toque_respondem_ao_dedo(self):
        """O hover ficou atras de (hover: hover) para nao grudar no ultimo item
        tocado, e isso deixou o toque sem sinal nenhum ate a tela seguinte
        chegar. :active dura o tempo do dedo encostado."""
        alvos = (".barra-flutuante-item", ".barra-flutuante-acao", ".folha-item")
        # Exige a mudanca de fundo, e nao so a existencia do seletor: o bloco de
        # movimento reduzido tambem casa ":active", e la a declaracao e
        # scale: 1 -- ou seja, a retirada do movimento, nao o retorno. Sem esta
        # condicao o teste passava com o retorno apagado.
        com_retorno = set()
        for seletores, declaracoes in self._regras():
            if not re.search(r"(?:^|;)\s*background(-color)?\s*:", declaracoes):
                continue
            for seletor in seletores:
                if seletor.endswith(":active"):
                    com_retorno.add(seletor[: -len(":active")])
        faltando = [a for a in alvos if a not in com_retorno]
        self.assertEqual(faltando, [], f"itens sem retorno ao toque: {faltando}")

    def test_as_grades_de_item_largo_deixam_o_item_encolher(self):
        for seletor in self.GRADES_QUE_ENCOLHEM:
            with self.subTest(grade=seletor):
                encolhe = any(
                    re.search(r"min-width\s*:\s*0", d) for d in self._declaracoes_de(seletor)
                )
                self.assertTrue(encolhe, f"{seletor} sem min-width: 0")


class VersaoExibidaTests(SimpleTestCase):
    """A versao que aparece no rodape precisa ter forma de versao.

    Em producao o rodape de todas as telas exibiu "vmain". A esteira passava
    github.ref_name como APP_VERSION, o que so vira uma versao quando o build
    sai de uma tag; disparada por push em main -- o caso de todo merge de PR --
    ela entregava o nome do branch.

    A esteira passou a ler o arquivo VERSION. Estes testes cobrem o outro lado:
    que a aplicacao nao aceite no lugar da versao um valor que nao seja uma.
    """

    @property
    def versao_do_arquivo(self):
        """Lida do arquivo, nao fixada aqui.

        Com o numero escrito no teste, subir a versao do projeto quebrava a
        suite -- e o que se quer verificar e que o valor invalido cai para o
        arquivo, nao que o arquivo diga 1.0.0.
        """
        return (Path(settings.BASE_DIR) / "VERSION").read_text(encoding="utf-8").strip()

    def _resolver(self, valor):
        from ppgec.settings import _versao_publicada

        with patch.dict(os.environ, {"APP_VERSION": valor} if valor is not None else {}, clear=False):
            if valor is None:
                os.environ.pop("APP_VERSION", None)
            return _versao_publicada()

    def test_o_arquivo_version_tem_forma_de_versao(self):
        """O arquivo e a fonte da verdade; se ele estiver errado, tudo esta."""
        from ppgec.settings import _FORMATO_VERSAO

        conteudo = (Path(settings.BASE_DIR) / "VERSION").read_text(encoding="utf-8").strip()
        self.assertTrue(
            _FORMATO_VERSAO.match(conteudo),
            f"VERSION contem {conteudo!r}, que nao tem forma de versao",
        )

    def test_nome_de_branch_no_ambiente_e_recusado(self):
        """O caso exato que produziu o "vmain"."""
        for nome in ("main", "master", "feature/melhorias-ux", "HEAD"):
            with self.subTest(ref=nome):
                with self.assertWarns(RuntimeWarning):
                    resolvido = self._resolver(nome)
                self.assertEqual(resolvido, self.versao_do_arquivo, "deve cair para a versao do arquivo")

    def test_versao_valida_no_ambiente_e_usada(self):
        """A esteira precisa conseguir sobrescrever com uma versao de verdade."""
        for valor, esperado in (("1.2.3", "1.2.3"), ("2.0", "2.0"), ("1.2.0-rc.1", "1.2.0-rc.1")):
            with self.subTest(valor=valor):
                self.assertEqual(self._resolver(valor), esperado)

    def test_prefixo_v_e_descartado(self):
        """Quem exibe ja escreve o "v".

        Se o build vier de uma tag "v1.0.0" e esse valor passar adiante inteiro,
        o rodape renderiza "vv1.0.0".
        """
        self.assertEqual(self._resolver("v1.0.0"), "1.0.0")

    def test_ambiente_vazio_usa_o_arquivo(self):
        self.assertEqual(self._resolver(""), self.versao_do_arquivo)
        self.assertEqual(self._resolver(None), self.versao_do_arquivo)

    def test_o_rodape_mostra_a_versao_do_arquivo(self):
        """Ponta a ponta: o que o usuario le na tela."""
        resposta = self.client.get(reverse("login"))
        self.assertContains(resposta, f"v{settings.APP_VERSION}")
        self.assertNotContains(resposta, "vmain")


class PadraoVisualDosTemplatesTests(SimpleTestCase):
    """Guarda os padroes que a revisao visual estabeleceu.

    Cada um destes ja foi corrigido tela por tela e volta sozinho na proxima
    tela nova, porque escrever "a | b | c" e mais rapido do que montar a
    meta-linha. Como sao verificaveis no proprio texto do template, ficam aqui.

    Nao e um teste de aparencia -- e de consistencia: o que ele impede e que
    duas telas resolvam a mesma coisa de dois jeitos.
    """

    DIRETORIO = Path(settings.BASE_DIR) / "templates"

    # Templates de e-mail ficam de fora: cliente de e-mail ignora <style>, e o
    # estilo inline ali e o unico que funciona. A regra vale para as telas.
    EXCECOES = ("emails",)

    def _templates(self):
        for caminho in sorted(self.DIRETORIO.rglob("*.html")):
            if any(parte in caminho.parts for parte in self.EXCECOES):
                continue
            yield caminho, caminho.read_text(encoding="utf-8")

    def _sem_comentarios(self, texto):
        """Comentarios explicam o que foi corrigido e costumam citar o defeito."""
        return re.sub(r"{%\s*comment\s*%}.*?{%\s*endcomment\s*%}", "", texto, flags=re.S)

    def _somente_texto_visivel(self, texto):
        """Deixa so o que o usuario le.

        Tira comentarios, <script>, <style>, o titulo da aba e toda a sintaxe do
        Django. Sem isso, procurar "|" acha filtro de template ({{ x|date }}),
        "||" de JavaScript e barra de CSS -- nada disso e separador de dado.
        """
        def apagar(match):
            # Preserva as quebras de linha do trecho removido, senao o numero de
            # linha reportado nao corresponde ao arquivo e o teste aponta para o
            # lugar errado.
            return "\n" * match.group(0).count("\n")

        for padrao in (
            r"{%\s*comment\s*%}.*?{%\s*endcomment\s*%}",
            r"<script\b.*?</script>",
            r"<style\b.*?</style>",
            r"<!--.*?-->",
            # "Pagina | Sistema" no <title> e convencao de aba, nao dado numa linha.
            r"{%\s*block title\s*%}.*?{%\s*endblock\s*%}",
            r"{{.*?}}",
            r"{%.*?%}",
        ):
            texto = re.sub(padrao, apagar, texto, flags=re.S)
        return texto

    def test_nenhum_template_usa_bloco_style(self):
        """CSS de tela vive no app.css.

        Um <style> dentro do template so vale na pagina que o renderiza. A ficha
        do aluno guardava assim o componente de modal, e a tela de ofertas
        guardava a grade de horario -- ambos usados em mais de um lugar, ambos
        invisiveis para quem procurasse a regra no app.css.
        """
        culpados = [
            caminho.name
            for caminho, texto in self._templates()
            if "<style" in self._sem_comentarios(texto)
        ]
        self.assertEqual(culpados, [], f"templates com <style> proprio: {culpados}")

    def test_nenhum_template_usa_o_padrao_antigo_de_formulario(self):
        """Campo e faixa de botoes tem uma classe so em todo o projeto.

        As duas conviveram: .field/.form-actions, que so davam margem e
        alinhamento, e .formulario-campo/.formulario-acoes, que trazem o rotulo
        em peso, a marca de obrigatorio, o erro em vermelho e a linha acima dos
        botoes. Na tela de novo processo as duas apareciam juntas -- o
        formulario no padrao novo e o modal dele no antigo.

        As regras antigas foram removidas do app.css; escrever a classe antiga
        hoje nao produz nem o estilo antigo, apenas um campo sem estilo nenhum.
        """
        antigas = ('class="field"', 'class="field ', 'class="form-actions"', "hidden-field")
        culpados = []
        for caminho, texto in self._templates():
            limpo = self._sem_comentarios(texto)
            for antiga in antigas:
                if antiga in limpo:
                    culpados.append(f"{caminho.name}: {antiga}")
        self.assertEqual(culpados, [], f"padrao antigo de formulario: {culpados}")

    def test_nenhum_template_separa_dados_com_pipe(self):
        """Metadados usam .meta-linha, nao "a | b | c".

        Com pipe escrito na mao, um campo vazio deixa o separador solto na
        linha, e cada template resolvia isso de um jeito -- ou nao resolvia.
        """
        culpados = []
        for caminho, texto in self._templates():
            for numero, linha in enumerate(self._somente_texto_visivel(texto).splitlines(), 1):
                if "|" in linha:
                    culpados.append(f"{caminho.name}:{numero}")
        self.assertEqual(culpados, [], f"pipes separando dados: {culpados}")

    def test_nenhum_template_envolve_os_cartoes_num_container_sem_classe(self):
        """Os cartoes de uma tela precisam ser filhos diretos da area de conteudo.

        O espacamento vertical entre blocos vem de ".content-area > * + *", que
        por definicao alcanca so filhos diretos. Um envolvente entre a area e os
        cartoes -- mesmo um <div> vazio -- tira todos eles do alcance da regra, e
        a tela passa a ter os cartoes encostados.

        Aconteceu duas vezes, e nas duas o envolvente nao fazia nada: na ficha do
        aluno era um <section> sem atributo nenhum, e na tela de ofertas era um
        <div class="matriculas-ofertas-page"> que havia servido para dar escopo a
        um <style> ja removido.

        E dificil de ver medindo a tela renderizada: com tudo dentro de um
        envolvente, a area de conteudo tem um unico filho, e nao existe folga
        alguma para medir como errada. Por isso a verificacao e no template.
        """
        # Um envolvente e legitimo quando ele mesmo espaca os filhos -- uma grade
        # com gap, por exemplo (.grid-two, .dashboard-grid). O conjunto vem do
        # proprio app.css, lendo quais classes declaram gap: assim uma grade nova
        # passa a ser aceita sem precisar mexer neste teste.
        css = (Path(settings.BASE_DIR) / "static" / "css" / "app.css").read_text(encoding="utf-8")
        com_gap = set()
        for seletor, corpo_regra in re.findall(r"([^{}]+)\{([^{}]*)\}", css):
            if re.search(r"(^|[;\s])gap\s*:", corpo_regra):
                com_gap.update(re.findall(r"\.([a-z0-9-]+)", seletor))

        abertura = re.compile(r"<(div|section|main|article)\b([^>]*)>")
        culpados = []
        for caminho, texto in self._templates():
            corpo = self._sem_comentarios(texto)
            bloco = re.search(r"{%\s*block content\s*%}(.*?){%\s*endblock", corpo, re.S)
            if not bloco:
                continue
            conteudo = bloco.group(1).lstrip()
            primeiro = abertura.match(conteudo)
            if not primeiro:
                continue

            dentro = self._conteudo_do_elemento(conteudo, primeiro)
            classes = set(re.findall(r"[a-z0-9-]+", re.search(r'class="([^"]*)"', primeiro.group(2)).group(1))) \
                if 'class="' in primeiro.group(2) else set()
            # Conta os cartoes DENTRO do primeiro elemento, nao no conteudo
            # inteiro: um cabecalho de pagina que abre a tela e irmao dos cartoes,
            # nao envolvente deles, e nao tem nada a ver com o ritmo entre eles.
            if dentro.count('class="card') > 1 and not (classes & (com_gap | {"card"})):
                culpados.append(f"{caminho.name}: <{primeiro.group(1)}{primeiro.group(2)}>")
        self.assertEqual(culpados, [], f"cartoes fora do alcance do ritmo vertical: {culpados}")

    @staticmethod
    def _conteudo_do_elemento(html, abertura):
        """O que esta entre a tag de abertura encontrada e o seu fechamento."""
        tag = abertura.group(1)
        profundidade = 0
        for marca in re.finditer(rf"<(/?){tag}\b[^>]*>", html):
            profundidade += -1 if marca.group(1) else 1
            if profundidade == 0:
                return html[abertura.end():marca.start()]
        # Sem fechamento correspondente (o template pode fechar noutro bloco):
        # considera tudo o que vem depois, que e a leitura conservadora.
        return html[abertura.end():]

    def test_nenhum_elemento_tem_class_duplicado(self):
        """Dois atributos class no mesmo elemento: o navegador ignora o segundo.

        E um defeito silencioso -- o HTML e valido o bastante para renderizar, a
        pagina nao quebra, e a classe simplesmente nao se aplica. Aconteceu ao
        acrescentar uma classe a elementos que ja tinham uma: sairam tres
        <h2 class="section-title" class="titulo-interno"> e um
        <div class="actions-row" class="secao-cabecalho">, todos sem o efeito
        pretendido.
        """
        culpados = []
        for caminho, texto in self._templates():
            for numero, linha in enumerate(texto.splitlines(), 1):
                if re.search(r'class="[^"]*"\s+class="', linha):
                    culpados.append(f"{caminho.name}:{numero}")
        self.assertEqual(culpados, [], f"elementos com class duplicado: {culpados}")

    def test_nenhum_template_carrega_anotacao_de_desenvolvimento(self):
        """TODO/FIXME nao sao conteudo de tela.

        A tela de documento de vinculo exibia ao aluno, como texto da pagina,
        "TODO: disponibilizar emissao do documento de vinculo."
        """
        culpados = [
            caminho.name
            for caminho, texto in self._templates()
            if re.search(r"\b(TODO|FIXME|XXX)\b", self._sem_comentarios(texto))
        ]
        self.assertEqual(culpados, [], f"templates com anotacao de desenvolvimento: {culpados}")


class BarraFlutuanteTests(TestCase):
    """A barra de navegacao que aparece em tela estreita.

    Abaixo de 920px a barra lateral vira gaveta, e a navegacao inteira passava a
    viver atras do botao no canto superior esquerdo -- o ponto mais distante do
    polegar --, com uma lista de oito a vinte e um destinos do outro lado do
    toque.

    A barra monta a partir dos itens do proprio menu, e nao de uma segunda lista
    de rotas: e o que impede que as duas discordem sobre onde um destino fica ou
    quando ele esta ativo. O preco dessa escolha e que um rotulo que nao case com
    nada some da barra em silencio -- foi o que aconteceu com "Alunos", que mora
    dentro de um grupo do menu do servidor, enquanto a busca so olhava o primeiro
    nivel. Dai a asercao ser sobre os rotulos exatos, e nao "pelo menos um".
    """

    @classmethod
    def setUpTestData(cls):
        senha = "senha-segura-123"
        cls.aluno = Aluno.objects.create_user(
            email="aluno.barra@example.com", password=senha, nome="Aluno Barra",
            tipo_usuario=User.TipoUsuario.ALUNO, matricula="2026M0009",
        )
        cls.docente = Docente.objects.create_user(
            email="docente.barra@example.com", password=senha, nome="Docente Barra",
            tipo_usuario=User.TipoUsuario.DOCENTE,
        )
        cls.servidor = User.objects.create_user(
            email="servidor.barra@example.com", password=senha, nome="Servidor Barra",
            tipo_usuario=User.TipoUsuario.SERVIDOR,
        )

    def _menu_e_barra(self, usuario):
        """O menu e a barra da mesma construcao.

        Cada chamada a _menu_lateral_items monta itens novos, entao comparar
        objetos entre duas construcoes nunca daria identidade -- o que se quer
        garantir e que a barra devolve os itens que recebeu, sem copiar.
        """
        from processos.context_processors import _barra_flutuante, _menu_lateral_items

        itens = _menu_lateral_items(usuario)
        return itens, _barra_flutuante(usuario, itens)

    def _barra(self, usuario):
        return self._menu_e_barra(usuario)[1]

    def test_cada_perfil_recebe_os_tres_destinos_declarados(self):
        esperado = {
            "aluno": (self.aluno, ["Início", "Meus Processos", "Matrícula"]),
            "docente": (self.docente, ["Início", "Meus Processos", "Meus Orientandos"]),
            "servidor": (self.servidor, ["Início", "Caixa de Processos", "Alunos"]),
        }
        for perfil, (usuario, rotulos) in esperado.items():
            with self.subTest(perfil=perfil):
                self.assertEqual([item["label"] for item in self._barra(usuario)], rotulos)

    def test_o_destino_da_barra_e_o_mesmo_do_menu(self):
        """Endereco, icone e url_names vem do item do menu, nao de uma copia."""
        for perfil, usuario in (("aluno", self.aluno), ("docente", self.docente), ("servidor", self.servidor)):
            itens, barra = self._menu_e_barra(usuario)
            do_menu = {}
            pendentes = list(itens)
            while pendentes:
                item = pendentes.pop(0)
                do_menu.setdefault(item["label"], item)
                pendentes.extend(item["children"])
            for item in barra:
                with self.subTest(perfil=perfil, destino=item["label"]):
                    self.assertIs(item, do_menu[item["label"]])

    def test_o_circulo_de_acao_segue_a_permissao_de_abrir_processo(self):
        """Servidor nao abre processo -- a barra dele nao oferece o atalho."""
        for perfil, usuario, tem_acao in (
            ("aluno", self.aluno, True),
            ("docente", self.docente, True),
            ("servidor", self.servidor, False),
        ):
            with self.subTest(perfil=perfil):
                self.client.force_login(usuario)
                resposta = self.client.get(reverse("home"))
                self.assertEqual(bool(resposta.context["barra_flutuante_acao"]), tem_acao)
                self.assertEqual("barra-flutuante-acao" in resposta.content.decode(), tem_acao)

    def test_a_tela_atual_acende_exatamente_um_destino(self):
        """Zero destinos acesos e o defeito que a busca rasa produzia."""
        telas = {
            "aluno": (self.aluno, ["home", "menu_meus_processos", "matriculas_minhas"]),
            "docente": (self.docente, ["home", "menu_meus_processos", "menu_meus_orientandos"]),
            "servidor": (self.servidor, ["home", "coordenacao_caixa_processos", "coordenacao_alunos"]),
        }
        for perfil, (usuario, url_names) in telas.items():
            barra = self._barra(usuario)
            for url_name in url_names:
                with self.subTest(perfil=perfil, tela=url_name):
                    acesos = [item["label"] for item in barra if url_name in item["url_names"]]
                    self.assertEqual(len(acesos), 1, f"{url_name} acendeu {acesos or 'nada'} para {perfil}")

    def test_a_navegacao_completa_tem_um_gatilho_so(self):
        """A barra e atalho, nao substituicao -- mas o caminho para o resto e um.

        Quando a barra flutuante nasceu, o botao de tres tracos continuou na
        barra superior: dois gatilhos para a mesma gaveta, um deles no canto
        oposto ao polegar. Ficou o de baixo, e o que ele abre e a folha.
        """
        self.client.force_login(self.servidor)
        corpo = self.client.get(reverse("home")).content.decode()
        # aria-controls, e nao o data-: o proprio script cita o atributo ao
        # procurar o gatilho, e contar o data- somaria o botao com o codigo.
        self.assertEqual(corpo.count('aria-controls="folha-navegacao"'), 1)
        self.assertNotIn('class="menu-toggle"', corpo)
        self.assertIn('id="folha-navegacao"', corpo)

    @staticmethod
    def _marcacao_da_folha(corpo):
        """So o trecho do dialogo.

        A pagina inteira nao serve: a barra lateral continua no HTML -- oculta
        por CSS em tela estreita, mas presente -- e carrega os mesmos enderecos.
        Procurar no corpo todo dava um teste que passava com a folha vazia.
        """
        inicio = corpo.index('<dialog id="folha-navegacao"')
        return corpo[inicio:corpo.index("</dialog>", inicio)]

    def test_a_folha_leva_a_todo_destino_do_menu(self):
        """O que sai da barra tem de estar na folha, senao fica inalcancavel.

        A folha achata os grupos -- um grupo nao vira link, porque o endereco
        dele repete o do primeiro filho --, entao a conta e sobre as folhas da
        arvore, e nao sobre o primeiro nivel.
        """
        from processos.context_processors import _menu_lateral_items

        for perfil, usuario in (("aluno", self.aluno), ("docente", self.docente), ("servidor", self.servidor)):
            with self.subTest(perfil=perfil):
                self.client.force_login(usuario)
                folha = self._marcacao_da_folha(self.client.get(reverse("home")).content.decode())
                pendentes = list(_menu_lateral_items(usuario))
                while pendentes:
                    item = pendentes.pop(0)
                    if item["children"]:
                        pendentes.extend(item["children"])
                        continue
                    self.assertIn(f'href="{item["href"]}"', folha, f'{item["label"]} sumiu da folha')


class MenuDaContaFechaTests(SimpleTestCase):
    """O painel da conta fecha por fora, nao so pelo chip que o abriu.

    Ele e um <details>, e <details> abre e fecha pelo proprio resumo -- nada
    mais o alcanca. Clicar em qualquer outro ponto da tela, ou apertar Esc,
    deixava o painel aberto por cima do conteudo, e a unica saida era voltar ao
    nome no canto e clicar de novo. E a excecao entre os menus da plataforma: a
    gaveta lateral, os modais e os blocos que abrem ja fechavam assim.

    O comportamento vive em JavaScript, sem executor de JS na suite; o que da
    para garantir aqui e que os dois tratadores continuam registrados. A
    verificacao de que fecham mesmo foi feita no navegador, nos tres perfis.
    """

    def setUp(self):
        self.base = (Path(settings.BASE_DIR) / "templates" / "base.html").read_text(encoding="utf-8")

    def test_fecha_com_clique_fora(self):
        self.assertIn("details.user-menu", self.base)
        self.assertIn("!conta.contains(e.target)", self.base)

    def test_fecha_com_esc_e_devolve_o_foco(self):
        self.assertIn('e.key === "Escape" && conta.open', self.base)
        self.assertIn("resumo.focus()", self.base)


class MenuMarcaItemAtivoTests(TestCase):
    """Toda tela acende exatamente um item da barra lateral.

    A barra marca o item cujo `url_names` contem o url_name da requisicao. Telas
    de detalhe (abrir um processo, abrir a trajetoria de um aluno) nao tem
    entrada propria no menu, e ninguem as declarava na listagem de origem: abrir
    um processo apagava a barra inteira e o usuario perdia a referencia de onde
    estava.

    O outro lado do defeito e igualmente possivel: declarar o mesmo detalhe em
    dois itens acende os dois. Por isso a asercao e "exatamente um", nao "ao
    menos um" -- e por isso o coordenador entra no teste, que e o perfil que
    acumula as telas pessoais e as da Coordenacao.
    """

    # Telas alcancadas pela barra superior, nao pelo menu lateral. Nenhum item
    # as reivindica, e marcar uma delas seria mentir sobre a origem.
    SEM_ITEM_NO_MENU = {"me"}

    @classmethod
    def setUpTestData(cls):
        senha = "senha-segura-123"
        cls.aluno = Aluno.objects.create_user(
            email="aluno.menu@example.com", password=senha, nome="Aluno Menu",
            tipo_usuario=User.TipoUsuario.ALUNO, matricula="2026M0001",
        )
        cls.docente = Docente.objects.create_user(
            email="docente.menu@example.com", password=senha, nome="Docente Menu",
            tipo_usuario=User.TipoUsuario.DOCENTE,
        )
        cls.coordenador = Docente.objects.create_user(
            email="coord.menu@example.com", password=senha, nome="Coordenador Menu",
            tipo_usuario=User.TipoUsuario.DOCENTE, coordenador=True,
        )
        cls.servidor = User.objects.create_user(
            email="servidor.menu@example.com", password=senha, nome="Servidor Menu",
            tipo_usuario=User.TipoUsuario.SERVIDOR,
        )

    def _itens_ativos(self, usuario, url_name):
        from processos.context_processors import _menu_lateral_sections

        ativos = []
        for secao in _menu_lateral_sections(usuario):
            for item in secao["items"]:
                if url_name in item["url_names"]:
                    ativos.append(item["label"])
        return ativos

    def test_telas_de_detalhe_acendem_exatamente_um_item(self):
        """As duas telas que motivaram a correcao, em todos os perfis."""
        usuarios = {
            "aluno": self.aluno,
            "docente": self.docente,
            "coordenador": self.coordenador,
            "servidor": self.servidor,
        }
        for perfil, usuario in usuarios.items():
            for url_name in ("processo_detalhe", "aluno_detalhe"):
                with self.subTest(perfil=perfil, tela=url_name):
                    ativos = self._itens_ativos(usuario, url_name)
                    self.assertEqual(
                        len(ativos), 1,
                        f"{url_name} acendeu {ativos or 'nenhum item'} para {perfil}",
                    )

    def test_toda_tela_do_menu_acende_exatamente_um_item(self):
        """Nenhuma tela alcancavel deixa a barra sem marcacao (nem com duas)."""
        usuarios = {"aluno": self.aluno, "docente": self.docente, "servidor": self.servidor}

        for nome_rota, perfis_com_acesso in TodasAsTelasRenderizamTests.TELAS:
            if nome_rota in self.SEM_ITEM_NO_MENU:
                continue
            for perfil in perfis_com_acesso:
                with self.subTest(tela=nome_rota, perfil=perfil):
                    ativos = self._itens_ativos(usuarios[perfil], nome_rota)
                    self.assertEqual(
                        len(ativos), 1,
                        f"{nome_rota} acendeu {ativos or 'nenhum item'} para {perfil}",
                    )


class DeclaracaoDeVinculoTests(TestCase):
    """A declaracao de vinculo, do envio pela secretaria ate a tela do aluno.

    Tres coisas que so aparecem ponta a ponta:

    O arquivo so e alcancavel se algum modelo reivindicar o caminho em
    _regras_de_arquivo. Sem essa linha, o PDF entra no bucket e responde 404
    para todo mundo, inclusive para o dono -- e nada no envio acusa isso.

    A declaracao vale por um semestre. Mostrar a do semestre passado quando
    falta a atual e pior do que nao mostrar nada: o aluno a apresenta vencida
    sem nenhum aviso de que era a errada.

    O nome do arquivo carrega o CPF, e ele nao pode seguir para dentro do
    bucket -- a chave do objeto viaja na URL assinada e nos logs.
    """

    @classmethod
    def setUpTestData(cls):
        senha = "senha-segura-123"
        cls.aluno = Aluno.objects.create_user(
            email="aluno.vinculo@example.com", password=senha, nome="Aluno Vínculo",
            tipo_usuario=User.TipoUsuario.ALUNO, matricula="2026M0100", cpf="52998224725",
        )
        cls.outro_aluno = Aluno.objects.create_user(
            email="outro.vinculo@example.com", password=senha, nome="Outro Vínculo",
            tipo_usuario=User.TipoUsuario.ALUNO, matricula="2026M0101", cpf="16899535009",
        )
        cls.servidor = User.objects.create_user(
            email="servidor.vinculo@example.com", password=senha, nome="Servidor Vínculo",
            tipo_usuario=User.TipoUsuario.SERVIDOR,
        )
        hoje = timezone.localdate()
        cls.periodo = PeriodoLetivo.objects.create(
            criado_por=cls.servidor,
            nome="2026.2",
            data_inicio=hoje - timedelta(days=10),
            data_fim=hoje + timedelta(days=80),
            prazo_cadastro_disciplinas=hoje,
            matricula_inicio=hoje, matricula_fim=hoje + timedelta(days=5),
            modificacao_inicio=hoje + timedelta(days=6), modificacao_fim=hoje + timedelta(days=10),
        )
        cls.periodo_anterior = PeriodoLetivo.objects.create(
            criado_por=cls.servidor,
            nome="2026.1",
            data_inicio=hoje - timedelta(days=200),
            data_fim=hoje - timedelta(days=100),
            prazo_cadastro_disciplinas=hoje - timedelta(days=200),
            matricula_inicio=hoje - timedelta(days=200), matricula_fim=hoje - timedelta(days=195),
            modificacao_inicio=hoje - timedelta(days=194), modificacao_fim=hoje - timedelta(days=190),
        )

    def _pdf(self, nome):
        return SimpleUploadedFile(nome, b"%PDF-1.4 conteudo", content_type="application/pdf")

    def _importar(self, nome, *, periodo=None, substituir=False):
        return importar_declaracoes_de_vinculo(
            periodo=periodo or self.periodo,
            arquivos=[self._pdf(nome)],
            enviado_por=self.servidor,
            substituir=substituir,
        )

    def test_o_cpf_do_nome_encontra_o_aluno(self):
        resultado = self._importar("52998224725.pdf")[0]

        self.assertTrue(resultado["importado"], resultado.get("motivo"))
        declaracao = DeclaracaoDeVinculo.objects.get(aluno=self.aluno, periodo=self.periodo)
        self.assertEqual(declaracao.enviado_por, self.servidor)

    def test_o_cpf_aceita_a_formatacao_da_pasta(self):
        """A pasta vem de mais de uma maquina e a formatacao varia."""
        self.assertTrue(self._importar("529.982.247-25.pdf")[0]["importado"])

    def test_o_cpf_nao_vai_para_o_caminho_do_arquivo(self):
        self._importar("52998224725.pdf")

        caminho = DeclaracaoDeVinculo.objects.get().arquivo.name
        self.assertNotIn("52998224725", caminho)
        self.assertNotIn("529.982.247-25", caminho)
        self.assertTrue(caminho.startswith("documentos/vinculo/"), caminho)

    def test_o_que_nao_casa_vira_linha_do_relatorio(self):
        casos = {
            "11111111111.pdf": "O nome do arquivo não é um CPF válido.",
            "16899535009.txt": "A declaração precisa ser um PDF.",
            "11144477735.pdf": "Nenhum aluno cadastrado com este CPF.",
        }
        for nome, motivo in casos.items():
            with self.subTest(arquivo=nome):
                resultado = self._importar(nome)[0]
                self.assertFalse(resultado["importado"])
                self.assertEqual(resultado["motivo"], motivo)
        self.assertFalse(DeclaracaoDeVinculo.objects.exists())

    def test_um_arquivo_ruim_nao_derruba_os_demais(self):
        resultados = importar_declaracoes_de_vinculo(
            periodo=self.periodo,
            arquivos=[self._pdf("11111111111.pdf"), self._pdf("52998224725.pdf")],
            enviado_por=self.servidor,
        )

        self.assertEqual([r["importado"] for r in resultados], [False, True])
        self.assertEqual(DeclaracaoDeVinculo.objects.count(), 1)

    def test_reenviar_sem_pedir_nao_troca_o_que_existe(self):
        """Reenviar a pasta inteira por engano nao pode substituir em silencio."""
        self._importar("52998224725.pdf")
        original = DeclaracaoDeVinculo.objects.get().arquivo.name

        resultado = self._importar("52998224725.pdf")[0]

        self.assertFalse(resultado["importado"])
        self.assertIn("Já existe declaração", resultado["motivo"])
        self.assertEqual(DeclaracaoDeVinculo.objects.get().arquivo.name, original)

    def test_substituir_troca_o_arquivo_e_nao_duplica(self):
        self._importar("52998224725.pdf")
        original = DeclaracaoDeVinculo.objects.get().arquivo.name

        resultado = self._importar("52998224725.pdf", substituir=True)[0]

        self.assertTrue(resultado["importado"])
        self.assertTrue(resultado["substituiu"])
        self.assertEqual(DeclaracaoDeVinculo.objects.count(), 1)
        self.assertNotEqual(DeclaracaoDeVinculo.objects.get().arquivo.name, original)

    def test_o_mesmo_aluno_tem_uma_por_semestre(self):
        self._importar("52998224725.pdf", periodo=self.periodo_anterior)
        self._importar("52998224725.pdf", periodo=self.periodo)

        self.assertEqual(DeclaracaoDeVinculo.objects.filter(aluno=self.aluno).count(), 2)

    def test_o_banco_recusa_duas_declaracoes_do_mesmo_semestre(self):
        """A restricao e o que da sentido a 'a declaracao vigente'."""
        self._importar("52998224725.pdf")

        with self.assertRaises(IntegrityError):
            DeclaracaoDeVinculo.objects.create(
                aluno=self.aluno, periodo=self.periodo, arquivo=self._pdf("outro.pdf"),
            )

    def test_a_tela_do_aluno_mostra_a_do_semestre_em_curso(self):
        self._importar("52998224725.pdf")
        self._matricular(self.aluno, self.periodo, SolicitacaoMatricula.Status.RASCUNHO)
        self.client.force_login(self.aluno)

        resposta = self.client.get(reverse("aluno_documento_vinculo"))

        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta.context["periodo"], self.periodo)
        self.assertIsNotNone(resposta.context["vigente"])
        self.assertContains(resposta, "Abrir declaração")

    def test_sem_a_do_semestre_a_anterior_nao_ocupa_o_lugar(self):
        """Declaracao vencida e pior do que declaracao ausente."""
        self._importar("52998224725.pdf", periodo=self.periodo_anterior)
        self._matricular(self.aluno, self.periodo_anterior, SolicitacaoMatricula.Status.RASCUNHO)
        self._matricular(self.aluno, self.periodo, SolicitacaoMatricula.Status.RASCUNHO)
        self.client.force_login(self.aluno)

        resposta = self.client.get(reverse("aluno_documento_vinculo"))

        self.assertIsNone(resposta.context["vigente"])
        self.assertContains(resposta, "Ainda não há declaração para 2026.2")
        # a anterior continua alcancavel, mas como anterior
        self.assertEqual(len(resposta.context["anteriores"]), 1)

    def test_o_arquivo_so_abre_para_quem_pode(self):
        self._importar("52998224725.pdf")
        self._matricular(self.aluno, self.periodo, SolicitacaoMatricula.Status.RASCUNHO)
        caminho = DeclaracaoDeVinculo.objects.get().arquivo.name
        url = reverse("media_file", kwargs={"path": caminho})

        with self.subTest(quem="o proprio aluno"):
            self.client.force_login(self.aluno)
            self.assertEqual(self.client.get(url).status_code, 200)

        with self.subTest(quem="a secretaria"):
            self.client.force_login(self.servidor)
            self.assertEqual(self.client.get(url).status_code, 200)

        with self.subTest(quem="outro aluno"):
            self.client.force_login(self.outro_aluno)
            self.assertEqual(self.client.get(url).status_code, 404)

    def test_a_tela_de_envio_e_restrita_a_gestao(self):
        self.client.force_login(self.aluno)
        self.assertEqual(self.client.get(reverse("declaracoes_vinculo")).status_code, 403)

        self.client.force_login(self.servidor)
        self.assertEqual(self.client.get(reverse("declaracoes_vinculo")).status_code, 200)

    def test_o_envio_pela_tela_grava_e_relata(self):
        self.client.force_login(self.servidor)

        resposta = self.client.post(
            reverse("declaracoes_vinculo"),
            {
                "periodo": self.periodo.pk,
                "arquivos": [self._pdf("52998224725.pdf"), self._pdf("11111111111.pdf")],
            },
        )

        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(len(resposta.context["importados"]), 1)
        self.assertEqual(len(resposta.context["recusados"]), 1)
        self.assertEqual(DeclaracaoDeVinculo.objects.count(), 1)

    def test_o_periodo_em_curso_e_o_que_contem_hoje(self):
        self.assertEqual(periodo_em_curso(), self.periodo)

    # --- a declaracao acompanha a matricula efetivada ---

    def _matricular(self, aluno, periodo, status):
        return SolicitacaoMatricula.objects.create(aluno=aluno, periodo=periodo, status=status)

    def test_qualquer_estado_da_matricula_abre_a_declaracao(self):
        """O status nao entra na conta, de proposito.

        Os estados sao legado: nao foram mantidos de forma confiavel ao longo do
        tempo, e filtrar por eles negaria a declaracao a alunos que cursaram o
        semestre -- justamente quem precisa comprovar o vinculo. O que se afirma
        e mais modesto: houve pedido de matricula naquele periodo.
        """
        self._importar("52998224725.pdf")
        declaracao = DeclaracaoDeVinculo.objects.get()

        for status in SolicitacaoMatricula.Status:
            with self.subTest(status=status):
                SolicitacaoMatricula.objects.all().delete()
                self._matricular(self.aluno, self.periodo, status)
                self.assertTrue(declaracao.pode_visualizar(self.aluno))

    def test_sem_matricula_nenhuma_o_aluno_nao_alcanca(self):
        self._importar("52998224725.pdf")

        self.assertFalse(DeclaracaoDeVinculo.objects.get().pode_visualizar(self.aluno))

    def test_a_condicao_e_do_semestre_da_declaracao(self):
        """A de 2026.1 exige matricula em 2026.1, e nao no semestre em curso."""
        self._importar("52998224725.pdf", periodo=self.periodo_anterior)
        self._importar("52998224725.pdf", periodo=self.periodo)
        antiga = DeclaracaoDeVinculo.objects.get(periodo=self.periodo_anterior)
        atual = DeclaracaoDeVinculo.objects.get(periodo=self.periodo)

        self._matricular(self.aluno, self.periodo_anterior, SolicitacaoMatricula.Status.RASCUNHO)

        self.assertTrue(antiga.pode_visualizar(self.aluno))
        self.assertFalse(atual.pode_visualizar(self.aluno))

    def test_a_gestao_ve_antes_de_o_aluno_alcancar(self):
        """E ela que emite, e precisa conferir o que enviou."""
        self._importar("52998224725.pdf")

        self.assertTrue(DeclaracaoDeVinculo.objects.get().pode_visualizar(self.servidor))

    def test_o_arquivo_segue_a_mesma_regra_da_tela(self):
        self._importar("52998224725.pdf")
        url = reverse("media_file", kwargs={"path": DeclaracaoDeVinculo.objects.get().arquivo.name})
        self.client.force_login(self.aluno)

        self.assertEqual(self.client.get(url).status_code, 404)

        self._matricular(self.aluno, self.periodo, SolicitacaoMatricula.Status.RASCUNHO)
        self.assertEqual(self.client.get(url).status_code, 200)

    def test_a_tela_nao_lista_o_que_a_permissao_recusa(self):
        """Listar e depois recusar no clique e pior do que nao listar."""
        self._importar("52998224725.pdf", periodo=self.periodo_anterior)
        self._importar("52998224725.pdf", periodo=self.periodo)
        self._matricular(self.aluno, self.periodo, SolicitacaoMatricula.Status.RASCUNHO)
        self.client.force_login(self.aluno)

        resposta = self.client.get(reverse("aluno_documento_vinculo"))

        self.assertIsNotNone(resposta.context["vigente"])
        self.assertEqual(resposta.context["anteriores"], [])

    def test_sem_matricula_a_tela_diz_que_falta_matricula(self):
        """Mandar esperar a secretaria emitir seria mandar esperar em vao."""
        self._importar("52998224725.pdf")
        self.client.force_login(self.aluno)

        resposta = self.client.get(reverse("aluno_documento_vinculo"))

        self.assertFalse(resposta.context["tem_vinculo"])
        self.assertContains(resposta, "Sem matrícula em 2026.2")
        self.assertNotContains(resposta, "Ainda não há declaração para")

    def test_o_relatorio_avisa_quando_o_aluno_nao_vai_ver(self):
        """O envio nao e bloqueado, mas nao pode ficar invisivel em silencio."""
        sem_matricula = self._importar("52998224725.pdf")[0]
        self.assertTrue(sem_matricula["importado"])
        self.assertTrue(sem_matricula["invisivel_ao_aluno"])

        DeclaracaoDeVinculo.objects.all().delete()
        self._matricular(self.aluno, self.periodo, SolicitacaoMatricula.Status.RASCUNHO)
        com_matricula = self._importar("52998224725.pdf")[0]
        self.assertFalse(com_matricula["invisivel_ao_aluno"])
