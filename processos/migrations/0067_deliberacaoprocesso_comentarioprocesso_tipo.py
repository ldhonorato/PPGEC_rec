import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("processos", "0066_rotulos_com_acento"),
    ]

    operations = [
        migrations.AddField(
            model_name="comentarioprocesso",
            name="tipo",
            field=models.CharField(
                choices=[("OBSERVACAO", "Registrar observação"), ("DEBATE", "Abrir debate")],
                default="OBSERVACAO",
                max_length=20,
            ),
        ),
        migrations.CreateModel(
            name="DeliberacaoProcesso",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("posicao", models.CharField(choices=[("FAVORAVEL", "Favorável"), ("CONTRARIA", "Contrário"), ("ABSTENCAO", "Abstenção")], max_length=20)),
                ("data_manifestacao", models.DateTimeField(auto_now=True)),
                ("docente", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="deliberacoes_processos", to=settings.AUTH_USER_MODEL)),
                ("processo", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="deliberacoes", to="processos.processo")),
            ],
            options={"ordering": ["-data_manifestacao"]},
        ),
        migrations.AddConstraint(
            model_name="deliberacaoprocesso",
            constraint=models.UniqueConstraint(fields=("processo", "docente"), name="unique_deliberacao_docente_processo"),
        ),
    ]
