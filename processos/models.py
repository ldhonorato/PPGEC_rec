import calendar
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
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
        BOLSISTA_VOLUNTARIO = "BOLSISTA_VOLUNTARIO", "Bolsista/Voluntário"

    username = None
    first_name = None
    last_name = None

    nome = models.CharField(max_length=255, verbose_name="Nome completo")
    email = models.EmailField(unique=True, verbose_name="E-mail")
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

    @classmethod
    def tipos_com_acesso_servidor(cls):
        """Perfis distintos que compartilham integralmente as regras de servidor."""
        return (cls.TipoUsuario.SERVIDOR, cls.TipoUsuario.BOLSISTA_VOLUNTARIO)


class Aluno(User):
    class NivelCurso(models.TextChoices):
        MESTRADO = "MESTRADO", "Mestrado"
        DOUTORADO = "DOUTORADO", "Doutorado"
        POSDOUTORADO = "POSDOUTORADO", "Pós-Doutorado"
        ALUNO_ESPECIAL = "ALUNO_ESPECIAL", "Aluno especial"

    class SexoAtribuidoNascimento(models.TextChoices):
        FEMININO = "FEMININO", "Feminino"
        MASCULINO = "MASCULINO", "Masculino"
        NAO_INFORMAR = "NAO_INFORMAR", "Prefiro não informar"

    class Genero(models.TextChoices):
        MULHER = "MULHER", "Mulher"
        HOMEM = "HOMEM", "Homem"
        NAO_BINARIO = "NAO_BINARIO", "Não binário"
        OUTRO = "OUTRO", "Outro"
        NAO_INFORMAR = "NAO_INFORMAR", "Prefiro não informar"

    class StatusAluno(models.TextChoices):
        EM_AVALIACAO = "EM_AVALIACAO", "Em avaliação"
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
    cpf = models.CharField(max_length=11, null=True, blank=True, unique=True)
    genero = models.CharField(max_length=15, choices=Genero.choices, blank=True)
    sexo_atribuido_nascimento = models.CharField(
        max_length=15,
        choices=SexoAtribuidoNascimento.choices,
        blank=True,
    )

    class Meta:
        verbose_name = "Aluno"
        verbose_name_plural = "Alunos"

    def clean(self):
        errors = {}

        if self.tipo_usuario and self.tipo_usuario != User.TipoUsuario.ALUNO:
            errors["tipo_usuario"] = "Aluno deve ter tipo_usuario ALUNO."
        if self.cpf and not validar_cpf_brasileiro(self.cpf):
            errors["cpf"] = "Informe um CPF válido."

        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.tipo_usuario = User.TipoUsuario.ALUNO
        self.cpf = "".join(char for char in (self.cpf or "") if char.isdigit()) or None
        self.is_active = self.status_aluno in {
            self.StatusAluno.EM_AVALIACAO,
            self.StatusAluno.ATIVO,
            self.StatusAluno.DEFENDEU,
        }
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self) -> str:
        return self.nome or self.email

    def trajetoria_ativa(self):
        trajetoria = self.trajetorias.filter(status=TrajetoriaAcademica.Status.ATIVA).order_by("-criado_em").first()
        if trajetoria:
            return trajetoria
        return self.trajetorias.filter(
            nivel_curso=self.NivelCurso.ALUNO_ESPECIAL,
            status=TrajetoriaAcademica.Status.CONCLUIDA,
        ).order_by("-criado_em").first()

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
        EM_HOMOLOGACAO = "EM_HOMOLOGACAO", "Em homologação"
        ATIVA = "ATIVA", "Ativa"
        CONCLUIDA = "CONCLUIDA", "Concluída"
        DESLIGADA = "DESLIGADA", "Desligada"
        TRANCADA = "TRANCADA", "Trancado"
        REMOVIDA = "REMOVIDA", "Removida"

    aluno = models.ForeignKey(Aluno, on_delete=models.CASCADE, related_name="trajetorias")
    nivel_curso = models.CharField(max_length=20, choices=Aluno.NivelCurso.choices)
    status = models.CharField(max_length=15, choices=Status.choices, default=Status.ATIVA)
    ingresso = models.CharField(max_length=6, validators=[Aluno.semestre_validator])
    data_ingresso = models.DateField(null=True, blank=True, verbose_name="Data de ingresso")
    prazo_qualificacao = models.CharField(max_length=6, blank=True, validators=[Aluno.semestre_validator])
    data_limite_qualificacao = models.DateField(
        null=True, blank=True, verbose_name="Data limite do projeto de dissertação"
    )
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

    @staticmethod
    def _somar_meses(valor: date, meses: int) -> date:
        indice = valor.month - 1 + meses
        ano, mes = valor.year + indice // 12, indice % 12 + 1
        dia = min(valor.day, calendar.monthrange(ano, mes)[1])
        return valor.replace(year=ano, month=mes, day=dia)

    @property
    def meses_minimos_defesa(self):
        return {Aluno.NivelCurso.MESTRADO: 12, Aluno.NivelCurso.DOUTORADO: 24}.get(self.nivel_curso)

    @property
    def meses_maximos_defesa(self):
        return {Aluno.NivelCurso.MESTRADO: 24, Aluno.NivelCurso.DOUTORADO: 48}.get(self.nivel_curso)

    @property
    def data_minima_defesa(self):
        if not self.data_ingresso or not self.meses_minimos_defesa:
            return None
        inicio_mes = self.data_ingresso.replace(day=1)
        return self._somar_meses(inicio_mes, self.meses_minimos_defesa)

    @property
    def prazo_limite_regimental(self):
        """Limite original, que nunca incorpora prorrogações ou trancamentos."""
        if not self.data_ingresso or not self.meses_maximos_defesa:
            return None
        inicio_mes = self.data_ingresso.replace(day=1)
        mes_limite = self._somar_meses(inicio_mes, self.meses_maximos_defesa)
        return self._somar_meses(mes_limite, 1) - timedelta(days=1)

    @property
    def dias_trancados(self):
        return sum((item.data_fim - item.data_inicio).days + 1 for item in self.trancamentos.all())

    @property
    def prazo_limite_efetivo(self):
        limite = self.prazo_limite_regimental
        return limite + timedelta(days=self.dias_trancados) if limite else None

    @property
    def meses_prorrogados(self):
        return sum(item.meses for item in self.prorrogacoes.all())

    @property
    def limite_prorrogacao_meses(self):
        return {Aluno.NivelCurso.MESTRADO: 6, Aluno.NivelCurso.DOUTORADO: 12}.get(self.nivel_curso, 0)

    @staticmethod
    def _somar_semestres(semestre: str, quantidade: int) -> str:
        ano, periodo = (int(parte) for parte in semestre.split("."))
        indice = ano * 2 + (periodo - 1) + quantidade
        return f"{indice // 2}.{indice % 2 + 1}"

    @property
    def prazo_qualificacao_regimental(self):
        if self.nivel_curso != Aluno.NivelCurso.DOUTORADO or not self.ingresso:
            return ""
        # O semestre de ingresso é o primeiro; portanto, o quinto fica quatro semestres depois.
        return self._somar_semestres(self.ingresso, 4)

    @property
    def usa_prazos_academicos(self) -> bool:
        return self.nivel_curso in {
            Aluno.NivelCurso.MESTRADO,
            Aluno.NivelCurso.DOUTORADO,
        }

    @property
    def usa_qualificacao(self) -> bool:
        return self.usa_prazos_academicos

    @property
    def usa_orientacao(self) -> bool:
        return self.usa_prazos_academicos

    @property
    def usa_supervisao(self) -> bool:
        return self.nivel_curso == Aluno.NivelCurso.POSDOUTORADO

    @property
    def usa_conclusao(self) -> bool:
        return self.nivel_curso in {
            Aluno.NivelCurso.MESTRADO,
            Aluno.NivelCurso.DOUTORADO,
            Aluno.NivelCurso.POSDOUTORADO,
        }

    @property
    def usa_deposito_final(self) -> bool:
        return self.nivel_curso in {
            Aluno.NivelCurso.MESTRADO,
            Aluno.NivelCurso.DOUTORADO,
        }

    @property
    def conclusao_label(self) -> str:
        if self.nivel_curso == Aluno.NivelCurso.POSDOUTORADO:
            return "Relatório final"
        return "Defesa"

    @property
    def conclusao_label_lower(self) -> str:
        return self.conclusao_label.lower()

    @property
    def numero_conclusao_label(self) -> str:
        if self.nivel_curso == Aluno.NivelCurso.POSDOUTORADO:
            return "Número do relatório final"
        return "Número da defesa"

    @property
    def data_conclusao_label(self) -> str:
        if self.nivel_curso == Aluno.NivelCurso.POSDOUTORADO:
            return "Data do relatório final"
        return "Data da defesa"

    def _normalizar_campos_por_nivel(self):
        if self.nivel_curso == Aluno.NivelCurso.ALUNO_ESPECIAL and self.status == self.Status.ATIVA:
            self.status = self.Status.CONCLUIDA
        if not self.usa_prazos_academicos:
            self.prazo_qualificacao = ""
            self.data_limite_qualificacao = None
            self.prazo_defesa = ""
            self.reingressante = False
            self.isQualificado = False
            self.coorientador = None
            self.coorientador_externo_nome = ""
            self.coorientador_externo_email = ""
            self.coorientador_externo_instituicao = ""
        if not self.usa_orientacao and not self.usa_supervisao:
            self.orientador = None
        if not self.usa_conclusao or self.status != self.Status.CONCLUIDA:
            self.numero_defesa = ""
            self.data_defesa = None
        if not self.usa_deposito_final or self.status != self.Status.CONCLUIDA:
            self.deposito_versao_final = False

    def clean(self):
        errors = {}
        self._normalizar_campos_por_nivel()

        if self.orientador and self.orientador.tipo_usuario != User.TipoUsuario.DOCENTE:
            errors["orientador"] = "O responsável deve ser um docente do PPGEC."
        if self.usa_supervisao and not self.orientador:
            errors["orientador"] = "Informe o supervisor do Pós-Doutorado."
        if self.coorientador and self.coorientador.tipo_usuario != User.TipoUsuario.DOCENTE:
            errors["coorientador"] = "Coorientador deve ser um usuário do tipo DOCENTE."
        if self.coorientador and self.coorientador_externo_nome.strip():
            errors["coorientador"] = "Informe coorientador cadastrado ou coorientador externo, não ambos."
            errors["coorientador_externo_nome"] = "Informe coorientador cadastrado ou coorientador externo, não ambos."
        if self.coorientador and self.orientador_id == self.coorientador_id:
            errors["coorientador"] = "Coorientador deve ser diferente do orientador."

        if not self.coorientador_externo_nome.strip():
            self.coorientador_externo_email = ""
            self.coorientador_externo_instituicao = ""

        if self.nivel_curso == Aluno.NivelCurso.MESTRADO and self.data_limite_qualificacao:
            semestre_data = f"{self.data_limite_qualificacao.year}.{'1' if self.data_limite_qualificacao.month <= 6 else '2'}"
            if not self.prazo_qualificacao:
                errors["prazo_qualificacao"] = "Informe o semestre do projeto de dissertação."
            elif semestre_data != self.prazo_qualificacao:
                errors["data_limite_qualificacao"] = "A data limite deve estar dentro do semestre informado."

        if self.status == self.Status.CONCLUIDA and self.usa_conclusao:
            # Usa os rotulos proprios de cada campo. Com conclusao_label_lower
            # a mensagem saia como "Informe o defesa" -- genero errado e
            # apontando para a defesa, quando o que falta e o numero dela.
            if not (self.numero_defesa or "").strip():
                errors["numero_defesa"] = (
                    f"Informe o {self.numero_conclusao_label.lower()} para concluir a trajetória."
                )
            if not self.data_defesa:
                errors["data_defesa"] = (
                    f"Informe a {self.data_conclusao_label.lower()} para concluir a trajetória."
                )
            if self.usa_prazos_academicos and self.data_ingresso and not self.isQualificado:
                errors["isQualificado"] = (
                    f"A aprovação no {self.qualificacao_label_lower} é obrigatória antes da defesa."
                )
            elif self.data_minima_defesa and self.data_defesa < self.data_minima_defesa:
                errors["data_defesa"] = (
                    f"A defesa só pode ocorrer a partir de {self.data_minima_defesa:%d/%m/%Y}."
                )
        elif self.deposito_versao_final:
            errors["deposito_versao_final"] = "Depósito da versão final só pode ser marcado após conclusão."

        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.numero_defesa = (self.numero_defesa or "").strip()
        if self.data_ingresso:
            if isinstance(self.data_ingresso, str):
                self.data_ingresso = datetime.strptime(self.data_ingresso, "%Y-%m-%d").date()
            self.data_ingresso = self.data_ingresso.replace(day=1)
        if self.nivel_curso == Aluno.NivelCurso.DOUTORADO and self.ingresso:
            self.prazo_qualificacao = self.prazo_qualificacao_regimental
            self.data_limite_qualificacao = None
        self._normalizar_campos_por_nivel()
        self.full_clean()
        return super().save(*args, **kwargs)

    @property
    def qualificacao_label(self) -> str:
        if not self.usa_qualificacao:
            return "-"
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


