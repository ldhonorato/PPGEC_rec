from django.db import migrations, models


POLOS_CADASTRO_ALUNO = (
    "POLI",
    "Caruaru",
    "Garanhuns",
    "Petrolina",
    "Fitec/SP",
)


def criar_polos_cadastro_aluno(apps, schema_editor):
    Polo = apps.get_model("processos", "Polo")
    for nome in POLOS_CADASTRO_ALUNO:
        Polo.objects.update_or_create(
            nome=nome,
            defaults={
                "ativo": True,
            },
        )


class Migration(migrations.Migration):

    dependencies = [
        ("processos", "0050_lancamento_horas_trajetoria"),
    ]

    operations = [
        migrations.AddField(
            model_name="aluno",
            name="sexo_atribuido_nascimento",
            field=models.CharField(
                blank=True,
                choices=[
                    ("FEMININO", "Feminino"),
                    ("MASCULINO", "Masculino"),
                    ("NAO_INFORMAR", "Prefiro nao informar"),
                ],
                max_length=15,
            ),
        ),
        migrations.RunPython(criar_polos_cadastro_aluno, migrations.RunPython.noop),
    ]
