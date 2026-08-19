from django.db import migrations, models
import django.db.models.deletion


def preencher_aluno_interessado(apps, schema_editor):
    Aluno = apps.get_model("processos", "Aluno")
    Processo = apps.get_model("processos", "Processo")
    ids_alunos = Aluno.objects.values_list("pk", flat=True)
    for processo in Processo.objects.filter(usuario_criado_por_id__in=ids_alunos).iterator():
        processo.aluno_interessado_id = processo.usuario_criado_por_id
        processo.save(update_fields=["aluno_interessado"])


class Migration(migrations.Migration):
    dependencies = [
        ("processos", "0073_preenche_prazo_qualificacao_doutorado"),
    ]

    operations = [
        migrations.AddField(
            model_name="processo",
            name="aluno_interessado",
            field=models.ForeignKey(
                blank=True,
                help_text="Discente a quem o processo se refere, independentemente de quem realizou a abertura.",
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="processos",
                to="processos.aluno",
            ),
        ),
        migrations.RunPython(preencher_aluno_interessado, migrations.RunPython.noop),
    ]