class ProrrogacaoTrajetoria(models.Model):
    trajetoria = models.ForeignKey(TrajetoriaAcademica, on_delete=models.CASCADE, related_name="prorrogacoes")
    meses = models.PositiveSmallIntegerField()
    data_concessao = models.DateField(default=timezone.localdate)
    justificativa = models.TextField(blank=True)
    registrado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["data_concessao", "id"]

    def clean(self):
        super().clean()
        if not self.trajetoria_id:
            return
        limite = self.trajetoria.limite_prorrogacao_meses
        recomendado = 3 if self.trajetoria.nivel_curso == Aluno.NivelCurso.MESTRADO else 6
        errors = {}
        if not limite:
            errors["trajetoria"] = "Prorrogação disponível apenas para mestrado e doutorado."
        if self.meses < 1 or self.meses > recomendado:
            errors["meses"] = f"Cada concessão pode ter de 1 a {recomendado} meses."
        anteriores = self.trajetoria.prorrogacoes.exclude(pk=self.pk)
        if sum(item.meses for item in anteriores) + self.meses > limite:
            errors["meses"] = f"O total de prorrogações não pode ultrapassar {limite} meses."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)


class TrancamentoTrajetoria(models.Model):
    trajetoria = models.ForeignKey(TrajetoriaAcademica, on_delete=models.CASCADE, related_name="trancamentos")
    data_inicio = models.DateField()
    data_fim = models.DateField()
    motivo = models.TextField(blank=True)
    registrado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["data_inicio", "id"]

    def clean(self):
        super().clean()
        errors = {}
        if self.data_inicio and self.data_fim and self.data_fim < self.data_inicio:
            errors["data_fim"] = "A data final deve ser igual ou posterior à inicial."
        if self.trajetoria_id and self.data_inicio and self.data_fim:
            sobreposto = self.trajetoria.trancamentos.exclude(pk=self.pk).filter(
                data_inicio__lte=self.data_fim, data_fim__gte=self.data_inicio
            ).exists()
            if sobreposto:
                errors["data_inicio"] = "Este período se sobrepõe a outro trancamento."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)


class ApresentacaoQualificacao(models.Model):
    class Conceito(models.TextChoices):
        A = "A", "A"
        B = "B", "B"
        C = "C", "C"

    trajetoria = models.ForeignKey(
        TrajetoriaAcademica, on_delete=models.CASCADE, related_name="apresentacoes_qualificacao"
    )
    tentativa = models.PositiveSmallIntegerField(default=1)
    data_apresentacao = models.DateField()
    conceito = models.CharField(max_length=1, choices=Conceito.choices)
    observacao = models.TextField(blank=True)
    registrado_por = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["tentativa"]
        constraints = [
            models.UniqueConstraint(fields=("trajetoria", "tentativa"), name="qualificacao_tentativa_unica")
        ]

    @property
    def aprovado(self):
        return self.conceito in {self.Conceito.A, self.Conceito.B}

    def clean(self):
        super().clean()
        errors = {}
        if not self.trajetoria_id:
            return
        existentes = self.trajetoria.apresentacoes_qualificacao.exclude(pk=self.pk).order_by("tentativa")
        primeira = existentes.first()
        self.tentativa = 2 if primeira else 1
        if self.tentativa == 2:
            if primeira.conceito != self.Conceito.C:
                errors["conceito"] = "Só é possível repetir uma apresentação que recebeu conceito C."
            meses = 3 if self.trajetoria.nivel_curso == Aluno.NivelCurso.MESTRADO else 6
            limite = TrajetoriaAcademica._somar_meses(primeira.data_apresentacao, meses)
            if self.data_apresentacao > limite:
                errors["data_apresentacao"] = f"A repetição deve ocorrer até {limite:%d/%m/%Y}."
        if existentes.count() >= 2:
            errors["tentativa"] = "É permitida apenas uma repetição."
        if self.trajetoria.nivel_curso == Aluno.NivelCurso.MESTRADO and self.tentativa == 1:
            creditos = self.trajetoria.disciplinas.filter(
                situacao=DisciplinaTrajetoria.Situacao.APROVADA
            ).aggregate(total=models.Sum("creditos"))["total"] or 0
            if creditos < 12:
                errors["trajetoria"] = "O projeto exige pelo menos 12 créditos aprovados em disciplinas (50%)."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.full_clean()
        resultado = super().save(*args, **kwargs)
        aprovado = self.trajetoria.apresentacoes_qualificacao.filter(conceito__in=[self.Conceito.A, self.Conceito.B]).exists()
        if self.trajetoria.isQualificado != aprovado:
            TrajetoriaAcademica.objects.filter(pk=self.trajetoria_id).update(isQualificado=aprovado)
            self.trajetoria.isQualificado = aprovado
        return resultado

class PublicacaoTrajetoria(models.Model):
    class TipoPublicacao(models.TextChoices):
        ARTIGO_PERIODICO = "ARTIGO_PERIODICO", "Artigo em periodico"
        ARTIGO_EVENTO = "ARTIGO_EVENTO", "Artigo em evento"
        LIVRO_CAPITULO = "LIVRO_CAPITULO", "Livro/capitulo"
        OUTRO = "OUTRO", "Outro"

    trajetoria = models.ForeignKey(TrajetoriaAcademica, on_delete=models.CASCADE, related_name="publicacoes")
    titulo = models.CharField(max_length=255, verbose_name="Título")
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
    codigo = models.CharField(max_length=40, blank=True, verbose_name="Código")
    nome = models.CharField(max_length=255)
    semestre = models.CharField(max_length=6, blank=True, validators=[Aluno.semestre_validator])
    conceito = models.CharField(max_length=20, blank=True)
    creditos = models.PositiveSmallIntegerField(null=True, blank=True)
    carga_horaria = models.PositiveSmallIntegerField(null=True, blank=True)
    situacao = models.CharField(max_length=15, choices=Situacao.choices, default=Situacao.CURSANDO, verbose_name="Situação")
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


class Disciplina(models.Model):
    class Tipo(models.TextChoices):
        """Categoria da disciplina na estrutura curricular do programa.

        O campo era texto livre de 120 caracteres, digitado a mao no formulario
        de cadastro. Com 47 disciplinas cadastradas, ja havia cinco grafias para
        tres categorias: "Disciplina Basica" sem acento ao lado de "Disciplina
        Básica", "Disciplina Eletiva Área" e um "Obrigatória" solto.

        Isso nao e so inconsistencia de escrita: enquanto o tipo e texto livre
        nao ha como filtrar por categoria nem contar quantas eletivas o aluno
        cursou, que e o que a integralizacao precisa saber.
        """

        BASICA = "BASICA", "Básica"
        ELETIVA_GERAL = "ELETIVA_GERAL", "Eletiva geral"
        ELETIVA_ESPECIFICA = "ELETIVA_ESPECIFICA", "Eletiva específica"

    codigo = models.CharField(max_length=40, unique=True, verbose_name="Código")
    nome = models.CharField(max_length=255)
    tipo = models.CharField(max_length=25, choices=Tipo.choices, blank=True)
    creditos = models.PositiveSmallIntegerField(null=True, blank=True)
    carga_horaria = models.PositiveSmallIntegerField(null=True, blank=True)
    pre_requisitos = models.TextField(blank=True)
    ementa = models.TextField(blank=True)
    bibliografia = models.TextField(blank=True)
    ativa = models.BooleanField(default=True)
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["codigo", "nome"]

    def __str__(self) -> str:
        return f"{self.codigo} - {self.nome}" if self.codigo else self.nome

    def save(self, *args, **kwargs):
        self.codigo = (self.codigo or "").strip().upper()
        self.nome = (self.nome or "").strip()
        self.tipo = (self.tipo or "").strip()
        self.pre_requisitos = (self.pre_requisitos or "").strip()
        self.ementa = (self.ementa or "").strip()
        self.bibliografia = (self.bibliografia or "").strip()
        self.full_clean()
        return super().save(*args, **kwargs)


