from django.core.exceptions import ValidationError
from django.db import models
from django.conf import settings

from processos.models import Aluno, TrajetoriaAcademica


class MarcoCalendario(models.Model):
    """Data da sessão de um marco acadêmico em um semestre.

    A data da sessão de projeto de dissertação e de qualificação muda a cada
    semestre. Este modelo só guarda o dado de entrada; a situação de cada
    aluno é calculada a partir dele e não é gravada aqui.
    """

    class TipoMarco(models.TextChoices):
        PROJETO_DISSERTACAO = "PROJETO_DISSERTACAO", "Projeto de dissertação"
        QUALIFICACAO = "QUALIFICACAO", "Qualificação"

    semestre = models.CharField(
        max_length=6,
        validators=[Aluno.semestre_validator],
        help_text="Semestre em que a sessão acontece, no formato YYYY.1 ou YYYY.2.",
    )
    tipo = models.CharField(max_length=20, choices=TipoMarco.choices)
    data_limite = models.DateField(
        verbose_name="Data limite",
        help_text="Data da sessão. Quem não apresentar até ela é tratado como reprovado.",
    )
    alterado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        editable=False,
        related_name="+",
        verbose_name="Alterado por",
)
    alterado_em = models.DateTimeField(auto_now=True, verbose_name="Alterado em")

    class Meta:
        verbose_name = "marco do calendário"
        verbose_name_plural = "calendário de marcos"
        ordering = ["-semestre", "tipo"]
        constraints = [
            models.UniqueConstraint(
                fields=["semestre", "tipo"],
                name="marco_calendario_semestre_tipo_unico",
            )
        ]

    def __str__(self) -> str:
        return f"{self.get_tipo_display()} {self.semestre}: {self.data_limite:%d/%m/%Y}"

    @staticmethod
    def semestre_da_data(data) -> str:
        """Semestre letivo de uma data: janeiro a junho é .1, o resto é .2."""
        return f"{data.year}.{'1' if data.month <= 6 else '2'}"

    @classmethod
    def data_limite_do(cls, semestre: str, tipo: str):
        """Data cadastrada para o marco no semestre, ou None se não houver.

        O None significa "sem prazo cadastrado": quem consulta não deve tratar
        a ausência como prazo em aberto nem como prazo vencido.
        """
        return (
            cls.objects.filter(semestre=semestre, tipo=tipo)
            .values_list("data_limite", flat=True)
            .first()
        )

    def clean(self):
        super().clean()
        if self.semestre and self.data_limite:
            if self.semestre_da_data(self.data_limite) != self.semestre:
                raise ValidationError(
                    {"data_limite": "A data limite deve estar dentro do semestre informado."}
                )


class AvisoIrregularidade(models.Model):
    """Aviso de que um aluno ficou atrasado num marco.

    O aviso fica aberto enquanto o aluno continua na mesma situação. Quando
    ele sai do atraso, o aviso é encerrado; se voltar a atrasar, nasce um
    aviso novo.
    """

    class Marco(models.TextChoices):
        PROJETO_QUALIFICACAO = "marco", "Projeto de dissertação ou qualificação"
        DEFESA = "defesa", "Defesa"

    class Situacao(models.TextChoices):
        COM_CHANCE = "irregular_com_chance", "Atrasado com chance"
        SEM_CHANCE = "irregular_sem_chance", "Atrasado sem chance"

    trajetoria = models.ForeignKey(
        TrajetoriaAcademica,
        on_delete=models.CASCADE,
        related_name="avisos_irregularidade",
    )
    marco = models.CharField(max_length=10, choices=Marco.choices)
    situacao = models.CharField(max_length=25, choices=Situacao.choices, verbose_name="Situação")
    data_limite = models.DateField(null=True, blank=True, verbose_name="Data limite")
    prazo_reapresentacao = models.DateField(
        null=True, blank=True, verbose_name="Prazo de reapresentação"
    )
    motivo = models.TextField(blank=True)
    criado_em = models.DateTimeField(auto_now_add=True, verbose_name="Criado em")
    enviado_em = models.DateTimeField(null=True, blank=True, verbose_name="Enviado em")
    tentativas_envio = models.PositiveSmallIntegerField(
        default=0, verbose_name="Tentativas de envio"
    )
    motivo_pendencia = models.TextField(blank=True, verbose_name="Motivo da pendência")
    encerrado_em = models.DateTimeField(null=True, blank=True, verbose_name="Encerrado em")

    class Meta:
        verbose_name = "aviso de irregularidade"
        verbose_name_plural = "avisos de irregularidade"
        ordering = ["-criado_em"]
        constraints = [
            # Só um aviso aberto por aluno, marco e situação.
            models.UniqueConstraint(
                fields=["trajetoria", "marco", "situacao"],
                condition=models.Q(encerrado_em__isnull=True),
                name="aviso_irregularidade_aberto_unico",
            )
        ]

    def __str__(self) -> str:
        return f"{self.trajetoria.aluno} - {self.get_marco_display()}: {self.get_situacao_display()}"


class PlanilhaBaixada(models.Model):
    baixada_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="planilhas_acompanhamento_baixadas",
        verbose_name="Baixada por",
    )
    baixada_em = models.DateTimeField(auto_now_add=True, verbose_name="Baixada em")
    filtros = models.JSONField(default=dict, verbose_name="Filtros")

    class Meta:
        verbose_name = "planilha baixada"
        verbose_name_plural = "planilhas baixadas"
        ordering = ["-baixada_em"]

    def __str__(self) -> str:
        return f"Planilha baixada por {self.baixada_por} em {self.baixada_em:%d/%m/%Y %H:%M}"


class ExecucaoAviso(models.Model):
    """Registro de uma execução da rotina do aviso diário.

    Serve para a equipe ver que a rotina está rodando: se o Celery parar,
    a data da última execução deixa de avançar.
    """

    executada_em = models.DateTimeField(auto_now_add=True, verbose_name="Rodou em")
    avisos_novos = models.PositiveIntegerField(default=0, verbose_name="Avisos novos")
    emails_enviados = models.PositiveIntegerField(default=0, verbose_name="E-mails enviados")
    avisos_pendentes = models.PositiveIntegerField(default=0, verbose_name="Avisos pendentes")

    class Meta:
        verbose_name = "execução do aviso"
        verbose_name_plural = "execuções do aviso"
        ordering = ["-executada_em"]

    def __str__(self) -> str:
        return f"Aviso rodou em {self.executada_em:%d/%m/%Y %H:%M}"
