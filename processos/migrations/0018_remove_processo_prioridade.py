from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("processos", "0017_processo_prazo_limite"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="processo",
            name="prioridade",
        ),
    ]
