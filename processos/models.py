import calendar
from datetime import timedelta
import uuid

from django.conf import settings
from django.contrib.auth.base_user import BaseUserManager
from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
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
    polo_atuacao = models.ForeignKey(
        "Polo",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="servidores",
    )
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
    class NivelCurso(models.TextChoices):
        MESTRADO = "MESTRADO", "Mestrado"
        DOUTORADO = "DOUTORADO", "Doutorado"

    class StatusAluno(models.TextChoices):
        ATIVO = "ATIVO", "Ativo"
        DESLIGADO = "DESLIGADO", "Desligado"
        DEFENDEU = "DEFENDEU", "Defendeu"

    semestre_validator = RegexValidator(
        regex=r"^\d{4}\.[12]$",
        message="Informe no formato YYYY.1 ou YYYY.2.",
    )
    status_aluno = models.CharField(
        max_length=12,
        choices=StatusAluno.choices,
        default=StatusAluno.ATIVO,
    )
    matricula = models.CharField(max_length=50, blank=True)

    class Meta:
        verbose_name = "Aluno"
        verbose_name_plural = "Alunos"

    def clean(self):
        errors = {}

        if self.tipo_usuario and self.tipo_usuario != User.TipoUsuario.ALUNO:
            errors["tipo_usuario"] = "Aluno deve ter tipo_usuario ALUNO."

        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.tipo_usuario = User.TipoUsuario.ALUNO
        self.is_active = self.status_aluno == self.StatusAluno.ATIVO
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self) -> str:
        return self.nome or self.email

    def trajetoria_ativa(self):
        return self.trajetorias.filter(status=TrajetoriaAcademica.Status.ATIVA).order_by("-criado_em").first()

    @property
    def coorientador_display(self) -> str:
        trajetoria = self.trajetoria_ativa()
        return trajetoria.coorientador_display if trajetoria else ""

    @property
    def qualificacao_label(self) -> str:
        if self.nivel_curso == self.NivelCurso.MESTRADO:
            return "Projeto de Dissertação"
        return "Qualificação"

    @property
    def qualificacao_label_lower(self) -> str:
        return self.qualificacao_label.lower()

    @property
    def qualificacao_label(self) -> str:
        trajetoria = self.trajetoria_ativa()
        return trajetoria.qualificacao_label if trajetoria else "QualificaÃ§Ã£o"


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


class TrajetoriaAcademica(models.Model):
    class Status(models.TextChoices):
        ATIVA = "ATIVA", "Ativa"
        CONCLUIDA = "CONCLUIDA", "Concluida"
        DESLIGADA = "DESLIGADA", "Desligada"
        TRANCADA = "TRANCADA", "Trancado"

    aluno = models.ForeignKey(Aluno, on_delete=models.CASCADE, related_name="trajetorias")
    nivel_curso = models.CharField(max_length=10, choices=Aluno.NivelCurso.choices)
    status = models.CharField(max_length=15, choices=Status.choices, default=Status.ATIVA)
    ingresso = models.CharField(max_length=6, validators=[Aluno.semestre_validator])
    prazo_qualificacao = models.CharField(max_length=6, blank=True, validators=[Aluno.semestre_validator])
    prazo_defesa = models.CharField(max_length=6, blank=True, validators=[Aluno.semestre_validator])
    orientador = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="trajetorias_orientadas",
        limit_choices_to={"tipo_usuario": User.TipoUsuario.DOCENTE},
    )
    coorientador = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="trajetorias_coorientadas",
        limit_choices_to={"tipo_usuario": User.TipoUsuario.DOCENTE},
    )
    coorientador_externo_nome = models.CharField(max_length=255, blank=True)
    coorientador_externo_email = models.EmailField(blank=True)
    coorientador_externo_instituicao = models.CharField(max_length=255, blank=True)
    isQualificado = models.BooleanField(default=False)
    numero_defesa = models.CharField(max_length=80, blank=True)
    data_defesa = models.DateField(null=True, blank=True)
    deposito_versao_final = models.BooleanField(default=False)
    reingressante = models.BooleanField(default=False)
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-criado_em"]

    def __str__(self) -> str:
        return f"{self.aluno.nome} - {self.get_nivel_curso_display()} - {self.get_status_display()}"

    def clean(self):
        errors = {}

        if self.orientador and self.orientador.tipo_usuario != User.TipoUsuario.DOCENTE:
            errors["orientador"] = "Orientador deve ser um usuario do tipo DOCENTE."
        if self.coorientador and self.coorientador.tipo_usuario != User.TipoUsuario.DOCENTE:
            errors["coorientador"] = "Coorientador deve ser um usuario do tipo DOCENTE."
        if self.coorientador and self.coorientador_externo_nome.strip():
            errors["coorientador"] = "Informe coorientador cadastrado ou coorientador externo, nao ambos."
            errors["coorientador_externo_nome"] = "Informe coorientador cadastrado ou coorientador externo, nao ambos."
        if self.coorientador and self.orientador_id == self.coorientador_id:
            errors["coorientador"] = "Coorientador deve ser diferente do orientador."

        if not self.coorientador_externo_nome.strip():
            self.coorientador_externo_email = ""
            self.coorientador_externo_instituicao = ""

        if self.status == self.Status.CONCLUIDA:
            if not (self.numero_defesa or "").strip():
                errors["numero_defesa"] = "Informe o numero da defesa para trajetoria concluida."
            if not self.data_defesa:
                errors["data_defesa"] = "Informe a data da defesa para trajetoria concluida."
        elif self.deposito_versao_final:
            errors["deposito_versao_final"] = "Deposito da versao final so pode ser marcado apos conclusao."

        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.numero_defesa = (self.numero_defesa or "").strip()
        if self.status != self.Status.CONCLUIDA:
            self.numero_defesa = ""
            self.data_defesa = None
            self.deposito_versao_final = False
        self.full_clean()
        return super().save(*args, **kwargs)

    @property
    def qualificacao_label(self) -> str:
        if self.nivel_curso == Aluno.NivelCurso.MESTRADO:
            return "Projeto de Dissertação"
        return "Qualificação"

    @property
    def qualificacao_label_lower(self) -> str:
        return self.qualificacao_label.lower()

    @property
    def coorientador_display(self) -> str:
        if self.coorientador:
            return self.coorientador.nome
        return self.coorientador_externo_nome.strip()


