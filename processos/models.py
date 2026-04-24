from django.conf import settings
from django.contrib.auth.base_user import BaseUserManager
from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.db import models
from django.db import transaction
from django.utils import timezone


class UserManager(BaseUserManager):
    use_in_migrations = True

    def _create_user(self, email, password, **extra_fields):
        if not email:
            raise ValueError("Email must be set")

        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)
        return self._create_user(email, password, **extra_fields)

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)

        if extra_fields.get("is_staff") is not True:
            raise ValueError("Superuser must have is_staff=True.")
        if extra_fields.get("is_superuser") is not True:
            raise ValueError("Superuser must have is_superuser=True.")

        return self._create_user(email, password, **extra_fields)


class User(AbstractUser):
    class TipoUsuario(models.TextChoices):
        ALUNO = "ALUNO", "Aluno"
        DOCENTE = "DOCENTE", "Docente"
        SERVIDOR = "SERVIDOR", "Servidor"

    username = None
    first_name = None
    last_name = None

    nome = models.CharField(max_length=255)
    email = models.EmailField(unique=True)
    tipo_usuario = models.CharField(
        max_length=20,
        choices=TipoUsuario.choices,
        default=TipoUsuario.SERVIDOR,
    )

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["nome"]

    objects = UserManager()

    class Meta:
        ordering = ["nome", "email"]

    def __str__(self) -> str:
        return self.nome or self.email


class Aluno(User):
    # DateField allows precise temporal queries and avoids year-only ambiguity.
    ingresso = models.DateField()
    orientador = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="orientandos",
        limit_choices_to={"tipo_usuario": User.TipoUsuario.DOCENTE},
    )
    matricula = models.CharField(max_length=50, blank=True)

    class Meta:
        verbose_name = "Aluno"
        verbose_name_plural = "Alunos"

    def clean(self):
        errors = {}

        if self.tipo_usuario and self.tipo_usuario != User.TipoUsuario.ALUNO:
            errors["tipo_usuario"] = "Aluno deve ter tipo_usuario ALUNO."

        if self.orientador and self.orientador.tipo_usuario != User.TipoUsuario.DOCENTE:
            errors["orientador"] = "Orientador deve ser um usuario do tipo DOCENTE."

        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.tipo_usuario = User.TipoUsuario.ALUNO
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self) -> str:
        return self.nome or self.email


class Docente(User):
    externo = models.BooleanField(default=False)
    permanente = models.BooleanField(default=False)
    coordenador = models.BooleanField(default=False)

    class Meta:
        verbose_name = "Docente"
        verbose_name_plural = "Docentes"

    def clean(self):
        if self.tipo_usuario and self.tipo_usuario != User.TipoUsuario.DOCENTE:
            raise ValidationError({"tipo_usuario": "Docente deve ter tipo_usuario DOCENTE."})

    def save(self, *args, **kwargs):
        self.tipo_usuario = User.TipoUsuario.DOCENTE
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self) -> str:
        return self.nome or self.email


class Setor(models.Model):
    nome = models.CharField(max_length=120, unique=True)
    descricao = models.CharField(max_length=255, blank=True)
    ativo = models.BooleanField(default=True)

    class Meta:
        ordering = ["nome"]

    def __str__(self) -> str:
        return self.nome