class PeriodoLetivo(models.Model):
    class Status(models.TextChoices):
        PLANEJAMENTO = "PLANEJAMENTO", "Planejamento"
        MATRICULA_ABERTA = "MATRICULA_ABERTA", "Matrícula aberta"
        MODIFICACAO_MATRICULA = "MODIFICACAO_MATRICULA", "Modificação de matrícula"
        ENCERRADO = "ENCERRADO", "Encerrado"

    nome = models.CharField(max_length=20, unique=True, validators=[Aluno.semestre_validator])
    status = models.CharField(max_length=25, choices=Status.choices, default=Status.PLANEJAMENTO)
    data_inicio = models.DateField(null=True, blank=True, verbose_name="Início do período letivo")
    data_fim = models.DateField(null=True, blank=True)
    prazo_cadastro_disciplinas = models.DateField()
    prazo_agendamento_aulas_presenciais = models.DateField(null=True, blank=True)
    matricula_inicio = models.DateField(verbose_name="Início da matrícula")
    matricula_fim = models.DateField(verbose_name="Fim da matrícula")
    modificacao_inicio = models.DateField(verbose_name="Início da modificação")
    modificacao_fim = models.DateField(verbose_name="Fim da modificação")
    encerrado_manualmente_em = models.DateTimeField(null=True, blank=True)
    encerrado_manualmente_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="periodos_letivos_encerrados",
    )
    criado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="periodos_letivos_criados",
    )
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-nome"]

    def __str__(self) -> str:
        return self.nome

    @property
    def status_atual(self):
        return self.status

    def calcular_status_por_data(self, data_base=None):
        data_base = data_base or timezone.localdate()
        if self.encerrado_manualmente_em:
            return self.Status.ENCERRADO
        if self.matricula_inicio <= data_base <= self.matricula_fim:
            return self.Status.MATRICULA_ABERTA
        if self.modificacao_inicio <= data_base <= self.modificacao_fim:
            return self.Status.MODIFICACAO_MATRICULA
        if data_base > self.modificacao_fim:
            return self.Status.ENCERRADO
        return self.Status.PLANEJAMENTO

    def atualizar_status_por_data(self, data_base=None, *, save=True):
        novo_status = self.calcular_status_por_data(data_base)
        if self.status == novo_status:
            return False
        self.status = novo_status
        if save:
            self.save(update_fields=["status", "atualizado_em"])
        return True

    @property
    def status_atual_display(self):
        return self.get_status_display()

    @property
    def aceita_ofertas(self):
        return not self.encerrado_manualmente_em and timezone.localdate() <= self.prazo_cadastro_disciplinas

    @property
    def aceita_solicitacao_matricula(self):
        return self.status in {
            self.Status.MATRICULA_ABERTA,
            self.Status.MODIFICACAO_MATRICULA,
        }

    def clean(self):
        errors = {}
        if self.matricula_inicio and self.matricula_fim and self.matricula_fim < self.matricula_inicio:
            errors["matricula_fim"] = "O fim da matrícula deve ser posterior ou igual ao início."
        if self.data_inicio and self.data_fim and self.data_fim < self.data_inicio:
            errors["data_fim"] = "O fim do período letivo deve ser posterior ou igual ao início."
        if self.prazo_agendamento_aulas_presenciais and self.data_fim and self.prazo_agendamento_aulas_presenciais > self.data_fim:
            errors["prazo_agendamento_aulas_presenciais"] = "O prazo deve estar dentro do período letivo."
        if self.modificacao_inicio and self.modificacao_fim and self.modificacao_fim < self.modificacao_inicio:
            errors["modificacao_fim"] = "O fim da modificação deve ser posterior ou igual ao início."
        if self.modificacao_inicio and self.matricula_fim and self.modificacao_inicio < self.matricula_fim:
            errors["modificacao_inicio"] = "A modificação de matrícula deve iniciar após o fim da matrícula."
        if (
            self.prazo_cadastro_disciplinas
            and self.matricula_inicio
            and self.prazo_cadastro_disciplinas > self.matricula_inicio
        ):
            errors["prazo_cadastro_disciplinas"] = "O cadastro de disciplinas deve encerrar antes do início da matrícula."
        if bool(self.encerrado_manualmente_em) != bool(self.encerrado_manualmente_por_id):
            errors["encerrado_manualmente_em"] = "Informe data e responsável pelo encerramento manual."
        if self.status in {self.Status.MATRICULA_ABERTA, self.Status.MODIFICACAO_MATRICULA}:
            periodos_ativos = PeriodoLetivo.objects.filter(
                status__in=[self.Status.MATRICULA_ABERTA, self.Status.MODIFICACAO_MATRICULA],
                encerrado_manualmente_em__isnull=True,
            )
            if self.pk:
                periodos_ativos = periodos_ativos.exclude(pk=self.pk)
            if periodos_ativos.exists():
                errors["status"] = "Só pode haver um período letivo ativo no sistema."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.nome = (self.nome or "").strip()
        self.full_clean()
        return super().save(*args, **kwargs)


class OfertaDisciplina(models.Model):
    class Modalidade(models.TextChoices):
        PRESENCIAL = "PRESENCIAL", "Presencial"
        HIBRIDA = "HIBRIDA", "Híbrida"

    periodo = models.ForeignKey(PeriodoLetivo, on_delete=models.CASCADE, related_name="ofertas", verbose_name="Período")
    disciplina = models.ForeignKey(Disciplina, on_delete=models.PROTECT, related_name="ofertas")
    docente_responsavel = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="ofertas_disciplinas",
        limit_choices_to={"tipo_usuario": User.TipoUsuario.DOCENTE},
    )
    docente_colaborador = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="ofertas_disciplinas_colaboracao",
        limit_choices_to={"tipo_usuario": User.TipoUsuario.DOCENTE},
    )
    modalidade = models.CharField(max_length=12, choices=Modalidade.choices, default=Modalidade.PRESENCIAL)
    vagas_regulares = models.PositiveSmallIntegerField(default=0)
    vagas_especiais = models.PositiveSmallIntegerField(default=0)
    criada_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="ofertas_disciplinas_criadas",
    )
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["periodo__nome", "disciplina__nome", "docente_responsavel__nome"]
        constraints = [
            models.UniqueConstraint(
                fields=["periodo", "disciplina", "docente_responsavel"],
                name="unique_oferta_disciplina_periodo_docente",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.disciplina} - {self.periodo}"

    def clean(self):
        errors = {}
        if self.docente_responsavel and self.docente_responsavel.tipo_usuario != User.TipoUsuario.DOCENTE:
            errors["docente_responsavel"] = "O responsável deve ser docente."
        if self.docente_colaborador and self.docente_colaborador.tipo_usuario != User.TipoUsuario.DOCENTE:
            errors["docente_colaborador"] = "O colaborador deve ser docente."
        if self.docente_colaborador_id and self.docente_colaborador_id == self.docente_responsavel_id:
            errors["docente_colaborador"] = "O segundo docente deve ser diferente do docente responsável."
        if (self.vagas_regulares or 0) == 0 and (self.vagas_especiais or 0) == 0:
            errors["vagas_regulares"] = "Informe ao menos uma vaga regular ou especial."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def vagas_ocupadas(self, tipo_aluno):
        return self.itens_matricula.filter(
            solicitacao__tipo_aluno=tipo_aluno,
            status__in=[
                ItemSolicitacaoMatricula.Status.SOLICITADO,
                ItemSolicitacaoMatricula.Status.HOMOLOGADO,
            ],
        ).count()

    def vagas_totais(self, tipo_aluno):
        if tipo_aluno == SolicitacaoMatricula.TipoAluno.ESPECIAL:
            return self.vagas_especiais
        return self.vagas_regulares

    def vagas_disponiveis(self, tipo_aluno):
        return max(self.vagas_totais(tipo_aluno) - self.vagas_ocupadas(tipo_aluno), 0)

    @property
    def docentes_display(self):
        nomes = [self.docente_responsavel.nome]
        if self.docente_colaborador_id:
            nomes.append(self.docente_colaborador.nome)
        return " / ".join(nomes)


class EncontroOferta(models.Model):
    class DiaSemana(models.IntegerChoices):
        SEGUNDA = 0, "Segunda-feira"
        TERCA = 1, "Terça-feira"
        QUARTA = 2, "Quarta-feira"
        QUINTA = 3, "Quinta-feira"
        SEXTA = 4, "Sexta-feira"
        SABADO = 5, "Sábado"
        DOMINGO = 6, "Domingo"

    oferta = models.ForeignKey(OfertaDisciplina, on_delete=models.CASCADE, related_name="encontros")
    dia_semana = models.PositiveSmallIntegerField(choices=DiaSemana.choices)
    hora_inicio = models.TimeField(verbose_name="Hora de início")
    hora_fim = models.TimeField()

    class Meta:
        ordering = ["oferta", "dia_semana", "hora_inicio"]

    def __str__(self) -> str:
        return f"{self.oferta} - {self.get_dia_semana_display()} {self.hora_inicio:%H:%M}-{self.hora_fim:%H:%M}"

    def clean(self):
        errors = {}
        if self.hora_inicio and self.hora_fim and self.hora_fim <= self.hora_inicio:
            errors["hora_fim"] = "O horário final deve ser posterior ao horário inicial."
        if self.oferta_id:
            encontros = self.oferta.encontros.all()
            if self.pk:
                encontros = encontros.exclude(pk=self.pk)
            if encontros.count() >= 2:
                errors["oferta"] = "A oferta pode ter no máximo dois encontros semanais."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)