class PublicacaoTrajetoria(models.Model):
    class TipoPublicacao(models.TextChoices):
        ARTIGO_PERIODICO = "ARTIGO_PERIODICO", "Artigo em periodico"
        ARTIGO_EVENTO = "ARTIGO_EVENTO", "Artigo em evento"
        LIVRO_CAPITULO = "LIVRO_CAPITULO", "Livro/capitulo"
        OUTRO = "OUTRO", "Outro"

    trajetoria = models.ForeignKey(TrajetoriaAcademica, on_delete=models.CASCADE, related_name="publicacoes")
    titulo = models.CharField(max_length=255)
    tipo = models.CharField(max_length=25, choices=TipoPublicacao.choices, default=TipoPublicacao.ARTIGO_PERIODICO)
    autores = models.TextField(blank=True)
    veiculo = models.CharField(max_length=255, blank=True)
    ano = models.PositiveIntegerField(null=True, blank=True)
    doi_url = models.CharField(max_length=255, blank=True)
    criado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="publicacoes_trajetoria_criadas",
    )
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-ano", "titulo"]

    def __str__(self) -> str:
        return self.titulo

    def save(self, *args, **kwargs):
        self.titulo = (self.titulo or "").strip()
        self.veiculo = (self.veiculo or "").strip()
        self.doi_url = (self.doi_url or "").strip()
        self.full_clean()
        return super().save(*args, **kwargs)


class DisciplinaTrajetoria(models.Model):
    class Situacao(models.TextChoices):
        CURSANDO = "CURSANDO", "Cursando"
        APROVADA = "APROVADA", "Aprovada"
        REPROVADA = "REPROVADA", "Reprovada"
        TRANCADA = "TRANCADA", "Trancada"

    trajetoria = models.ForeignKey(TrajetoriaAcademica, on_delete=models.CASCADE, related_name="disciplinas")
    codigo = models.CharField(max_length=40, blank=True)
    nome = models.CharField(max_length=255)
    semestre = models.CharField(max_length=6, blank=True, validators=[Aluno.semestre_validator])
    conceito = models.CharField(max_length=20, blank=True)
    creditos = models.PositiveSmallIntegerField(null=True, blank=True)
    carga_horaria = models.PositiveSmallIntegerField(null=True, blank=True)
    situacao = models.CharField(max_length=15, choices=Situacao.choices, default=Situacao.CURSANDO)
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["semestre", "nome"]

    def __str__(self) -> str:
        return self.nome

    def save(self, *args, **kwargs):
        self.codigo = (self.codigo or "").strip()
        self.nome = (self.nome or "").strip()
        self.conceito = (self.conceito or "").strip()
        self.full_clean()
        return super().save(*args, **kwargs)


