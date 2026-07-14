import django.db.models.deletion
from django.db import migrations, models


def preencher_horarios_aulas(apps, schema_editor):
    AulaPresencialOferta = apps.get_model("processos", "AulaPresencialOferta")
    for aula in AulaPresencialOferta.objects.select_related("encontro"):
        if aula.encontro_id:
            aula.hora_inicio = aula.encontro.hora_inicio
            aula.hora_fim = aula.encontro.hora_fim
            aula.save(update_fields=["hora_inicio", "hora_fim"])


class Migration(migrations.Migration):

    dependencies = [
        ("processos", "0047_seed_disciplinas_ppgec"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="aulapresencialoferta",
            name="unique_aula_presencial_oferta_data_encontro",
        ),
        migrations.AddField(
            model_name="aulapresencialoferta",
            name="hora_inicio",
            field=models.TimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="aulapresencialoferta",
            name="hora_fim",
            field=models.TimeField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name="aulapresencialoferta",
            name="encontro",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="aulas_presenciais",
                to="processos.encontrooferta",
            ),
        ),
        migrations.RunPython(preencher_horarios_aulas, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="aulapresencialoferta",
            name="hora_inicio",
            field=models.TimeField(),
        ),
        migrations.AlterField(
            model_name="aulapresencialoferta",
            name="hora_fim",
            field=models.TimeField(),
        ),
        migrations.AlterModelOptions(
            name="aulapresencialoferta",
            options={"ordering": ["data", "hora_inicio"]},
        ),
        migrations.AddConstraint(
            model_name="aulapresencialoferta",
            constraint=models.UniqueConstraint(
                fields=("oferta", "data", "hora_inicio", "hora_fim"),
                name="unique_aula_presencial_oferta_data_horario",
            ),
        ),
    ]
