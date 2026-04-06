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
        ABERTO = "ABERTO", "Aberto"
        EM_ANALISE = "EM_ANALISE", "Em analise"
        AGUARDANDO_DOCUMENTO = "AGUARDANDO_DOCUMENTO", "Aguardando documento"
        ENCAMINHADO = "ENCAMINHADO", "Encaminhado"
        DEFERIDO = "DEFERIDO", "Deferido"
        INDEFERIDO = "INDEFERIDO", "Indeferido"
        FINALIZADO = "FINALIZADO", "Finalizado"
        ARQUIVADO = "ARQUIVADO", "Arquivado"

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
        default=StatusProcesso.ABERTO,
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
    observacoes_internas = models.TextField(blank=True)

    class Meta:
        ordering = ["-data_criacao"]

    def __str__(self) -> str:
        return f"{self.numero} - {self.assunto}"

    @property
    def esta_finalizado(self) -> bool:
        return self.finalizado_em is not None or self.status in {
            self.StatusProcesso.DEFERIDO,
            self.StatusProcesso.INDEFERIDO,
            self.StatusProcesso.FINALIZADO,
            self.StatusProcesso.ARQUIVADO,
        }

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
            self.StatusProcesso.ABERTO,
            self.StatusProcesso.EM_ANALISE,
            self.StatusProcesso.AGUARDANDO_DOCUMENTO,
            self.StatusProcesso.ENCAMINHADO,
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
        tipo_documento: str | None = None,
    ):
        return Documento.objects.create(
            processo=self,
            titulo=titulo,
            texto=texto,
            arquivo=arquivo,
            enviado_por=enviado_por,
            tipo_documento=tipo_documento or "",
        )

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

        status_novo = status_resultante or self.StatusProcesso.ENCAMINHADO
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

    def finalizar(self, *, status_final: str | None = None):
        if self.esta_finalizado:
            raise ValidationError("Processo ja finalizado.")

        self.status = status_final or self.StatusProcesso.FINALIZADO
        self.finalizado_em = timezone.now()
        self.save(update_fields=["status", "finalizado_em", "atualizado_em"])

    def deferir(self):
        self.finalizar(status_final=self.StatusProcesso.DEFERIDO)

    def indeferir(self):
        self.finalizar(status_final=self.StatusProcesso.INDEFERIDO)


class Documento(models.Model):
    class TipoDocumento(models.TextChoices):
        REQUERIMENTO = "REQUERIMENTO", "Requerimento"
        PARECER = "PARECER", "Parecer"
        ATA = "ATA", "Ata"
        COMPROVANTE = "COMPROVANTE", "Comprovante"
        OUTRO = "OUTRO", "Outro"

    processo = models.ForeignKey(
        Processo,
        on_delete=models.CASCADE,
        related_name="documentos",
    )
    titulo = models.CharField(max_length=255)
    texto = models.TextField(blank=True)
    arquivo = models.FileField(upload_to="documentos/processos/", blank=True, null=True)
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
        if not (self.texto or "").strip() and not self.arquivo:
            raise ValidationError("Documento deve possuir texto ou arquivo.")

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