class AlteracaoAluno(models.Model):
    class TipoAlteracao(models.TextChoices):
        STATUS = "STATUS", "Status"
        QUALIFICACAO = "QUALIFICACAO", "Qualificacao"
        DEFESA = "DEFESA", "Defesa"
        DEPOSITO_FINAL = "DEPOSITO_FINAL", "Deposito versao final"
        PRAZO_QUALIFICACAO = "PRAZO_QUALIFICACAO", "Prazo qualificacao"
        PRAZO_DEFESA = "PRAZO_DEFESA", "Prazo defesa"
        ORIENTADOR = "ORIENTADOR", "Orientador"
        COORIENTADOR = "COORIENTADOR", "Coorientador"
        REINGRESSO = "REINGRESSO", "Reingresso"
        TRAJETORIA = "TRAJETORIA", "Trajetoria academica"

    aluno = models.ForeignKey(Aluno, on_delete=models.CASCADE, related_name="alteracoes")
    tipo = models.CharField(max_length=25, choices=TipoAlteracao.choices)
    valor_anterior = models.TextField(blank=True)
    valor_novo = models.TextField(blank=True)
    comentario = models.TextField()
    alterado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="alteracoes_alunos_realizadas",
    )
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-criado_em"]

    def __str__(self) -> str:
        return f"{self.aluno.nome} - {self.get_tipo_display()} - {self.criado_em:%Y-%m-%d %H:%M}"


def validar_cpf_brasileiro(cpf: str) -> bool:
    digitos = "".join(char for char in (cpf or "") if char.isdigit())
    if len(digitos) != 11 or digitos == digitos[0] * 11:
        return False

    for posicao in (9, 10):
        soma = sum(int(digitos[indice]) * (posicao + 1 - indice) for indice in range(posicao))
        verificador = (soma * 10) % 11
        if verificador == 10:
            verificador = 0
        if verificador != int(digitos[posicao]):
            return False
    return True


class SolicitacaoBanca(models.Model):
    class TipoDefesa(models.TextChoices):
        DEFESA_MESTRADO = "DEFESA_MESTRADO", "Defesa de Mestrado"
        QUALIFICACAO_DOUTORADO = "QUALIFICACAO_DOUTORADO", "Qualificação de Doutorado"
        DEFESA_DOUTORADO = "DEFESA_DOUTORADO", "Defesa de Doutorado"

    class Status(models.TextChoices):
        RASCUNHO = "RASCUNHO", "Rascunho"
        FINALIZADA = "FINALIZADA", "Finalizada"

    docente = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="solicitacoes_banca_docente",
        limit_choices_to={"tipo_usuario": User.TipoUsuario.DOCENTE},
    )
    aluno = models.ForeignKey(Aluno, on_delete=models.PROTECT, related_name="solicitacoes_banca")
    trajetoria = models.ForeignKey(
        TrajetoriaAcademica,
        on_delete=models.PROTECT,
        related_name="solicitacoes_banca",
    )
    tipo_defesa = models.CharField(max_length=30, choices=TipoDefesa.choices)
    titulo = models.CharField(max_length=255, blank=True)
    resumo = models.TextField(blank=True)
    palavras_chave = models.CharField(max_length=255, blank=True)
    data_prevista = models.DateField(null=True, blank=True)
    horario_previsto = models.TimeField(null=True, blank=True)
    modalidade_local_link = models.TextField(blank=True)
    requisitos_cumpridos = models.BooleanField(default=False)
    justificativa_excepcionalidade = models.TextField(blank=True)
    ciencia_recomendacao_mpf = models.BooleanField(default=False)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.RASCUNHO)
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)
    finalizado_em = models.DateTimeField(null=True, blank=True)
    finalizado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="solicitacoes_banca_finalizadas",
    )
    processo = models.ForeignKey(
        "Processo",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="solicitacoes_banca_anexadas",
    )

    class Meta:
        ordering = ["-atualizado_em"]

    def __str__(self) -> str:
        return f"{self.get_tipo_defesa_display()} - {self.aluno.nome}"

    @property
    def is_rascunho(self) -> bool:
        return self.status == self.Status.RASCUNHO

    def clean(self):
        errors = {}
        if self.docente and self.docente.tipo_usuario != User.TipoUsuario.DOCENTE:
            errors["docente"] = "Solicitacao de banca deve ser criada por docente."
        if self.trajetoria_id and self.aluno_id and self.trajetoria.aluno_id != self.aluno_id:
            errors["trajetoria"] = "A trajetoria selecionada nao pertence ao aluno informado."
        if self.trajetoria_id and self.docente_id:
            docente_vinculado = self.trajetoria.orientador_id == self.docente_id or self.trajetoria.coorientador_id == self.docente_id
            if not docente_vinculado:
                errors["trajetoria"] = "A trajetoria deve estar vinculada ao docente por orientacao ou coorientacao."
        if self.trajetoria_id and self.trajetoria.status != TrajetoriaAcademica.Status.ATIVA:
            errors["trajetoria"] = "A trajetoria deve estar ativa."

        if self.status == self.Status.FINALIZADA:
            campos_obrigatorios = {
                "titulo": self.titulo,
                "resumo": self.resumo,
                "palavras_chave": self.palavras_chave,
                "modalidade_local_link": self.modalidade_local_link,
            }
            for campo, valor in campos_obrigatorios.items():
                if not (valor or "").strip():
                    errors[campo] = "Campo obrigatorio para finalizar a solicitacao."
            if not self.data_prevista:
                errors["data_prevista"] = "Campo obrigatorio para finalizar a solicitacao."
            if not self.horario_previsto:
                errors["horario_previsto"] = "Campo obrigatorio para finalizar a solicitacao."
            if not self.requisitos_cumpridos:
                errors["requisitos_cumpridos"] = "Confirme que o discente cumpre os requisitos."
            if not self.ciencia_recomendacao_mpf:
                errors["ciencia_recomendacao_mpf"] = "Confirme a ciencia da recomendacao."
            if not self.finalizado_por_id:
                errors["finalizado_por"] = "Informe o usuario responsavel pela finalizacao."
            if not self.finalizado_em:
                errors["finalizado_em"] = "Informe a data de finalizacao."

        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.titulo = (self.titulo or "").strip()
        self.palavras_chave = (self.palavras_chave or "").strip()
        if self.status == self.Status.RASCUNHO:
            self.finalizado_em = None
            self.finalizado_por = None
        self.full_clean()
        return super().save(*args, **kwargs)