class AulaPresencialOferta(models.Model):
    oferta = models.ForeignKey(OfertaDisciplina, on_delete=models.CASCADE, related_name="aulas_presenciais")
    encontro = models.ForeignKey(
        EncontroOferta,
        on_delete=models.SET_NULL,
        related_name="aulas_presenciais",
        null=True,
        blank=True,
    )
    data = models.DateField()
    hora_inicio = models.TimeField(verbose_name="Hora de início")
    hora_fim = models.TimeField()
    sala = models.ForeignKey("Sala", on_delete=models.PROTECT, related_name="aulas_presenciais_ofertas")
    reserva = models.OneToOneField(
        "ReservaAmbiente",
        on_delete=models.PROTECT,
        related_name="aula_presencial_oferta",
        null=True,
        blank=True,
    )
    criado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="aulas_presenciais_ofertas_criadas",
    )
    atualizado_em = models.DateTimeField(auto_now=True)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["data", "hora_inicio"]
        constraints = [
            models.UniqueConstraint(fields=["oferta", "data", "hora_inicio", "hora_fim"], name="unique_aula_presencial_oferta_data_horario"),
        ]

    def __str__(self) -> str:
        return f"{self.oferta} - {self.data:%d/%m/%Y}"

    @property
    def carga_horaria_minutos(self):
        inicio = datetime.combine(self.data, self.hora_inicio)
        fim = datetime.combine(self.data, self.hora_fim)
        return int((fim - inicio).total_seconds() // 60)

    def clean(self):
        errors = {}
        if self.oferta_id and self.oferta.modalidade != OfertaDisciplina.Modalidade.HIBRIDA:
            errors["oferta"] = "O planejamento presencial é exigido apenas para ofertas híbridas."
        if self.oferta_id and self.encontro_id and self.encontro.oferta_id != self.oferta_id:
            errors["encontro"] = "O encontro deve pertencer à oferta."
        if self.hora_inicio and self.hora_fim and self.hora_fim <= self.hora_inicio:
            errors["hora_fim"] = "O horário final deve ser posterior ao horário inicial."
        if self.oferta_id and self.oferta.periodo.data_inicio and self.data and self.data < self.oferta.periodo.data_inicio:
            errors["data"] = "A aula deve ocorrer dentro do período letivo."
        if self.oferta_id and self.oferta.periodo.data_fim and self.data and self.data > self.oferta.periodo.data_fim:
            errors["data"] = "A aula deve ocorrer dentro do período letivo."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if self.encontro_id:
            self.hora_inicio = self.hora_inicio or self.encontro.hora_inicio
            self.hora_fim = self.hora_fim or self.encontro.hora_fim
        self.full_clean()
        return super().save(*args, **kwargs)


class SolicitacaoMatricula(models.Model):
    class TipoMatricula(models.TextChoices):
        DISCIPLINAS = "DISCIPLINAS", "Disciplinas"
        VINCULO = "VINCULO", "Matrícula vínculo"

    class TipoAluno(models.TextChoices):
        REGULAR = "REGULAR", "Regular"
        ESPECIAL = "ESPECIAL", "Especial"

    class Status(models.TextChoices):
        RASCUNHO = "RASCUNHO", "Rascunho"
        SOLICITADA = "SOLICITADA", "Solicitada"
        PARCIALMENTE_HOMOLOGADA = "PARCIALMENTE_HOMOLOGADA", "Parcialmente processada"
        HOMOLOGADA = "HOMOLOGADA", "Processada"
        INDEFERIDA = "INDEFERIDA", "Indeferida"
        CANCELADA = "CANCELADA", "Cancelada"

    periodo = models.ForeignKey(PeriodoLetivo, on_delete=models.PROTECT, related_name="solicitacoes_matricula", verbose_name="Período")
    aluno = models.ForeignKey(Aluno, on_delete=models.PROTECT, related_name="solicitacoes_matricula")
    tipo_matricula = models.CharField(max_length=12, choices=TipoMatricula.choices, default=TipoMatricula.DISCIPLINAS)
    tipo_aluno = models.CharField(max_length=10, choices=TipoAluno.choices, default=TipoAluno.REGULAR)
    status = models.CharField(max_length=25, choices=Status.choices, default=Status.RASCUNHO)
    observacao_aluno = models.TextField(blank=True)
    observacao_secretaria = models.TextField(blank=True)
    solicitada_em = models.DateTimeField(null=True, blank=True)
    homologada_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="solicitacoes_matricula_homologadas",
    )
    homologada_em = models.DateTimeField(null=True, blank=True)
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-criado_em"]
        constraints = [
            models.UniqueConstraint(fields=["periodo", "aluno"], name="unique_solicitacao_matricula_aluno_periodo"),
        ]

    def __str__(self) -> str:
        return f"{self.aluno} - {self.periodo}"

    def atualizar_status_por_itens(self, *, usuario=None):
        itens = list(self.itens.all())
        if not itens:
            if self.tipo_matricula == self.TipoMatricula.VINCULO and self.status in {
                self.Status.SOLICITADA,
                self.Status.HOMOLOGADA,
                self.Status.INDEFERIDA,
                self.Status.CANCELADA,
            }:
                pass
            else:
                self.status = self.Status.RASCUNHO
                self.homologada_em = None
                self.homologada_por = None
        elif all(item.status == ItemSolicitacaoMatricula.Status.CANCELADO for item in itens):
            self.status = self.Status.CANCELADA
        elif all(item.status == ItemSolicitacaoMatricula.Status.INDEFERIDO for item in itens):
            self.status = self.Status.INDEFERIDA
        elif all(item.status == ItemSolicitacaoMatricula.Status.HOMOLOGADO for item in itens):
            self.status = self.Status.HOMOLOGADA
            self.homologada_em = self.homologada_em or timezone.now()
            self.homologada_por = self.homologada_por or usuario
        elif any(item.status == ItemSolicitacaoMatricula.Status.HOMOLOGADO for item in itens):
            self.status = self.Status.PARCIALMENTE_HOMOLOGADA
        else:
            self.status = self.Status.SOLICITADA
        self.save(update_fields=["status", "homologada_em", "homologada_por", "atualizado_em"])

    def save(self, *args, **kwargs):
        self.observacao_aluno = (self.observacao_aluno or "").strip()
        self.observacao_secretaria = (self.observacao_secretaria or "").strip()
        if self.status != self.Status.HOMOLOGADA:
            self.homologada_em = None
            self.homologada_por = None
        if self.status == self.Status.SOLICITADA and not self.solicitada_em:
            self.solicitada_em = timezone.now()
        self.full_clean()
        return super().save(*args, **kwargs)


class ItemSolicitacaoMatricula(models.Model):
    class FaseInclusao(models.TextChoices):
        MATRICULA = "MATRICULA", "Matrícula"
        MODIFICACAO = "MODIFICACAO", "Modificação de matrícula"

    class Status(models.TextChoices):
        SOLICITADO = "SOLICITADO", "Matrícula solicitada"
        HOMOLOGADO = "HOMOLOGADO", "Matrícula solicitada"
        EM_LISTA_ESPERA = "EM_LISTA_ESPERA", "Matrícula solicitada - em lista de espera"
        INDEFERIDO = "INDEFERIDO", "Indeferido"
        CANCELADO = "CANCELADO", "Cancelado"

    solicitacao = models.ForeignKey(SolicitacaoMatricula, on_delete=models.CASCADE, related_name="itens")
    oferta = models.ForeignKey(OfertaDisciplina, on_delete=models.PROTECT, related_name="itens_matricula")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.SOLICITADO)
    incluido_na_fase = models.CharField(
        max_length=12,
        choices=FaseInclusao.choices,
        default=FaseInclusao.MATRICULA,
    )
    solicitado_em = models.DateTimeField(auto_now_add=True)
    homologado_em = models.DateTimeField(null=True, blank=True)
    homologado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="itens_matricula_homologados",
    )
    indeferido_em = models.DateTimeField(null=True, blank=True)
    indeferido_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="itens_matricula_indeferidos",
    )
    motivo_indeferimento = models.TextField(blank=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["solicitado_em", "id"]
        constraints = [
            models.UniqueConstraint(fields=["solicitacao", "oferta"], name="unique_item_matricula_por_oferta"),
        ]

    def __str__(self) -> str:
        return f"{self.solicitacao.aluno} - {self.oferta}"

    def clean(self):
        errors = {}
        if self.solicitacao_id and self.oferta_id and self.solicitacao.periodo_id != self.oferta.periodo_id:
            errors["oferta"] = "A oferta deve pertencer ao período da solicitação."
        if self.status == self.Status.HOMOLOGADO and not (self.homologado_em and self.homologado_por_id):
            errors["status"] = "Informe data e responsável pela homologação."
        if self.status == self.Status.INDEFERIDO and not (self.indeferido_em and self.indeferido_por_id):
            errors["status"] = "Informe data e responsável pelo indeferimento."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.motivo_indeferimento = (self.motivo_indeferimento or "").strip()
        if self.status != self.Status.HOMOLOGADO:
            self.homologado_em = None
            self.homologado_por = None
        if self.status != self.Status.INDEFERIDO:
            self.indeferido_em = None
            self.indeferido_por = None
            self.motivo_indeferimento = ""
        self.full_clean()
        return super().save(*args, **kwargs)


class AlteracaoMatricula(models.Model):
    class Acao(models.TextChoices):
        SOLICITACAO_CRIADA = "SOLICITACAO_CRIADA", "Solicitação criada"
        MATRICULA_VINCULO_SOLICITADA = "MATRICULA_VINCULO_SOLICITADA", "Matrícula vínculo solicitada"
        MATRICULA_VINCULO_INDEFERIDA = "MATRICULA_VINCULO_INDEFERIDA", "Matrícula vínculo indeferida"
        TIPO_MATRICULA_ALTERADO = "TIPO_MATRICULA_ALTERADO", "Tipo de matrícula alterado"
        DISCIPLINA_INCLUIDA = "DISCIPLINA_INCLUIDA", "Disciplina incluída"
        DISCIPLINA_REINCLUIDA = "DISCIPLINA_REINCLUIDA", "Disciplina reincluída"
        DISCIPLINA_CANCELADA = "DISCIPLINA_CANCELADA", "Disciplina cancelada"
        DISCIPLINA_INDEFERIDA = "DISCIPLINA_INDEFERIDA", "Disciplina indeferida"
        LISTA_ESPERA_PROMOVIDA = "LISTA_ESPERA_PROMOVIDA", "Promoção da lista de espera"

    class Fase(models.TextChoices):
        MATRICULA = "MATRICULA", "Matrícula"
        MODIFICACAO = "MODIFICACAO", "Modificação de matrícula"
        ADMINISTRATIVA = "ADMINISTRATIVA", "Ação administrativa"

    solicitacao = models.ForeignKey(
        SolicitacaoMatricula,
        on_delete=models.CASCADE,
        related_name="alteracoes",
    )
    item = models.ForeignKey(
        ItemSolicitacaoMatricula,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="alteracoes",
    )
    oferta = models.ForeignKey(
        OfertaDisciplina,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="alteracoes_matricula",
    )
    acao = models.CharField(max_length=35, choices=Acao.choices)
    fase = models.CharField(max_length=15, choices=Fase.choices)
    realizado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="alteracoes_matricula_realizadas",
    )
    estado_anterior = models.JSONField(default=dict, blank=True)
    estado_novo = models.JSONField(default=dict, blank=True)
    justificativa = models.TextField(blank=True)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["criado_em", "id"]
        indexes = [
            models.Index(fields=["solicitacao", "fase", "criado_em"]),
            models.Index(fields=["oferta", "fase", "criado_em"]),
        ]

    def __str__(self) -> str:
        return f"{self.solicitacao} - {self.get_acao_display()}"

    def save(self, *args, **kwargs):
        self.justificativa = (self.justificativa or "").strip()
        self.full_clean()
        return super().save(*args, **kwargs)