class Processo(models.Model):
    class TipoProcesso(models.TextChoices):
        APROVEITAMENTO_CREDITOS = "APROVEITAMENTO_CREDITOS", "Aproveitamento de Creditos"
        DISPENSA_DISCIPLINA = "DISPENSA_DISCIPLINA", "Dispensa de Disciplina"
        TRANCAMENTO_MATRICULA = "TRANCAMENTO_MATRICULA", "Trancamento de Matricula"
        PRORROGACAO_PRAZO = "PRORROGACAO_PRAZO", "Prorrogacao de Prazo"
        REINGRESSO = "REINGRESSO", "Reingresso"
        MUDANCA_ORIENTADOR = "MUDANCA_ORIENTADOR", "Mudanca de Orientador"
        QUALIFICACAO = "QUALIFICACAO", "Qualificacao"
        DEFESA = "DEFESA", "Defesa"
        RECURSO = "RECURSO", "Recurso"
        OUTRO = "OUTRO", "Outro"

    class StatusProcesso(models.TextChoices):
        EM_ANALISE = "EM_ANALISE", "Em analise"
        AGUARDANDO_DOCUMENTO = "AGUARDANDO_DOCUMENTO", "Aguardando documento"
        AGUARDANDO_CIENCIA = "AGUARDANDO_CIENCIA", "Aguardando ciencia"
        FINALIZADO = "FINALIZADO", "Finalizado"

    class Prioridade(models.TextChoices):
        BAIXA = "BAIXA", "Baixa"
        MEDIA = "MEDIA", "Media"
        ALTA = "ALTA", "Alta"

    usuario_criado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="processos_criados",
    )
    tipo = models.CharField(max_length=40, choices=TipoProcesso.choices)
    assunto = models.CharField(max_length=255)
    descricao = models.TextField()
    data_criacao = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)
    status_inicial = models.CharField(
        max_length=25,
        choices=StatusProcesso.choices,
        null=True,
        blank=True,
    )
    status = models.CharField(
        max_length=25,
        choices=StatusProcesso.choices,
        default=StatusProcesso.EM_ANALISE,
    )
    prioridade = models.CharField(
        max_length=10,
        choices=Prioridade.choices,
        null=True,
        blank=True,
    )
    setor_atual = models.ForeignKey(
        Setor,
        on_delete=models.PROTECT,
        related_name="processos_atuais",
    )
    numero = models.CharField(max_length=20, unique=True, editable=False, blank=True)
    finalizado_em = models.DateTimeField(null=True, blank=True)
    termo_finalizacao = models.TextField(blank=True)
    observacoes_internas = models.TextField(blank=True)

    class Meta:
        ordering = ["-data_criacao"]

    def __str__(self) -> str:
        return f"{self.numero} - {self.assunto}"

    @property
    def esta_finalizado(self) -> bool:
        return self.finalizado_em is not None or self.status == self.StatusProcesso.FINALIZADO

    @classmethod
    def gerar_numero(cls) -> str:
        prefixo = timezone.now().strftime("%Y%m")
        ultimo = (
            cls.objects.select_for_update()
            .filter(numero__startswith=f"{prefixo}-")
            .order_by("-numero")
            .first()
        )
        if ultimo and ultimo.numero:
            sequencia = int(ultimo.numero.split("-")[1]) + 1
        else:
            sequencia = 1
        return f"{prefixo}-{sequencia:06d}"

    def clean(self):
        if self.finalizado_em and self.status in {
            self.StatusProcesso.EM_ANALISE,
            self.StatusProcesso.AGUARDANDO_DOCUMENTO,
            self.StatusProcesso.AGUARDANDO_CIENCIA,
        }:
            raise ValidationError(
                {"status": "Status em andamento nao pode ter data de finalizacao."}
            )

    def save(self, *args, **kwargs):
        if self._state.adding and not self.status_inicial:
            self.status_inicial = self.status

        if not self.numero:
            for _ in range(5):
                try:
                    with transaction.atomic():
                        self.numero = self.gerar_numero()
                        return super().save(*args, **kwargs)
                except IntegrityError:
                    self.numero = ""
            raise ValidationError("Nao foi possivel gerar numero unico para o processo.")

        return super().save(*args, **kwargs)

    def adicionar_documento(
        self,
        *,
        titulo: str,
        enviado_por: User,
        texto: str = "",
        arquivo=None,
        restricao_tipo: str = "NAO",
        tipo_documento: str | None = None,
    ):
        return Documento.objects.create(
            processo=self,
            titulo=titulo,
            texto=texto,
            arquivo=arquivo,
            restricao_tipo=restricao_tipo,
            enviado_por=enviado_por,
            tipo_documento=tipo_documento or "",
        )

    def obter_orientador_responsavel(self):
        aluno = Aluno.objects.filter(pk=self.usuario_criado_por_id).select_related("orientador").first()
        if not aluno:
            return None
        return aluno.orientador

    def solicitar_ciente_orientador(self, *, solicitado_por, mensagem_solicitacao: str = ""):
        orientador = self.obter_orientador_responsavel()
        if not orientador:
            raise ValidationError("Processo sem orientador definido para solicitar ciente.")

        pendente = self.manifestacoes.filter(
            tipo=ManifestacaoProcesso.TipoManifestacao.CIENTE_ORIENTADOR,
            status=ManifestacaoProcesso.StatusManifestacao.PENDENTE,
        ).exists()
        if pendente:
            raise ValidationError("Ja existe solicitacao de ciente do orientador pendente.")

        manifestacao = ManifestacaoProcesso.objects.create(
            processo=self,
            tipo=ManifestacaoProcesso.TipoManifestacao.CIENTE_ORIENTADOR,
            status=ManifestacaoProcesso.StatusManifestacao.PENDENTE,
            responsavel=orientador,
            solicitado_por=solicitado_por,
            mensagem_solicitacao=mensagem_solicitacao,
        )
        if self.status != self.StatusProcesso.AGUARDANDO_CIENCIA:
            self.status = self.StatusProcesso.AGUARDANDO_CIENCIA
            self.save(update_fields=["status", "atualizado_em"])
        return manifestacao

    def encaminhar(
        self,
        *,
        setor_destino: Setor,
        encaminhado_por: User,
        observacao: str = "",
        status_resultante: str | None = None,
    ):
        if self.esta_finalizado:
            raise ValidationError("Nao e permitido encaminhar processo finalizado.")
        if self.manifestacoes.filter(
            tipo=ManifestacaoProcesso.TipoManifestacao.CIENTE_ORIENTADOR,
            status=ManifestacaoProcesso.StatusManifestacao.PENDENTE,
        ).exists():
            raise ValidationError("Nao e permitido encaminhar com ciente do orientador pendente.")

        status_novo = status_resultante or self.StatusProcesso.EM_ANALISE
        setor_origem = self.setor_atual

        with transaction.atomic():
            self.setor_atual = setor_destino
            self.status = status_novo
            self.save(update_fields=["setor_atual", "status", "atualizado_em"])

            return TramitacaoProcesso.objects.create(
                processo=self,
                setor_origem=setor_origem,
                setor_destino=setor_destino,
                encaminhado_por=encaminhado_por,
                observacao=observacao,
                status_resultante=status_novo,
            )

    def finalizar(self, *, termo_finalizacao: str, status_final: str | None = None):
        if self.esta_finalizado:
            raise ValidationError("Processo ja finalizado.")
        termo_finalizacao = (termo_finalizacao or "").strip()
        if not termo_finalizacao:
            raise ValidationError("Informe o termo de finalizacao do processo.")

        self.status = status_final or self.StatusProcesso.FINALIZADO
        self.finalizado_em = timezone.now()
        self.termo_finalizacao = termo_finalizacao
        self.save(update_fields=["status", "finalizado_em", "termo_finalizacao", "atualizado_em"])

    def deferir(self):
        self.finalizar(
            termo_finalizacao="Processo deferido.",
            status_final=self.StatusProcesso.FINALIZADO,
        )

    def indeferir(self):
        self.finalizar(
            termo_finalizacao="Processo indeferido.",
            status_final=self.StatusProcesso.FINALIZADO,
        )