class MembroBanca(models.Model):
    class Papel(models.TextChoices):
        EXAMINADOR_EXTERNO = "EXAMINADOR_EXTERNO", "Examinador externo"
        EXAMINADOR_EXTERNO_1 = "EXAMINADOR_EXTERNO_1", "Examinador externo 1"
        EXAMINADOR_EXTERNO_2 = "EXAMINADOR_EXTERNO_2", "Examinador externo 2"
        EXAMINADOR_INTERNO = "EXAMINADOR_INTERNO", "Examinador interno"
        TERCEIRO_EXAMINADOR = "TERCEIRO_EXAMINADOR", "Terceiro examinador"
        QUARTO_EXAMINADOR = "QUARTO_EXAMINADOR", "Quarto examinador"
        SUPLENTE = "SUPLENTE", "Suplente"
        SUPLENTE_EXTERNO = "SUPLENTE_EXTERNO", "Suplente externo"
        SUPLENTE_INTERNO = "SUPLENTE_INTERNO", "Suplente interno"

    PAPEIS_POR_TIPO = {
        SolicitacaoBanca.TipoDefesa.DEFESA_MESTRADO: [
            Papel.EXAMINADOR_EXTERNO,
            Papel.EXAMINADOR_INTERNO,
            Papel.SUPLENTE_EXTERNO,
            Papel.SUPLENTE_INTERNO,
        ],
        SolicitacaoBanca.TipoDefesa.QUALIFICACAO_DOUTORADO: [
            Papel.EXAMINADOR_EXTERNO,
            Papel.EXAMINADOR_INTERNO,
            Papel.TERCEIRO_EXAMINADOR,
            Papel.SUPLENTE,
        ],
        SolicitacaoBanca.TipoDefesa.DEFESA_DOUTORADO: [
            Papel.EXAMINADOR_EXTERNO_1,
            Papel.EXAMINADOR_EXTERNO_2,
            Papel.EXAMINADOR_INTERNO,
            Papel.QUARTO_EXAMINADOR,
            Papel.SUPLENTE_EXTERNO,
            Papel.SUPLENTE_INTERNO,
        ],
    }

    PAPEIS_COM_INSTITUICAO = {
        Papel.EXAMINADOR_EXTERNO,
        Papel.EXAMINADOR_EXTERNO_1,
        Papel.EXAMINADOR_EXTERNO_2,
        Papel.TERCEIRO_EXAMINADOR,
        Papel.QUARTO_EXAMINADOR,
        Papel.SUPLENTE,
        Papel.SUPLENTE_EXTERNO,
    }

    PAPEIS_COM_CPF = {
        Papel.EXAMINADOR_EXTERNO,
        Papel.EXAMINADOR_EXTERNO_1,
        Papel.EXAMINADOR_EXTERNO_2,
        Papel.EXAMINADOR_INTERNO,
        Papel.TERCEIRO_EXAMINADOR,
        Papel.QUARTO_EXAMINADOR,
        Papel.SUPLENTE,
        Papel.SUPLENTE_EXTERNO,
    }

    PAPEIS_OPCIONAIS_POR_TIPO = {
        SolicitacaoBanca.TipoDefesa.DEFESA_DOUTORADO: {Papel.QUARTO_EXAMINADOR},
    }

    solicitacao = models.ForeignKey(SolicitacaoBanca, on_delete=models.CASCADE, related_name="membros")
    papel = models.CharField(max_length=30, choices=Papel.choices)
    nome = models.CharField(max_length=255, blank=True)
    instituicao = models.CharField(max_length=255, blank=True)
    cpf = models.CharField(max_length=14, blank=True)

    class Meta:
        ordering = ["id"]
        constraints = [
            models.UniqueConstraint(fields=["solicitacao", "papel"], name="unique_membro_por_papel_solicitacao"),
        ]

    def __str__(self) -> str:
        return f"{self.get_papel_display()} - {self.nome}"

    @classmethod
    def papeis_para_tipo(cls, tipo_defesa):
        return cls.PAPEIS_POR_TIPO.get(tipo_defesa, [])

    @classmethod
    def papel_opcional(cls, tipo_defesa, papel):
        return papel in cls.PAPEIS_OPCIONAIS_POR_TIPO.get(tipo_defesa, set())

    @classmethod
    def exige_instituicao(cls, papel):
        return papel in cls.PAPEIS_COM_INSTITUICAO

    @classmethod
    def exige_cpf(cls, tipo_defesa, papel):
        if papel == cls.Papel.SUPLENTE_INTERNO:
            return tipo_defesa == SolicitacaoBanca.TipoDefesa.DEFESA_MESTRADO
        return papel in cls.PAPEIS_COM_CPF

    def clean(self):
        errors = {}
        if self.papel and self.solicitacao_id:
            papeis_validos = self.papeis_para_tipo(self.solicitacao.tipo_defesa)
            if self.papel not in papeis_validos:
                errors["papel"] = "Papel de banca incompativel com o tipo de defesa."
        if self.cpf and not validar_cpf_brasileiro(self.cpf):
            errors["cpf"] = "Informe um CPF valido."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.nome = (self.nome or "").strip()
        self.instituicao = (self.instituicao or "").strip()
        self.cpf = (self.cpf or "").strip()
        self.full_clean()
        return super().save(*args, **kwargs)


