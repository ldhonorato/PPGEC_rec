# Generated manually for the matrícula domain.

import django.core.validators
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("processos", "0035_reservaambiente_excluida_em_and_more"),
    ]

    operations = [
        migrations.CreateModel(
            name="Disciplina",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("codigo", models.CharField(max_length=40, unique=True)),
                ("nome", models.CharField(max_length=255)),
                ("creditos", models.PositiveSmallIntegerField(blank=True, null=True)),
                ("carga_horaria", models.PositiveSmallIntegerField(blank=True, null=True)),
                ("ativa", models.BooleanField(default=True)),
                ("criado_em", models.DateTimeField(auto_now_add=True)),
                ("atualizado_em", models.DateTimeField(auto_now=True)),
            ],
            options={"ordering": ["codigo", "nome"]},
        ),
        migrations.CreateModel(
            name="PeriodoLetivo",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "nome",
                    models.CharField(
                        max_length=20,
                        unique=True,
                        validators=[
                            django.core.validators.RegexValidator(
                                message="Informe no formato YYYY.1 ou YYYY.2.",
                                regex="^\\d{4}\\.[12]$",
                            )
                        ],
                    ),
                ),
                ("prazo_cadastro_disciplinas", models.DateField()),
                ("matricula_inicio", models.DateField()),
                ("matricula_fim", models.DateField()),
                ("modificacao_inicio", models.DateField()),
                ("modificacao_fim", models.DateField()),
                ("encerrado_manualmente_em", models.DateTimeField(blank=True, null=True)),
                ("criado_em", models.DateTimeField(auto_now_add=True)),
                ("atualizado_em", models.DateTimeField(auto_now=True)),
                (
                    "criado_por",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="periodos_letivos_criados",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "encerrado_manualmente_por",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="periodos_letivos_encerrados",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={"ordering": ["-nome"]},
        ),
        migrations.CreateModel(
            name="OfertaDisciplina",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "modalidade",
                    models.CharField(
                        choices=[("PRESENCIAL", "Presencial"), ("HIBRIDA", "Híbrida")],
                        default="PRESENCIAL",
                        max_length=12,
                    ),
                ),
                ("vagas_regulares", models.PositiveSmallIntegerField(default=0)),
                ("vagas_especiais", models.PositiveSmallIntegerField(default=0)),
                ("ativa", models.BooleanField(default=True)),
                ("criado_em", models.DateTimeField(auto_now_add=True)),
                ("atualizado_em", models.DateTimeField(auto_now=True)),
                (
                    "criada_por",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="ofertas_disciplinas_criadas",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "disciplina",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="ofertas",
                        to="processos.disciplina",
                    ),
                ),
                (
                    "docente_responsavel",
                    models.ForeignKey(
                        limit_choices_to={"tipo_usuario": "DOCENTE"},
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="ofertas_disciplinas",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "periodo",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="ofertas",
                        to="processos.periodoletivo",
                    ),
                ),
            ],
            options={"ordering": ["periodo__nome", "disciplina__nome"]},
        ),
        migrations.CreateModel(
            name="SolicitacaoMatricula",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "tipo_aluno",
                    models.CharField(
                        choices=[("REGULAR", "Regular"), ("ESPECIAL", "Especial")],
                        default="REGULAR",
                        max_length=10,
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("RASCUNHO", "Rascunho"),
                            ("SOLICITADA", "Solicitada"),
                            ("PARCIALMENTE_HOMOLOGADA", "Parcialmente homologada"),
                            ("HOMOLOGADA", "Homologada"),
                            ("INDEFERIDA", "Indeferida"),
                            ("CANCELADA", "Cancelada"),
                        ],
                        default="RASCUNHO",
                        max_length=25,
                    ),
                ),
                ("observacao_aluno", models.TextField(blank=True)),
                ("observacao_secretaria", models.TextField(blank=True)),
                ("solicitada_em", models.DateTimeField(blank=True, null=True)),
                ("homologada_em", models.DateTimeField(blank=True, null=True)),
                ("criado_em", models.DateTimeField(auto_now_add=True)),
                ("atualizado_em", models.DateTimeField(auto_now=True)),
                (
                    "aluno",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="solicitacoes_matricula",
                        to="processos.aluno",
                    ),
                ),
                (
                    "homologada_por",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="solicitacoes_matricula_homologadas",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "periodo",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="solicitacoes_matricula",
                        to="processos.periodoletivo",
                    ),
                ),
            ],
            options={"ordering": ["-criado_em"]},
        ),
        migrations.CreateModel(
            name="EncontroOferta",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "dia_semana",
                    models.PositiveSmallIntegerField(
                        choices=[
                            (0, "Segunda-feira"),
                            (1, "Terça-feira"),
                            (2, "Quarta-feira"),
                            (3, "Quinta-feira"),
                            (4, "Sexta-feira"),
                            (5, "Sábado"),
                            (6, "Domingo"),
                        ]
                    ),
                ),
                ("hora_inicio", models.TimeField()),
                ("hora_fim", models.TimeField()),
                (
                    "oferta",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="encontros",
                        to="processos.ofertadisciplina",
                    ),
                ),
            ],
            options={"ordering": ["oferta", "dia_semana", "hora_inicio"]},
        ),
        migrations.CreateModel(
            name="ItemSolicitacaoMatricula",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("SOLICITADO", "Solicitado"),
                            ("HOMOLOGADO", "Homologado"),
                            ("EM_LISTA_ESPERA", "Em lista de espera"),
                            ("INDEFERIDO", "Indeferido"),
                            ("CANCELADO", "Cancelado"),
                        ],
                        default="SOLICITADO",
                        max_length=20,
                    ),
                ),
                ("solicitado_em", models.DateTimeField(auto_now_add=True)),
                ("homologado_em", models.DateTimeField(blank=True, null=True)),
                ("indeferido_em", models.DateTimeField(blank=True, null=True)),
                ("motivo_indeferimento", models.TextField(blank=True)),
                ("atualizado_em", models.DateTimeField(auto_now=True)),
                (
                    "homologado_por",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="itens_matricula_homologados",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "indeferido_por",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="itens_matricula_indeferidos",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "oferta",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="itens_matricula",
                        to="processos.ofertadisciplina",
                    ),
                ),
                (
                    "solicitacao",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="itens",
                        to="processos.solicitacaomatricula",
                    ),
                ),
            ],
            options={"ordering": ["solicitado_em", "id"]},
        ),
        migrations.AddConstraint(
            model_name="ofertadisciplina",
            constraint=models.UniqueConstraint(fields=("periodo", "disciplina"), name="unique_oferta_disciplina_periodo"),
        ),
        migrations.AddConstraint(
            model_name="solicitacaomatricula",
            constraint=models.UniqueConstraint(fields=("periodo", "aluno"), name="unique_solicitacao_matricula_aluno_periodo"),
        ),
        migrations.AddConstraint(
            model_name="itemsolicitacaomatricula",
            constraint=models.UniqueConstraint(fields=("solicitacao", "oferta"), name="unique_item_matricula_por_oferta"),
        ),
    ]