class AlteracaoAluno(models.Model):
    class TipoAlteracao(models.TextChoices):
        STATUS = "STATUS", "Status"
        QUALIFICACAO = "QUALIFICACAO", "Qualificação"
        HORAS_COMPLEMENTARES = "HORAS_COMPLEMENTARES", "Horas complementares"
        DEFESA = "DEFESA", "Defesa"
        DEPOSITO_FINAL = "DEPOSITO_FINAL", "Depósito versão final"
        PRAZO_QUALIFICACAO = "PRAZO_QUALIFICACAO", "Prazo qualificação"
        PRAZO_DEFESA = "PRAZO_DEFESA", "Prazo defesa"
        ORIENTADOR = "ORIENTADOR", "Orientador"
        COORIENTADOR = "COORIENTADOR", "Coorientador"
        REINGRESSO = "REINGRESSO", "Reingresso"
        TRAJETORIA = "TRAJETORIA", "Trajetória acadêmica"
        ESTAGIO_DOCENCIA = "ESTAGIO_DOCENCIA", "Estágio docência"

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


class NormaHorasComplementares(models.Model):
    class Status(models.TextChoices):
        RASCUNHO = "RASCUNHO", "Rascunho"
        VIGENTE = "VIGENTE", "Vigente"
        REVOGADA = "REVOGADA", "Revogada"

    nome = models.CharField(max_length=255)
    identificacao = models.CharField(max_length=80)
    descricao = models.TextField(blank=True, verbose_name="Descrição")
    inicio_vigencia = models.DateField()
    fim_vigencia = models.DateField(null=True, blank=True)
    carga_horaria_exigida = models.PositiveIntegerField(default=45)
    nivel_curso = models.CharField(max_length=20, choices=Aluno.NivelCurso.choices)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.RASCUNHO)
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-inicio_vigencia", "identificacao", "nivel_curso"]
        unique_together = ("identificacao", "nivel_curso")

    def __str__(self) -> str:
        return f"{self.identificacao} - {self.get_nivel_curso_display()}"


class GrupoLimiteHorasComplementares(models.Model):
    norma = models.ForeignKey(
        NormaHorasComplementares,
        on_delete=models.PROTECT,
        related_name="grupos_limite",
    )
    nome = models.CharField(max_length=120)
    descricao = models.TextField(blank=True, verbose_name="Descrição")
    limite_maximo = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    ordem = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["ordem", "nome"]
        unique_together = ("norma", "nome")

    def __str__(self) -> str:
        return self.nome


class TipoAtividadeHorasComplementares(models.Model):
    norma = models.ForeignKey(
        NormaHorasComplementares,
        on_delete=models.PROTECT,
        related_name="tipos_atividade",
    )
    grupo_limite = models.ForeignKey(
        GrupoLimiteHorasComplementares,
        on_delete=models.PROTECT,
        related_name="tipos_atividade",
        null=True,
        blank=True,
    )
    nome = models.CharField(max_length=180)
    descricao = models.TextField(blank=True, verbose_name="Descrição")
    unidade_calculo = models.CharField(max_length=40)
    horas_por_unidade = models.DecimalField(max_digits=6, decimal_places=2)
    limite_individual = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    ativo = models.BooleanField(default=True)
    ordem = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["ordem", "nome"]
        unique_together = ("norma", "nome")

    def __str__(self) -> str:
        return self.nome


