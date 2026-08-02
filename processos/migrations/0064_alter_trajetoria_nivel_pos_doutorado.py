from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("processos", "0063_corrige_nome_colegiado"),
    ]

    operations = [
        migrations.AlterField(
            model_name="normahorascomplementares",
            name="nivel_curso",
            field=models.CharField(
                choices=[
                    ("MESTRADO", "Mestrado"),
                    ("DOUTORADO", "Doutorado"),
                    ("POSDOUTORADO", "Pós-Doutorado"),
                    ("ALUNO_ESPECIAL", "Aluno especial"),
                ],
                max_length=20,
            ),
        ),
        migrations.AlterField(
            model_name="trajetoriaacademica",
            name="nivel_curso",
            field=models.CharField(
                choices=[
                    ("MESTRADO", "Mestrado"),
                    ("DOUTORADO", "Doutorado"),
                    ("POSDOUTORADO", "Pós-Doutorado"),
                    ("ALUNO_ESPECIAL", "Aluno especial"),
                ],
                max_length=20,
            ),
        ),
    ]
