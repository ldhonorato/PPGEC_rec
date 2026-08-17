import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models
from django.utils import timezone


def preencher_fase_inclusao(apps, schema_editor):
    Item = apps.get_model("processos", "ItemSolicitacaoMatricula")
    for item in Item.objects.select_related("solicitacao__periodo").iterator():
        periodo = item.solicitacao.periodo
        data_solicitacao = timezone.localtime(item.solicitado_em).date() if item.solicitado_em else None
        if data_solicitacao and data_solicitacao >= periodo.modificacao_inicio:
            item.incluido_na_fase = "MODIFICACAO"
            item.save(update_fields=["incluido_na_fase"])


class Migration(migrations.Migration):
    dependencies = [
        ("processos", "0069_alter_user_tipo_usuario"),
    ]

    operations = [
        migrations.AddField(
            model_name="itemsolicitacaomatricula",
            name="incluido_na_fase",
            field=models.CharField(
                choices=[
                    ("MATRICULA", "Matrícula"),
                    ("MODIFICACAO", "Modificação de matrícula"),
                ],
                default="MATRICULA",
                max_length=12,
            ),
        ),
        migrations.CreateModel(
            name="AlteracaoMatricula",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("acao", models.CharField(choices=[
                    ("SOLICITACAO_CRIADA", "Solicitação criada"),
                    ("MATRICULA_VINCULO_SOLICITADA", "Matrícula vínculo solicitada"),
                    ("MATRICULA_VINCULO_INDEFERIDA", "Matrícula vínculo indeferida"),
                    ("TIPO_MATRICULA_ALTERADO", "Tipo de matrícula alterado"),
                    ("DISCIPLINA_INCLUIDA", "Disciplina incluída"),
                    ("DISCIPLINA_REINCLUIDA", "Disciplina reincluída"),
                    ("DISCIPLINA_CANCELADA", "Disciplina cancelada"),
                    ("DISCIPLINA_INDEFERIDA", "Disciplina indeferida"),
                    ("LISTA_ESPERA_PROMOVIDA", "Promoção da lista de espera"),
                ], max_length=35)),
                ("fase", models.CharField(choices=[
                    ("MATRICULA", "Matrícula"),
                    ("MODIFICACAO", "Modificação de matrícula"),
                    ("ADMINISTRATIVA", "Ação administrativa"),
                ], max_length=15)),
                ("estado_anterior", models.JSONField(blank=True, default=dict)),
                ("estado_novo", models.JSONField(blank=True, default=dict)),
                ("justificativa", models.TextField(blank=True)),
                ("criado_em", models.DateTimeField(auto_now_add=True)),
                ("item", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="alteracoes", to="processos.itemsolicitacaomatricula")),
                ("oferta", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="alteracoes_matricula", to="processos.ofertadisciplina")),
                ("realizado_por", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="alteracoes_matricula_realizadas", to=settings.AUTH_USER_MODEL)),
                ("solicitacao", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="alteracoes", to="processos.solicitacaomatricula")),
            ],
            options={"ordering": ["criado_em", "id"]},
        ),
        migrations.AddIndex(
            model_name="alteracaomatricula",
            index=models.Index(fields=["solicitacao", "fase", "criado_em"], name="processos_a_solicit_d7d70b_idx"),
        ),
        migrations.AddIndex(
            model_name="alteracaomatricula",
            index=models.Index(fields=["oferta", "fase", "criado_em"], name="processos_a_oferta__39415f_idx"),
        ),
        migrations.RunPython(preencher_fase_inclusao, migrations.RunPython.noop),
    ]