class Setor(models.Model):
    class TipoSetor(models.TextChoices):
        SETOR = "SETOR", "Setor"
        COMISSAO = "COMISSAO", "Comissao"

    nome = models.CharField(max_length=120, unique=True)
    descricao = models.CharField(max_length=255, blank=True)
    ativo = models.BooleanField(default=True)
    email = models.EmailField(max_length=255, blank=True, null=True, help_text="E-mail institucional do setor")
    tipo = models.CharField(max_length=20, choices=TipoSetor.choices, default=TipoSetor.SETOR)

    class Meta:
        ordering = ["nome"]

    def __str__(self) -> str:
        return self.nome


class SetorMembro(models.Model):
    setor = models.ForeignKey(Setor, on_delete=models.CASCADE, related_name="membros")
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="participacoes_setor")
    data_entrada = models.DateField(default=timezone.localdate)
    data_saida = models.DateField(null=True, blank=True)
    designado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="designacoes_setor",
    )

    class Meta:
        ordering = ["setor__nome", "usuario__nome", "-data_entrada"]
        constraints = [
            models.UniqueConstraint(
                fields=["setor", "usuario"],
                condition=models.Q(data_saida__isnull=True),
                name="unique_membro_ativo_por_setor",
            )
        ]

    @property
    def ativo(self) -> bool:
        return self.data_saida is None

    def encerrar(self, data_saida=None):
        self.data_saida = data_saida or timezone.localdate()
        self.save(update_fields=["data_saida"])

    def __str__(self) -> str:
        status = "ativo" if self.ativo else f"ate {self.data_saida:%Y-%m-%d}"
        return f"{self.usuario} em {self.setor} ({status})"