class LancamentoHorasComplementares(models.Model):
    class Status(models.TextChoices):
        ATIVO = "ATIVO", "Ativo"
        CANCELADO = "CANCELADO", "Cancelado"
        RETIFICADO = "RETIFICADO", "Retificado"

    trajetoria = models.ForeignKey(
        TrajetoriaAcademica,
        on_delete=models.PROTECT,
        related_name="lancamentos_horas_complementares",
    )
    processo_origem = models.ForeignKey(
        "Processo",
        on_delete=models.PROTECT,
        related_name="lancamentos_horas_complementares",
        null=True,
        blank=True,
    )
    tipo_atividade = models.ForeignKey(
        TipoAtividadeHorasComplementares,
        on_delete=models.PROTECT,
        related_name="lancamentos",
    )
    norma = models.ForeignKey(
        NormaHorasComplementares,
        on_delete=models.PROTECT,
        related_name="lancamentos",
    )
    grupo_limite = models.ForeignKey(
        GrupoLimiteHorasComplementares,
        on_delete=models.PROTECT,
        related_name="lancamentos",
        null=True,
        blank=True,
    )
    descricao = models.CharField(max_length=255, verbose_name="Descrição")
    periodo_realizacao = models.CharField(max_length=120, blank=True, verbose_name="Período de realização")
    quantidade = models.DecimalField(max_digits=8, decimal_places=2)
    unidade_quantidade = models.CharField(max_length=40)
    horas_solicitadas = models.DecimalField(max_digits=6, decimal_places=2, default=0)
    horas_calculadas = models.DecimalField(max_digits=6, decimal_places=2, default=0)
    horas_aprovadas = models.DecimalField(max_digits=6, decimal_places=2)
    limite_grupo_no_lancamento = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    limite_individual_no_lancamento = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    observacoes_secretaria = models.TextField(blank=True)
    referencia_decisao = models.TextField(blank=True, verbose_name="Referência da decisão")
    justificativa_excepcional = models.TextField(blank=True)
    excepcional_autorizado = models.BooleanField(default=False)
    criado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="lancamentos_horas_complementares_criados",
    )
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.ATIVO)
    substitui_lancamento = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="retificacoes",
    )
    cancelado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="lancamentos_horas_complementares_cancelados",
        null=True,
        blank=True,
    )
    cancelado_em = models.DateTimeField(null=True, blank=True)
    justificativa_cancelamento = models.TextField(blank=True)
    origem_migracao = models.BooleanField(default=False)
    justificativa_sem_processo = models.TextField(blank=True)

    class Meta:
        ordering = ["-criado_em"]

    @property
    def aluno(self):
        return self.trajetoria.aluno

    def __str__(self) -> str:
        return f"{self.aluno.nome} - {self.tipo_atividade.nome} - {self.horas_aprovadas}h"

    @classmethod
    def ativos(cls):
        return cls.objects.filter(status=cls.Status.ATIVO)

    @classmethod
    def norma_para_trajetoria(cls, trajetoria):
        nivel = trajetoria.nivel_curso if trajetoria else Aluno.NivelCurso.MESTRADO
        hoje = timezone.localdate()
        norma = (
            NormaHorasComplementares.objects.filter(
                nivel_curso=nivel,
                status=NormaHorasComplementares.Status.VIGENTE,
                inicio_vigencia__lte=hoje,
            )
            .filter(models.Q(fim_vigencia__isnull=True) | models.Q(fim_vigencia__gte=hoje))
            .order_by("-inicio_vigencia")
            .first()
        )
        if norma:
            return norma
        return (
            NormaHorasComplementares.objects.filter(
                nivel_curso=nivel,
                status=NormaHorasComplementares.Status.VIGENTE,
            )
            .order_by("-inicio_vigencia")
            .first()
        )

    @classmethod
    def norma_para_aluno(cls, aluno):
        return cls.norma_para_trajetoria(aluno.trajetoria_ativa())

    @staticmethod
    def _sum_horas(queryset):
        return queryset.aggregate(total=models.Sum("horas_aprovadas"))["total"] or 0

    def horas_do_grupo_ja_aprovadas(self):
        if not self.grupo_limite_id:
            return 0
        queryset = self.ativos().filter(trajetoria=self.trajetoria, grupo_limite=self.grupo_limite)
        if self.pk:
            queryset = queryset.exclude(pk=self.pk)
        if self.substitui_lancamento_id:
            queryset = queryset.exclude(pk=self.substitui_lancamento_id)
        return self._sum_horas(queryset)

    def horas_da_atividade_ja_aprovadas(self):
        queryset = self.ativos().filter(trajetoria=self.trajetoria, tipo_atividade=self.tipo_atividade)
        if self.pk:
            queryset = queryset.exclude(pk=self.pk)
        if self.substitui_lancamento_id:
            queryset = queryset.exclude(pk=self.substitui_lancamento_id)
        return self._sum_horas(queryset)

    def maximo_aprovavel(self):
        saldos = []
        if self.grupo_limite and self.grupo_limite.limite_maximo is not None:
            saldos.append(max(self.grupo_limite.limite_maximo - self.horas_do_grupo_ja_aprovadas(), 0))
        if self.tipo_atividade and self.tipo_atividade.limite_individual is not None:
            saldos.append(max(self.tipo_atividade.limite_individual - self.horas_da_atividade_ja_aprovadas(), 0))
        return min(saldos) if saldos else None

    def clean(self):
        errors = {}
        if self.trajetoria_id and self.trajetoria.nivel_curso == Aluno.NivelCurso.POSDOUTORADO:
            raise ValidationError(
                {"trajetoria": "Trajetórias de Pós-Doutorado não possuem horas complementares."}
            )
        if self.tipo_atividade_id:
            self.norma = self.tipo_atividade.norma
            self.grupo_limite = self.tipo_atividade.grupo_limite
            self.unidade_quantidade = self.tipo_atividade.unidade_calculo
            self._normalizar_horas_calculadas()
            self.limite_individual_no_lancamento = self.tipo_atividade.limite_individual
            self.limite_grupo_no_lancamento = (
                self.tipo_atividade.grupo_limite.limite_maximo if self.tipo_atividade.grupo_limite else None
            )
        if not self.processo_origem_id and not self.origem_migracao and not self.justificativa_sem_processo:
            errors["processo_origem"] = "Informe o processo de origem ou justifique o lançamento sem processo."
        if self.processo_origem_id and self.processo_origem.aluno_interessado_id != self.trajetoria.aluno_id:
            errors["processo_origem"] = "O processo de origem deve pertencer ao discente informado."
        if self.horas_aprovadas is not None and self.horas_calculadas and self.horas_aprovadas != self.horas_calculadas:
            if not (self.observacoes_secretaria or "").strip():
                errors["observacoes_secretaria"] = "Justifique a diferenca entre horas calculadas e aprovadas."
        maximo = self.maximo_aprovavel()
        if (
            maximo is not None
            and self.status == self.Status.ATIVO
            and self.horas_aprovadas is not None
            and self.horas_aprovadas > maximo
            and not self.excepcional_autorizado
        ):
            errors["horas_aprovadas"] = f"O máximo aprovável pelas regras vigentes e {maximo}h."
        if self.excepcional_autorizado and not (self.justificativa_excepcional or "").strip():
            errors["justificativa_excepcional"] = "Justifique a aprovação excepcional acima do limite normativo."
        if errors:
            raise ValidationError(errors)

    def _normalizar_horas_calculadas(self):
        if not self.tipo_atividade_id:
            return
        self.horas_calculadas = (
            (self.quantidade or Decimal("0")) * self.tipo_atividade.horas_por_unidade
        ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    def full_clean(self, *args, **kwargs):
        self._normalizar_horas_calculadas()
        return super().full_clean(*args, **kwargs)

    def save(self, *args, **kwargs):
        self._normalizar_horas_calculadas()
        self.full_clean()
        return super().save(*args, **kwargs)

    def cancelar(self, *, usuario, justificativa: str):
        justificativa = (justificativa or "").strip()
        if not justificativa:
            raise ValidationError("Informe a justificativa do cancelamento.")
        self.status = self.Status.CANCELADO
        self.cancelado_por = usuario
        self.cancelado_em = timezone.now()
        self.justificativa_cancelamento = justificativa
        self.save(update_fields=["status", "cancelado_por", "cancelado_em", "justificativa_cancelamento", "atualizado_em"])

    @classmethod
    def resumo_trajetoria(cls, trajetoria):
        norma = cls.norma_para_trajetoria(trajetoria)
        carga_exigida = norma.carga_horaria_exigida if norma else 45
        lancamentos = cls.ativos().filter(trajetoria=trajetoria)
        computadas = cls._sum_horas(lancamentos)
        pendentes = max(carga_exigida - computadas, 0)
        grupos = []
        if norma:
            for grupo in norma.grupos_limite.all():
                horas = cls._sum_horas(lancamentos.filter(grupo_limite=grupo))
                saldo = None if grupo.limite_maximo is None else max(grupo.limite_maximo - horas, 0)
                grupos.append({"grupo": grupo, "horas": horas, "saldo": saldo})
        return {
            "norma": norma,
            "horas_exigidas": carga_exigida,
            "horas_computadas": computadas,
            "horas_pendentes": pendentes,
            "situacao": "Cumprido" if computadas >= carga_exigida else "Pendente",
            "grupos": grupos,
            "lancamentos": cls.objects.filter(trajetoria=trajetoria).select_related(
                "tipo_atividade",
                "processo_origem",
                "criado_por",
            ),
        }

    @classmethod
    def resumo_aluno(cls, aluno):
        trajetoria = aluno.trajetoria_ativa()
        if trajetoria:
            return cls.resumo_trajetoria(trajetoria)
        return {
            "norma": None,
            "horas_exigidas": 45,
            "horas_computadas": 0,
            "horas_pendentes": 45,
            "situacao": "Pendente",
            "grupos": [],
            "lancamentos": cls.objects.none(),
        }


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
    titulo = models.CharField(max_length=255, blank=True, verbose_name="Título")
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
            errors["docente"] = "Solicitação de banca deve ser criada por docente."
        if self.trajetoria_id and self.aluno_id and self.trajetoria.aluno_id != self.aluno_id:
            errors["trajetoria"] = "A trajetória selecionada não pertence ao aluno informado."
        if self.trajetoria_id and self.docente_id:
            docente_vinculado = self.trajetoria.orientador_id == self.docente_id or self.trajetoria.coorientador_id == self.docente_id
            if not docente_vinculado:
                errors["trajetoria"] = "A trajetória deve estar vinculada ao docente por orientação ou coorientação."
        if self.trajetoria_id and self.trajetoria.status != TrajetoriaAcademica.Status.ATIVA:
            errors["trajetoria"] = "A trajetória deve estar ativa."

        if self.status == self.Status.FINALIZADA:
            campos_obrigatorios = {
                "titulo": self.titulo,
                "resumo": self.resumo,
                "palavras_chave": self.palavras_chave,
                "modalidade_local_link": self.modalidade_local_link,
            }
            for campo, valor in campos_obrigatorios.items():
                if not (valor or "").strip():
                    errors[campo] = "Campo obrigatório para finalizar a solicitação."
            if not self.data_prevista:
                errors["data_prevista"] = "Campo obrigatório para finalizar a solicitação."
            if not self.horario_previsto:
                errors["horario_previsto"] = "Campo obrigatório para finalizar a solicitação."
            if not self.requisitos_cumpridos:
                errors["requisitos_cumpridos"] = "Confirme que o discente cumpre os requisitos."
            if not self.ciencia_recomendacao_mpf:
                errors["ciencia_recomendacao_mpf"] = "Confirme a ciência da recomendação."
            if not self.finalizado_por_id:
                errors["finalizado_por"] = "Informe o usuário responsável pela finalização."
            if not self.finalizado_em:
                errors["finalizado_em"] = "Informe a data de finalização."

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
    instituicao = models.CharField(max_length=255, blank=True, verbose_name="Instituição")
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
                errors["papel"] = "Papel de banca incompatível com o tipo de defesa."
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

    # Os setores oficiais concedem permissoes especificas, por isso seus nomes
    # precisam ter uma unica fonte. Setores e comissoes comuns continuam
    # concedendo somente acesso a propria caixa e as assinaturas destinadas.
    NOME_SECRETARIA = "Secretaria PPGEC"
    NOME_COORDENACAO = "Coordenação PPG"
    NOME_PLENO = "Colegiado PPGEC (Pleno)"

    nome = models.CharField(max_length=120, unique=True)
    descricao = models.CharField(max_length=255, blank=True, verbose_name="Descrição")
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
        status = "ativo" if self.ativo else f"até {self.data_saida:%Y-%m-%d}"
        return f"{self.usuario} em {self.setor} ({status})"


class SolicitacaoAssinatura(models.Model):
    class DestinatarioTipo(models.TextChoices):
        DOCENTE = "DOCENTE", "Docente"
        SETOR = "SETOR", "Setor/Comissao"

    class TipoDocumento(models.TextChoices):
        DOCUMENTO_SEI = "DOCUMENTO_SEI", "Documento SEI"
        BLOCO_SEI = "BLOCO_SEI", "Bloco de assinatura SEI"
        PDF = "PDF", "PDF para assinatura eletronica"

    class Status(models.TextChoices):
        PENDENTE = "PENDENTE", "Pendente"
        ASSINADO = "ASSINADO", "Assinado"

    criado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="solicitacoes_assinatura_criadas",
    )
    destinatario_tipo = models.CharField(max_length=12, choices=DestinatarioTipo.choices)
    docente = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="solicitacoes_assinatura_docente",
        limit_choices_to={"tipo_usuario": User.TipoUsuario.DOCENTE},
    )
    setor = models.ForeignKey(
        Setor,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="solicitacoes_assinatura",
    )
    tipo_documento = models.CharField(max_length=20, choices=TipoDocumento.choices)
    numero_documento_sei = models.CharField(max_length=80, blank=True, verbose_name="Número do documento no SEI")
    numero_bloco_sei = models.CharField(max_length=80, blank=True, verbose_name="Número do bloco de assinatura no SEI")
    documento_pdf = models.FileField(upload_to="assinaturas/originais/%Y/%m/", blank=True)
    documento_assinado_pdf = models.FileField(upload_to="assinaturas/assinados/%Y/%m/", blank=True)
    observacao = models.TextField(blank=True, verbose_name="Observação")
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PENDENTE)
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)
    assinado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="solicitacoes_assinatura_atendidas",
    )
    assinado_em = models.DateTimeField(null=True, blank=True)
    observacao_assinatura = models.TextField(blank=True, verbose_name="Observação da assinatura")

    class Meta:
        ordering = ["-criado_em"]

    def __str__(self) -> str:
        return f"{self.get_tipo_documento_display()} - {self.destinatario_display}"

    @property
    def destinatario_display(self) -> str:
        if self.destinatario_tipo == self.DestinatarioTipo.DOCENTE and self.docente:
            return self.docente.nome
        if self.destinatario_tipo == self.DestinatarioTipo.SETOR and self.setor:
            return self.setor.nome
        return "-"

    @property
    def referencia_documento(self) -> str:
        if self.tipo_documento == self.TipoDocumento.DOCUMENTO_SEI:
            return self.numero_documento_sei
        if self.tipo_documento == self.TipoDocumento.BLOCO_SEI:
            return self.numero_bloco_sei
        if self.documento_pdf:
            return self.documento_pdf.name.rsplit("/", 1)[-1]
        return "-"

    @property
    def is_pdf(self) -> bool:
        return self.tipo_documento == self.TipoDocumento.PDF

    @property
    def is_pendente(self) -> bool:
        return self.status == self.Status.PENDENTE

    def clean(self):
        errors = {}
        if self.destinatario_tipo == self.DestinatarioTipo.DOCENTE:
            if not self.docente_id:
                errors["docente"] = "Selecione o docente requisitado."
            if self.setor_id:
                errors["setor"] = "Não informe setor para solicitação destinada a docente."
        elif self.destinatario_tipo == self.DestinatarioTipo.SETOR:
            if not self.setor_id:
                errors["setor"] = "Selecione o setor ou comissão requisitado."
            if self.docente_id:
                errors["docente"] = "Não informe docente para solicitação destinada a setor."

        if self.tipo_documento == self.TipoDocumento.DOCUMENTO_SEI:
            if not (self.numero_documento_sei or "").strip():
                errors["numero_documento_sei"] = "Informe o número do documento no SEI."
            if self.numero_bloco_sei or self.documento_pdf:
                errors["tipo_documento"] = "Informe apenas uma origem para assinatura."
        elif self.tipo_documento == self.TipoDocumento.BLOCO_SEI:
            if not (self.numero_bloco_sei or "").strip():
                errors["numero_bloco_sei"] = "Informe o número do bloco de assinatura no SEI."
            if self.numero_documento_sei or self.documento_pdf:
                errors["tipo_documento"] = "Informe apenas uma origem para assinatura."
        elif self.tipo_documento == self.TipoDocumento.PDF:
            if not self.documento_pdf:
                errors["documento_pdf"] = "Anexe o PDF para assinatura."
            if self.numero_documento_sei or self.numero_bloco_sei:
                errors["tipo_documento"] = "Informe apenas uma origem para assinatura."

        if self.status == self.Status.ASSINADO:
            if not self.assinado_por_id:
                errors["assinado_por"] = "Informe quem realizou a assinatura."
            if not self.assinado_em:
                errors["assinado_em"] = "Informe a data da assinatura."
            if self.is_pdf and not self.documento_assinado_pdf:
                errors["documento_assinado_pdf"] = "Anexe o PDF assinado."

        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.numero_documento_sei = (self.numero_documento_sei or "").strip()
        self.numero_bloco_sei = (self.numero_bloco_sei or "").strip()
        self.full_clean()
        return super().save(*args, **kwargs)

    def marcar_assinado(self, *, usuario, documento_assinado=None, observacao=""):
        if documento_assinado:
            self.documento_assinado_pdf = documento_assinado
        self.assinado_por = usuario
        self.assinado_em = timezone.now()
        self.observacao_assinatura = (observacao or "").strip()
        self.status = self.Status.ASSINADO
        self.save()


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
        "ESTAGIO_DOCENCIA": 30,
        "HORAS_COMPLEMENTARES": 30,
        "OUTRO": 60,
    }

    class TipoProcesso(models.TextChoices):
        APROVEITAMENTO_DISPENSA_CREDITOS = "APROVEITAMENTO_DISPENSA_CREDITOS", "Aproveitamento de Créditos ou Dispensa de Disciplina"
        DEFESA_MESTRADO = "DEFESA_MESTRADO", "Defesa de Mestrado"
        DEFESA_DOUTORADO = "DEFESA_DOUTORADO", "Defesa de Doutorado"
        QUALIFICACAO_DOUTORADO = "QUALIFICACAO_DOUTORADO", "Qualificação de Doutorado"
        ESTAGIO_DOCENCIA = "ESTAGIO_DOCENCIA", "Estágio docência"
        HORAS_COMPLEMENTARES = "HORAS_COMPLEMENTARES", "Horas complementares"
        TRANCAMENTO_MATRICULA = "TRANCAMENTO_MATRICULA", "Trancamento de Matrícula"
        PRORROGACAO_PRAZO = "PRORROGACAO_PRAZO", "Prorrogação de Prazo"
        REINGRESSO = "REINGRESSO", "Reingresso"
        MUDANCA_ORIENTADOR = "MUDANCA_ORIENTADOR", "Mudança de Orientador(a)"
        OUTRO = "OUTRO", "Outro"

    class StatusProcesso(models.TextChoices):
        EM_ANALISE = "EM_ANALISE", "Em análise"
        AGUARDANDO_DOCUMENTO = "AGUARDANDO_DOCUMENTO", "Aguardando documento"
        AGUARDANDO_CIENCIA = "AGUARDANDO_CIENCIA", "Aguardando ciência"
        EM_DEBATE = "EM_DEBATE", "Em debate"
        FINALIZADO = "FINALIZADO", "Finalizado"

    usuario_criado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="processos_criados",
    )
    aluno_interessado = models.ForeignKey(
        Aluno,
        on_delete=models.PROTECT,
        related_name="processos",
        null=True,
        blank=True,
        help_text="Discente a quem o processo se refere, independentemente de quem realizou a abertura.",
    )
    tipo = models.CharField(max_length=40, choices=TipoProcesso.choices)
    assunto = models.CharField(max_length=255)
    descricao = models.TextField(verbose_name="Descrição")
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
    numero = models.CharField(max_length=20, unique=True, editable=False, blank=True, verbose_name="Número")
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
                {"status": "Status em andamento não pode ter data de finalização."}
            )

    def save(self, *args, **kwargs):
        if self._state.adding and not self.aluno_interessado_id and self.usuario_criado_por_id:
            if Aluno.objects.filter(pk=self.usuario_criado_por_id).exists():
                self.aluno_interessado_id = self.usuario_criado_por_id

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
            raise ValidationError("Não foi possível gerar número único para o processo.")

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
        aluno = self.obter_aluno_interessado()
        if not aluno:
            return None
        trajetoria = (
            TrajetoriaAcademica.objects.filter(
                aluno_id=aluno.id,
                status=TrajetoriaAcademica.Status.ATIVA,
            )
            .select_related("orientador")
            .order_by("-criado_em")
            .first()
        )
        if not trajetoria:
            return None
        return trajetoria.orientador

    def obter_aluno_interessado(self):
        if self.aluno_interessado_id:
            return self.aluno_interessado
        return Aluno.objects.filter(pk=self.usuario_criado_por_id).first()

    def solicitar_ciente_orientador(self, *, solicitado_por, mensagem_solicitacao: str = ""):
        orientador = self.obter_orientador_responsavel()
        if not orientador:
            raise ValidationError("Processo sem orientador definido para solicitar ciente.")

        ciente_registrado = self.manifestacoes.filter(
            tipo=ManifestacaoProcesso.TipoManifestacao.CIENTE_ORIENTADOR,
            status=ManifestacaoProcesso.StatusManifestacao.CIENTE,
        ).exists()
        if ciente_registrado:
            raise ValidationError("O orientador já manifestou ciência neste processo.")

        pendente = self.manifestacoes.filter(
            tipo=ManifestacaoProcesso.TipoManifestacao.CIENTE_ORIENTADOR,
            status=ManifestacaoProcesso.StatusManifestacao.PENDENTE,
        ).exists()
        if pendente:
            raise ValidationError("Já existe solicitação de ciente do orientador pendente.")

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

    def registrar_ciencia_espontanea_orientador(self, *, orientador, mensagem: str = ""):
        orientador_responsavel = self.obter_orientador_responsavel()
        if not orientador_responsavel or orientador.id != orientador_responsavel.id:
            raise ValidationError("Apenas o orientador responsável pode manifestar ciência.")

        if self.manifestacoes.filter(
            tipo=ManifestacaoProcesso.TipoManifestacao.CIENTE_ORIENTADOR,
            status__in=(
                ManifestacaoProcesso.StatusManifestacao.PENDENTE,
                ManifestacaoProcesso.StatusManifestacao.CIENTE,
            ),
        ).exists():
            raise ValidationError("Já existe ciência ou solicitação de ciência neste processo.")

        return ManifestacaoProcesso.objects.create(
            processo=self,
            tipo=ManifestacaoProcesso.TipoManifestacao.CIENTE_ORIENTADOR,
            status=ManifestacaoProcesso.StatusManifestacao.CIENTE,
            responsavel=orientador,
            solicitado_por=orientador,
            mensagem_manifestacao=(mensagem or "").strip(),
            data_manifestacao=timezone.now(),
        )

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
            raise ValidationError("Não e permitido encaminhar processo finalizado.")
        if self.manifestacoes.filter(
            tipo=ManifestacaoProcesso.TipoManifestacao.CIENTE_ORIENTADOR,
            status=ManifestacaoProcesso.StatusManifestacao.PENDENTE,
        ).exists():
            raise ValidationError("Não e permitido encaminhar com ciente do orientador pendente.")

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
            raise ValidationError("Processo já finalizado.")
        termo_finalizacao = (termo_finalizacao or "").strip()
        if not termo_finalizacao:
            raise ValidationError("Informe o termo de finalização do processo.")

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
    titulo = models.CharField(max_length=255, verbose_name="Título")
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

        if getattr(user, "tipo_usuario", None) in User.tipos_com_acesso_servidor():
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
            raise ValidationError("Informe o motivo da remoção do arquivo.")

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
    observacao = models.TextField(blank=True, verbose_name="Observação")
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
        return f"Tramitação {self.processo.numero} -> {self.setor_destino.nome}"


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
            raise ValidationError("Manifestação já concluída.")
        if autor.id != self.responsavel_id:
            raise ValidationError("Apenas o responsável pode se manifestar.")

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
    class TipoComentario(models.TextChoices):
        OBSERVACAO = "OBSERVACAO", "Registrar observação"
        DEBATE = "DEBATE", "Abrir debate"

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
    tipo = models.CharField(
        max_length=20,
        choices=TipoComentario.choices,
        default=TipoComentario.OBSERVACAO,
    )
    texto = models.TextField()
    data_criacao = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-data_criacao"]

    def __str__(self) -> str:
        return f"Comentário em {self.processo.numero}"


