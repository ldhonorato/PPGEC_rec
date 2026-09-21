from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("processos", "0077_remove_coluna_obsoleta_progresso_tangivel"),
    ]

    operations = [
        migrations.AlterField(
            model_name="documento",
            name="arquivo",
            field=models.FileField(
                blank=True,
                max_length=255,
                null=True,
                upload_to="documentos/processos/",
            ),
        ),
    ]