class Processo(models.Model):
    PRAZOS_DIAS_POR_TIPO = {
        "APROVEITAMENTO_DISPENSA_CREDITOS": 30,
        "TRANCAMENTO_MATRICULA": 15,
        "PRORROGACAO_PRAZO": 20,
        "REINGRESSO": 30,
        "MUDANCA_ORIENTADOR": 20,
        "DEFESA_MESTRADO": 45,
        "DEFESA_DOUTORADO": 45,
        "QUALIFICACAO_DOUTORADO": 45,
        "OUTRO": 60,
    }

    class TipoProcesso(models.TextChoices):
        APROVEITAMENTO_DISPENSA_CREDITOS = "APROVEITAMENTO_DISPENSA_CREDITOS", "Aproveitamento de Créditos ou Dispensa de Disciplina"
        DEFESA_MESTRADO = "DEFESA_MESTRADO", "Defesa de Mestrado"
        DEFESA_DOUTORADO = "DEFESA_DOUTORADO", "Defesa de Doutorado"
        QUALIFICACAO_DOUTORADO = "QUALIFICACAO_DOUTORADO", "Qualificação de Doutorado"
        TRANCAMENTO_MATRICULA = "TRANCAMENTO_MATRICULA", "Trancamento de Matrícula"
        PRORROGACAO_PRAZO = "PRORROGACAO_PRAZO", "Prorrogação de Prazo"
        REINGRESSO = "REINGRESSO", "Reingresso"
        MUDANCA_ORIENTADOR = "MUDANCA_ORIENTADOR", "Mudança de Orientador(a)"
        OUTRO = "OUTRO", "Outro"

    class StatusProcesso(models.TextChoices):
        EM_ANALISE = "EM_ANALISE", "Em analise"
        AGUARDANDO_DOCUMENTO = "AGUARDANDO_DOCUMENTO", "Aguardando documento"
        AGUARDANDO_CIENCIA = "AGUARDANDO_CIENCIA", "Aguardando ciencia"
        EM_DEBATE = "EM_DEBATE", "Em debate"
        FINALIZADO = "FINALIZADO", "Finalizado"

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
    setor_atual = models.ForeignKey(
        Setor,
        on_delete=models.PROTECT,
        related_name="processos_atuais",
    )
    numero = models.CharField(max_length=20, unique=True, editable=False, blank=True)
    prazo_limite = models.DateField(null=True, blank=True)
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

    @property
    def esta_atrasado(self) -> bool:
        return bool(
            self.prazo_limite
            and self.prazo_limite < timezone.localdate()
            and not self.esta_finalizado
        )

    @classmethod
    def prazo_dias_para_tipo(cls, tipo_processo: str) -> int:
        return cls.PRAZOS_DIAS_POR_TIPO.get(tipo_processo, 30)

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

        if self._state.adding and not self.prazo_limite:
            self.prazo_limite = timezone.localdate() + timedelta(days=self.prazo_dias_para_tipo(self.tipo))

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
        trajetoria = (
            TrajetoriaAcademica.objects.filter(
                aluno_id=self.usuario_criado_por_id,
                status=TrajetoriaAcademica.Status.ATIVA,
            )
            .select_related("orientador")
            .order_by("-criado_em")
            .first()
        )
        if not trajetoria:
            return None
        return trajetoria.orientador

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
        prazo_limite: models.DateField | None = None,  #Parâmetro para receber a data exata
    ):
        if self.esta_finalizado:
            raise ValidationError("Nao e permitido encaminhar processo finalizado.")
        if self.manifestacoes.filter(
            tipo=ManifestacaoProcesso.TipoManifestacao.CIENTE_ORIENTADOR,
            status=ManifestacaoProcesso.StatusManifestacao.PENDENTE,
        ).exists():
            raise ValidationError("Nao e permitido encaminhar com ciente do orientador pendente.")

        # Exige data limite se o destino for o Pleno 
        if setor_destino and "pleno" in (setor_destino.nome or "").lower():
            if not prazo_limite:
                raise ValidationError(
                    {"prazo_limite": "É obrigatório informar uma data limite exata para deliberação do Pleno."}
                )
            if prazo_limite < timezone.localdate():
                raise ValidationError(
                    {"prazo_limite": "A data limite para o Pleno não pode ser uma data passada."}
                )

        status_novo = status_resultante or self.StatusProcesso.EM_ANALISE
        setor_origem = self.setor_atual

        with transaction.atomic():
            self.setor_atual = setor_destino
            self.status = status_novo
            
            if prazo_limite:
                self.prazo_limite = prazo_limite
                
            self.save(update_fields=["setor_atual", "status", "prazo_limite", "atualizado_em"])

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