class Documento(models.Model):
    class TipoDocumento(models.TextChoices):
        REQUERIMENTO = "REQUERIMENTO", "Requerimento"
        PARECER = "PARECER", "Parecer"
        ATA = "ATA", "Ata"
        COMPROVANTE = "COMPROVANTE", "Comprovante"
        OUTRO = "OUTRO", "Outro"

    class RestricaoAcesso(models.TextChoices):
        NAO = "NAO", "Não"
        INFORMACAO_PESSOAL = (
            "INFORMACAO_PESSOAL",
            "Informação pessoal (Art. 31 da Lei de Acesso à Informação (Lei nº 12.527/2011))",
        )
        DOCUMENTO_PREPARATORIO = (
            "DOCUMENTO_PREPARATORIO",
            "Documento preparatório / processo decisório (Art. 7º, §3º da Lei de Acesso à Informação (Lei nº 12.527/2011))",
        )
        INVESTIGACAO_ADMINISTRATIVA = (
            "INVESTIGACAO_ADMINISTRATIVA",
            "Investigação ou apuração administrativa (Art. 150 da Lei nº 8.112/1990)",
        )
        SIGILO_ACADEMICO = (
            "SIGILO_ACADEMICO",
            "Sigilo acadêmico (avaliações, pareceres, bancas) (Art. 31 da Lei de Acesso à Informação (Lei nº 12.527/2011))",
        )
        PROPRIEDADE_INTELECTUAL = (
            "PROPRIEDADE_INTELECTUAL",
            "Propriedade intelectual / direito autoral (Art. 24, III da Lei nº 9.610/1998; Art. 2º da Lei nº 9.609/1998)",
        )
        SEGREDO_INDUSTRIAL = (
            "SEGREDO_INDUSTRIAL",
            "Segredo industrial ou informação estratégica (Art. 195, XIV da Lei nº 9.279/1996)",
        )
        SIGILO_LEGAL_ESPECIFICO = (
            "SIGILO_LEGAL_ESPECIFICO",
            "Sigilo legal específico (fiscal, bancário, etc.) (Art. 198 do CTN; LC nº 105/2001)",
        )

    processo = models.ForeignKey(
        Processo,
        on_delete=models.CASCADE,
        related_name="documentos",
    )
    titulo = models.CharField(max_length=255)
    texto = models.TextField(blank=True)
    arquivo = models.FileField(upload_to="documentos/processos/", blank=True, null=True)
    restrito = models.BooleanField(default=False)
    restricao_tipo = models.CharField(
        max_length=40,
        choices=RestricaoAcesso.choices,
        default=RestricaoAcesso.NAO,
    )
    restricao_outro = models.CharField(max_length=255, blank=True)
    arquivo_removido = models.BooleanField(default=False)
    arquivo_removido_em = models.DateTimeField(blank=True, null=True)
    arquivo_removido_motivo = models.TextField(blank=True)
    arquivo_removido_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="documentos_com_arquivo_removido",
    )
    enviado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="documentos_enviados",
    )
    data_envio = models.DateTimeField(auto_now_add=True)
    tipo_documento = models.CharField(
        max_length=20,
        choices=TipoDocumento.choices,
        blank=True,
    )

    class Meta:
        ordering = ["-data_envio"]

    def __str__(self) -> str:
        return self.titulo

    def clean(self):
        self.restricao_outro = ""

        self.restrito = self.restricao_tipo != self.RestricaoAcesso.NAO

        if self.arquivo_removido:
            return

        if not (self.texto or "").strip() and not self.arquivo:
            raise ValidationError("Documento deve possuir texto ou arquivo.")

    def pode_visualizar_arquivo(self, user) -> bool:
        if self.arquivo_removido or not self.arquivo:
            return False

        if not self.restrito:
            return True

        if not user or not user.is_authenticated:
            return False

        if user.id == self.enviado_por_id:
            return True

        if getattr(user, "tipo_usuario", None) == User.TipoUsuario.SERVIDOR:
            return True

        if getattr(user, "tipo_usuario", None) == User.TipoUsuario.DOCENTE:
            try:
                return bool(user.docente.coordenador)
            except Docente.DoesNotExist:
                return False

        return False

    def remover_arquivo(self, *, removido_por, motivo: str):
        if self.arquivo_removido:
            return

        motivo = (motivo or "").strip()
        if not motivo:
            raise ValidationError("Informe o motivo da remocao do arquivo.")

        self.arquivo_removido = True
        self.arquivo_removido_em = timezone.now()
        self.arquivo_removido_motivo = motivo
        self.arquivo_removido_por = removido_por
        self.save(
            update_fields=[
                "arquivo_removido",
                "arquivo_removido_em",
                "arquivo_removido_motivo",
                "arquivo_removido_por",
            ]
        )

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)


