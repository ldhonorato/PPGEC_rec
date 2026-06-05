from django.db import migrations


def criar_polo_sede_e_vincular_usuarios(apps, schema_editor):
    Polo = apps.get_model("processos", "Polo")
    User = apps.get_model("processos", "User")

    polo, _ = Polo.objects.get_or_create(
        nome="Polo Sede",
        defaults={
            "descricao": "Polo padrao para usuarios cadastrados antes do modulo de reservas.",
            "ativo": True,
        },
    )
    User.objects.filter(polo_atuacao__isnull=True).update(polo_atuacao=polo)


class Migration(migrations.Migration):

    dependencies = [
        ("processos", "0028_polo_user_polo_atuacao_sala_reservaambiente_and_more"),
    ]

    operations = [
        migrations.RunPython(criar_polo_sede_e_vincular_usuarios, migrations.RunPython.noop),
    ]