class Polo(models.Model):
    nome = models.CharField(max_length=120, unique=True)
    descricao = models.CharField(max_length=255, blank=True)
    ativo = models.BooleanField(default=True)

    class Meta:
        ordering = ["nome"]

    def __str__(self) -> str:
        return self.nome


class Sala(models.Model):
    polo = models.ForeignKey(Polo, on_delete=models.PROTECT, related_name="salas")
    nome = models.CharField(max_length=120)
    capacidade = models.PositiveIntegerField(null=True, blank=True)
    ativa = models.BooleanField(default=True)

    class Meta:
        ordering = ["polo__nome", "nome"]
        constraints = [
            models.UniqueConstraint(fields=["polo", "nome"], name="unique_sala_por_polo"),
        ]

    def __str__(self) -> str:
        return f"{self.nome} - {self.polo.nome}"


class DisponibilidadeSala(models.Model):
    class DiaSemana(models.IntegerChoices):
        SEGUNDA = 0, "Segunda-feira"
        TERCA = 1, "Terca-feira"
        QUARTA = 2, "Quarta-feira"
        QUINTA = 3, "Quinta-feira"
        SEXTA = 4, "Sexta-feira"
        SABADO = 5, "Sabado"
        DOMINGO = 6, "Domingo"

    sala = models.ForeignKey(Sala, on_delete=models.CASCADE, related_name="disponibilidades")
    dia_semana = models.PositiveSmallIntegerField(choices=DiaSemana.choices)
    hora_inicio = models.TimeField()
    hora_fim = models.TimeField()

    class Meta:
        ordering = ["sala", "dia_semana", "hora_inicio"]

    def __str__(self) -> str:
        return f"{self.sala} - {self.get_dia_semana_display()} {self.hora_inicio:%H:%M}-{self.hora_fim:%H:%M}"

    def clean(self):
        if self.hora_fim <= self.hora_inicio:
            raise ValidationError({"hora_fim": "O horario final deve ser posterior ao horario inicial."})

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)