class TramitacaoProcesso(models.Model):
    processo = models.ForeignKey(
        Processo,
        on_delete=models.CASCADE,
        related_name="tramitacoes",
    )
    setor_origem = models.ForeignKey(
        Setor,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="tramitacoes_origem",
    )
    setor_destino = models.ForeignKey(
        Setor,
        on_delete=models.PROTECT,
        related_name="tramitacoes_destino",
    )
    encaminhado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="tramitacoes_realizadas",
    )
    observacao = models.TextField(blank=True)
    status_resultante = models.CharField(
        max_length=25,
        choices=Processo.StatusProcesso.choices,
        null=True,
        blank=True,
    )
    data_encaminhamento = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-data_encaminhamento"]

    def __str__(self) -> str:
        return f"Tramitacao {self.processo.numero} -> {self.setor_destino.nome}"


class ManifestacaoProcesso(models.Model):
    class TipoManifestacao(models.TextChoices):
        CIENTE_ORIENTADOR = "CIENTE_ORIENTADOR", "Ciente do orientador"

    class StatusManifestacao(models.TextChoices):
        PENDENTE = "PENDENTE", "Pendente"
        CIENTE = "CIENTE", "Ciente"
        RECUSADO = "RECUSADO", "Recusado"

    processo = models.ForeignKey(
        Processo,
        on_delete=models.CASCADE,
        related_name="manifestacoes",
    )
    tipo = models.CharField(max_length=40, choices=TipoManifestacao.choices)
    status = models.CharField(
        max_length=20,
        choices=StatusManifestacao.choices,
        default=StatusManifestacao.PENDENTE,
    )
    responsavel = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="manifestacoes_pendentes",
    )
    solicitado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="manifestacoes_solicitadas",
    )
    mensagem_solicitacao = models.TextField(blank=True)
    mensagem_manifestacao = models.TextField(blank=True)
    data_solicitacao = models.DateTimeField(auto_now_add=True)
    data_manifestacao = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-data_solicitacao"]

    def __str__(self) -> str:
        return f"{self.get_tipo_display()} - {self.processo.numero}"

    def registrar_manifestacao(self, *, autor, aceito: bool, mensagem: str = ""):
        if self.status != self.StatusManifestacao.PENDENTE:
            raise ValidationError("Manifestacao ja concluida.")
        if autor.id != self.responsavel_id:
            raise ValidationError("Apenas o responsavel pode se manifestar.")

        self.status = self.StatusManifestacao.CIENTE if aceito else self.StatusManifestacao.RECUSADO
        self.mensagem_manifestacao = (mensagem or "").strip()
        self.data_manifestacao = timezone.now()
        self.save(update_fields=["status", "mensagem_manifestacao", "data_manifestacao"])
        if not self.processo.manifestacoes.filter(
            tipo=self.TipoManifestacao.CIENTE_ORIENTADOR,
            status=self.StatusManifestacao.PENDENTE,
        ).exists() and self.processo.status == Processo.StatusProcesso.AGUARDANDO_CIENCIA:
            self.processo.status = Processo.StatusProcesso.EM_ANALISE
            self.processo.save(update_fields=["status", "atualizado_em"])


class ComentarioProcesso(models.Model):
    processo = models.ForeignKey(
        Processo,
        on_delete=models.CASCADE,
        related_name="comentarios",
    )
    autor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="comentarios_processo",
    )
    anonimo = models.BooleanField(default=False)
    texto = models.TextField()
    data_criacao = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-data_criacao"]

    def __str__(self) -> str:
        return f"Comentario em {self.processo.numero}"
