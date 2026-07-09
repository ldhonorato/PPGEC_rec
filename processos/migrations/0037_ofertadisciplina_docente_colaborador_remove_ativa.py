# Generated manually after 0036 had already been applied in development.

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("processos", "0036_matriculas"),
    ]

    operations = [
        migrations.AddField(
            model_name="ofertadisciplina",
            name="docente_colaborador",
            field=models.ForeignKey(
                blank=True,
                limit_choices_to={"tipo_usuario": "DOCENTE"},
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="ofertas_disciplinas_colaboracao",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.RemoveField(
            model_name="ofertadisciplina",
            name="ativa",
        ),
    ]
