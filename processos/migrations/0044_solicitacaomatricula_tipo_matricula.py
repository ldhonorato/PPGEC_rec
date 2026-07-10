from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("processos", "0043_merge_matriculas_status"),
    ]

    operations = [
        migrations.AddField(
            model_name="solicitacaomatricula",
            name="tipo_matricula",
            field=models.CharField(
                choices=[
                    ("DISCIPLINAS", "Disciplinas"),
                    ("VINCULO", "Matrícula vínculo"),
                ],
                default="DISCIPLINAS",
                max_length=12,
            ),
        ),
    ]