class DeliberacaoProcesso(models.Model):
    class Posicao(models.TextChoices):
        FAVORAVEL = "FAVORAVEL", "Favorável"
        CONTRARIA = "CONTRARIA", "Contrário"
        ABSTENCAO = "ABSTENCAO", "Abstenção"

    processo = models.ForeignKey(
        Processo,
        on_delete=models.CASCADE,
        related_name="deliberacoes",
    )
    docente = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="deliberacoes_processos",
    )
    posicao = models.CharField(max_length=20, choices=Posicao.choices)
    data_manifestacao = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-data_manifestacao"]
        constraints = [
            models.UniqueConstraint(
                fields=["processo", "docente"],
                name="unique_deliberacao_docente_processo",
            )
        ]

    def __str__(self) -> str:
        return f"{self.docente} - {self.get_posicao_display()} - {self.processo.numero}"


class Polo(models.Model):
    nome = models.CharField(max_length=120, unique=True)
    descricao = models.CharField(max_length=255, blank=True, verbose_name="Descrição")
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
        SABADO = 5, "Sábado"
        DOMINGO = 6, "Domingo"

    sala = models.ForeignKey(Sala, on_delete=models.CASCADE, related_name="disponibilidades")
    dia_semana = models.PositiveSmallIntegerField(choices=DiaSemana.choices)
    hora_inicio = models.TimeField(verbose_name="Hora de início")
    hora_fim = models.TimeField()

    class Meta:
        ordering = ["sala", "dia_semana", "hora_inicio"]

    def __str__(self) -> str:
        return f"{self.sala} - {self.get_dia_semana_display()} {self.hora_inicio:%H:%M}-{self.hora_fim:%H:%M}"

    def clean(self):
        if self.hora_fim <= self.hora_inicio:
            raise ValidationError({"hora_fim": "O horário final deve ser posterior ao horário inicial."})

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
    titulo = models.CharField(max_length=255, blank=True, verbose_name="Título")
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
            errors["fim"] = "O término deve ser posterior ao inicio."
        elif self.inicio.date() != self.fim.date():
            errors["fim"] = "A reserva deve comecar e terminar no mesmo dia."
        if self.sala_id and self.inicio and self.fim:
            if self.status == self.StatusReserva.ATIVA and not self.horario_disponivel_na_sala():
                errors["inicio"] = "A sala não está disponível neste horário."
            conflito = self.reserva_conflitante() if self.status == self.StatusReserva.ATIVA else None
            if conflito:
                errors["inicio"] = self.mensagem_conflito(conflito)
        if self.status == self.StatusReserva.EXCLUIDA and not (self.justificativa_exclusao or "").strip():
            errors["justificativa_exclusao"] = "Informe a justificativa da exclusão."
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
                raise ValidationError("A recorrência não pode ser superior a 6 meses.")
            if duracao_recorrencia_meses < 1:
                raise ValidationError("A duração da recorrência deve ser de pelo menos 1 mês.")
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
        raise ValidationError("Recorrência inválida.")

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