class ReservaAmbiente(models.Model):
    class TipoReserva(models.TextChoices):
        AULA = "AULA", "Aula"
        DEFESA = "DEFESA", "Defesa"
        REUNIAO_PESQUISA = "REUNIAO_PESQUISA", "Reuniao de pesquisa"

    class StatusReserva(models.TextChoices):
        ATIVA = "ATIVA", "Ativa"
        EXCLUIDA = "EXCLUIDA", "Excluida"

    sala = models.ForeignKey(Sala, on_delete=models.PROTECT, related_name="reservas")
    docente = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="reservas_docente",
        limit_choices_to={"tipo_usuario": User.TipoUsuario.DOCENTE},
    )
    criado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="reservas_criadas",
    )
    tipo = models.CharField(max_length=20, choices=TipoReserva.choices)
    titulo = models.CharField(max_length=255, blank=True)
    inicio = models.DateTimeField()
    fim = models.DateTimeField()
    recorrente = models.BooleanField(default=False)
    grupo_recorrencia = models.UUIDField(null=True, blank=True, db_index=True)
    status = models.CharField(max_length=12, choices=StatusReserva.choices, default=StatusReserva.ATIVA)
    excluida_em = models.DateTimeField(null=True, blank=True)
    excluida_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="reservas_ambiente_excluidas",
    )
    justificativa_exclusao = models.TextField(blank=True)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["inicio", "sala__nome"]

    def __str__(self) -> str:
        return f"{self.sala} - {self.inicio:%d/%m/%Y %H:%M}"

    def horario_disponivel_na_sala(self) -> bool:
        if self.inicio.date() != self.fim.date():
            return False
        dia_semana = self.inicio.weekday()
        inicio_hora = timezone.localtime(self.inicio).time() if timezone.is_aware(self.inicio) else self.inicio.time()
        fim_hora = timezone.localtime(self.fim).time() if timezone.is_aware(self.fim) else self.fim.time()
        return self.sala.disponibilidades.filter(
            dia_semana=dia_semana,
            hora_inicio__lte=inicio_hora,
            hora_fim__gte=fim_hora,
        ).exists()

    def reserva_conflitante(self):
        queryset = ReservaAmbiente.objects.filter(
            sala=self.sala,
            inicio__lt=self.fim,
            fim__gt=self.inicio,
            status=self.StatusReserva.ATIVA,
        ).select_related("sala", "sala__polo", "docente").order_by("inicio")
        if self.pk:
            queryset = queryset.exclude(pk=self.pk)
        return queryset.first()

    def tem_conflito(self):
        return self.reserva_conflitante() is not None

    @staticmethod
    def _local_datetime(valor):
        return timezone.localtime(valor) if timezone.is_aware(valor) else valor

    @classmethod
    def mensagem_conflito(cls, reserva):
        inicio = cls._local_datetime(reserva.inicio)
        fim = cls._local_datetime(reserva.fim)
        return (
            "Choque com reserva existente: "
            f"{reserva.sala.nome} - {reserva.sala.polo.nome}, "
            f"{inicio:%d/%m/%Y} das {inicio:%H:%M} as {fim:%H:%M}, "
            f"{reserva.docente.nome}, {reserva.get_tipo_display()}."
        )

    def clean(self):
        errors = {}
        if self.docente and self.docente.tipo_usuario != User.TipoUsuario.DOCENTE:
            errors["docente"] = "A reserva deve estar vinculada a um docente."
        if self.fim <= self.inicio:
            errors["fim"] = "O termino deve ser posterior ao inicio."
        elif self.inicio.date() != self.fim.date():
            errors["fim"] = "A reserva deve comecar e terminar no mesmo dia."
        if self.sala_id and self.inicio and self.fim:
            if self.status == self.StatusReserva.ATIVA and not self.horario_disponivel_na_sala():
                errors["inicio"] = "A sala nao esta disponivel neste horario."
            conflito = self.reserva_conflitante() if self.status == self.StatusReserva.ATIVA else None
            if conflito:
                errors["inicio"] = self.mensagem_conflito(conflito)
        if self.status == self.StatusReserva.EXCLUIDA and not (self.justificativa_exclusao or "").strip():
            errors["justificativa_exclusao"] = "Informe a justificativa da exclusao."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def excluir(self, *, usuario, justificativa):
        self.status = self.StatusReserva.EXCLUIDA
        self.excluida_por = usuario
        self.excluida_em = timezone.now()
        self.justificativa_exclusao = (justificativa or "").strip()
        self.save()

    @classmethod
    def criar_reservas(cls, *, sala, docente, criado_por, tipo, titulo, inicio, fim, recorrencia, duracao_recorrencia_meses):
        datas = [(inicio, fim)]
        if recorrencia != "NENHUMA":
            if not duracao_recorrencia_meses:
                raise ValidationError("Informe por quantos meses repetir.")
            if duracao_recorrencia_meses > 6:
                raise ValidationError("A recorrencia nao pode ser superior a 6 meses.")
            if duracao_recorrencia_meses < 1:
                raise ValidationError("A duracao da recorrencia deve ser de pelo menos 1 mes.")
            recorrencia_ate = cls._somar_meses(inicio, duracao_recorrencia_meses).date()
            atual_inicio, atual_fim = cls._proxima_ocorrencia(inicio, fim, recorrencia)
            while atual_inicio.date() <= recorrencia_ate:
                datas.append((atual_inicio, atual_fim))
                atual_inicio, atual_fim = cls._proxima_ocorrencia(atual_inicio, atual_fim, recorrencia)

        grupo = uuid.uuid4() if len(datas) > 1 else None
        reservas = [
            cls(
                sala=sala,
                docente=docente,
                criado_por=criado_por,
                tipo=tipo,
                titulo=titulo,
                inicio=item_inicio,
                fim=item_fim,
                recorrente=len(datas) > 1,
                grupo_recorrencia=grupo,
            )
            for item_inicio, item_fim in datas
        ]
        for reserva in reservas:
            reserva.full_clean()
        with transaction.atomic():
            return [reserva.save() or reserva for reserva in reservas]

    @classmethod
    def _proxima_ocorrencia(cls, inicio, fim, recorrencia):
        if recorrencia == "DIARIA":
            return inicio + timedelta(days=1), fim + timedelta(days=1)
        if recorrencia == "SEMANAL":
            return inicio + timedelta(days=7), fim + timedelta(days=7)
        if recorrencia == "MENSAL":
            return cls._somar_um_mes(inicio), cls._somar_um_mes(fim)
        raise ValidationError("Recorrencia invalida.")

    @staticmethod
    def _somar_um_mes(valor):
        ano = valor.year + (valor.month // 12)
        mes = (valor.month % 12) + 1
        dia = min(valor.day, calendar.monthrange(ano, mes)[1])
        return valor.replace(year=ano, month=mes, day=dia)

    @classmethod
    def _somar_meses(cls, valor, quantidade):
        resultado = valor
        for _ in range(quantidade):
            resultado = cls._somar_um_mes(resultado)
        return resultado