class LoginThrottle(models.Model):
    """Contador persistente de falhas de login, sem armazenar e-mail ou IP."""

    scope = models.CharField(max_length=16)
    fingerprint = models.CharField(max_length=64)
    failure_count = models.PositiveIntegerField(default=0)
    window_started_at = models.DateTimeField()
    locked_until = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["scope", "fingerprint"],
                name="processos_login_throttle_scope_fingerprint_uniq",
            )
        ]


class EstagioDocencia(models.Model):

    class Status(models.TextChoices):
        NAO_INICIADO = "NAO_INICIADO", "Não Iniciado"
        AGUARD_CIENCIA = "AGUARD_CIENCIA", "Aguardando Ciente do Orientador"
        AGUARD_ASSINATURA = "AGUARD_ASSINATURA", "Aguardando Aprovação da Coordenação"
        ANALISE_DISP = "ANALISE_DISP", "Em Análise de Dispensa"
        DISPENSADO = "DISPENSADO", "Dispensado"
        EM_ANDAMENTO = "EM_ANDAMENTO", "Em Andamento"
        AGUARD_RELAT = "AGUARD_RELAT", "Aguardando Relatório"
        CONCLUIDO = "CONCLUIDO", "Concluído"

    # --- 2. AS PONTES DE LIGAÇÃO E DADOS ---
    trajetoria = models.ForeignKey(
        "TrajetoriaAcademica", 
        on_delete=models.CASCADE, 
        related_name="estagios_docencia"
    )
    
    supervisor = models.CharField(
        max_length=255,
        blank=True,
        verbose_name="Nome do Supervisor"
    ) 
    
    processo_vinculado = models.OneToOneField(
        "Processo", 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True,
        related_name="estagio_gerado"
    )

    # --- 3. DADOS DE CONTROLE ---
    status = models.CharField(
        max_length=25, # Aumentado para 25 para acomodar perfeitamente o AGUARD_ASSINATURA
        choices=Status.choices, 
        default=Status.NAO_INICIADO
    )
    
    # --- 4. DATAS ---
    inicio = models.DateField(null=True, blank=True)
    termino = models.DateField(null=True, blank=True)
    
    class Meta:
        ordering = ["id"]
        verbose_name = "Estágio de Docência"
        verbose_name_plural = "Estágios de Docência"
        
    def __str__(self) -> str:
        # Traz o nome do aluno puxando a ponte da Trajetória e exibe o status legível
        return f"{self.trajetoria.aluno.nome} - Status: {self.get_status_display()}"
    
    @property
    def relatorio_pendente_ou_proximo(self) -> bool:
        """
        Retorna True se o estágio está em andamento e 
        falta 15 dias (ou menos) para a data de término (ou se já passou).
        """
        if self.status == self.Status.EM_ANDAMENTO and self.termino:
            hoje = timezone.localdate()
            # Calcula a diferença de dias entre o término e hoje
            dias_restantes = (self.termino - hoje).days
            return dias_restantes <= 30
            
        return False


def caminho_da_declaracao_de_vinculo(instance, filename):
    """Nome opaco, sob o prefixo das declaracoes.

    O arquivo de origem chega nomeado pelo CPF -- e assim que a secretaria casa
    cada PDF com o aluno certo na importacao. O CPF nao segue para dentro do
    bucket: a chave do objeto viaja na URL assinada, nos logs de acesso do S3 e
    no historico do navegador, e com CPF obrigatorio para todo aluno, listar o
    bucket devolveria a relacao completa deles.

    A data no caminho e a da gravacao, e existe para manter cada prefixo
    pequeno. O semestre a que a declaracao se refere e outra coisa -- uma
    declaracao de 2026.2 pode ser reemitida em janeiro de 2027 -- e vive na
    coluna periodo.
    """
    extensao = (filename or "").rsplit(".", 1)
    extensao = f".{extensao[-1].lower()}" if len(extensao) == 2 else ".pdf"
    agora = timezone.now()
    return f"documentos/vinculo/{agora:%Y/%m}/{uuid.uuid4().hex}{extensao}"


class DeclaracaoDeVinculo(models.Model):
    """Comprovante de vinculo de um aluno, valido por um semestre letivo.

    Hoje o documento e emitido a mao pela secretaria e trazido para o sistema
    em lote. Quando a emissao automatica existir, o documento gerado entra
    aqui, com enviado_por vazio: duas fontes para a mesma declaracao obrigariam
    a tela a escolher entre elas.
    """

    aluno = models.ForeignKey(
        Aluno,
        on_delete=models.CASCADE,
        related_name="declaracoes_de_vinculo",
        verbose_name="Aluno",
    )
    periodo = models.ForeignKey(
        PeriodoLetivo,
        on_delete=models.PROTECT,
        related_name="declaracoes_de_vinculo",
        verbose_name="Período letivo",
    )
    arquivo = models.FileField(upload_to=caminho_da_declaracao_de_vinculo, verbose_name="Arquivo")
    # Vazio quando o documento vier da emissao automatica, que ainda sera
    # construida: ali nao ha pessoa que enviou.
    enviado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="declaracoes_de_vinculo_enviadas",
        verbose_name="Enviado por",
    )
    enviado_em = models.DateTimeField(auto_now_add=True, verbose_name="Enviado em")

    class Meta:
        verbose_name = "Declaração de vínculo"
        verbose_name_plural = "Declarações de vínculo"
        ordering = ["-periodo__nome", "aluno__nome"]
        constraints = [
            # E esta restricao que da sentido a "a declaracao vigente". Sem ela,
            # dois envios do mesmo semestre criariam duas declaracoes validas e a
            # tela teria de desempatar por criterio arbitrario -- a mais recente,
            # a de maior id --, sem que ninguem tivesse decidido isso. Com ela,
            # reenviar e uma decisao explicita: substituir a que existe.
            models.UniqueConstraint(
                fields=["aluno", "periodo"],
                name="declaracao_vinculo_unica_por_periodo",
            ),
        ]

    def __str__(self) -> str:
        return f"Declaração de vínculo {self.periodo.nome} — {self.aluno.nome}"

    @classmethod
    def aluno_tem_vinculo_no_periodo(cls, aluno_id, periodo_id) -> bool:
        """Basta ter solicitado matricula no periodo, em qualquer estado.

        O status da solicitacao nao entra na conta de proposito. Os estados sao
        legado: nao foram mantidos de forma confiavel ao longo do tempo, e
        filtrar por eles negaria a declaracao a alunos que cursaram o semestre
        -- justamente quem precisa comprovar o vinculo.

        O que se afirma aqui e mais modesto e mais seguro: houve pedido de
        matricula naquele periodo. Se um dia os estados voltarem a ser
        confiaveis, este e o unico lugar a mudar.
        """
        return SolicitacaoMatricula.objects.filter(
            aluno_id=aluno_id,
            periodo_id=periodo_id,
        ).exists()

    def pode_visualizar(self, user) -> bool:
        """O aluno que solicitou matricula naquele semestre, e a gestao.

        A condicao e do semestre da declaracao, e nao do semestre em curso: a de
        2026.1 exige solicitacao de matricula em 2026.1. Cada documento carrega a sua
        propria condicao, entao o historico nao se apaga quando o semestre vira
        -- um recem-formado continua podendo comprovar os semestres que cursou.

        A gestao ve tudo: e ela que emite, e precisa conferir o que enviou antes
        de o aluno alcancar.
        """
        if not self.arquivo:
            return False
        if not user or not user.is_authenticated:
            return False
        if user.id == self.aluno_id:
            return self.aluno_tem_vinculo_no_periodo(self.aluno_id, self.periodo_id)
        if getattr(user, "tipo_usuario", None) in User.tipos_com_acesso_servidor():
            return True
        if getattr(user, "tipo_usuario", None) == User.TipoUsuario.DOCENTE:
            try:
                return bool(user.docente.coordenador)
            except Docente.DoesNotExist:
                return False
        return False
