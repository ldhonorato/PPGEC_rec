from datetime import timedelta

from django.db import migrations, models
from django.utils import timezone


PRAZOS_DIAS_POR_TIPO = {
    "APROVEITAMENTO_CREDITOS": 30,
    "DISPENSA_DISCIPLINA": 30,
    "TRANCAMENTO_MATRICULA": 15,
    "PRORROGACAO_PRAZO": 20,
    "REINGRESSO": 30,
    "MUDANCA_ORIENTADOR": 20,
    "QUALIFICACAO": 30,
    "DEFESA": 45,
    "RECURSO": 15,
    "OUTRO": 30,
}


def popular_prazo_limite(apps, schema_editor):
    Processo = apps.get_model("processos", "Processo")
    hoje = timezone.localdate()
    for processo in Processo.objects.all().iterator():
        data_base = processo.data_criacao.date() if processo.data_criacao else hoje
        dias = PRAZOS_DIAS_POR_TIPO.get(processo.tipo, 30)
        processo.prazo_limite = data_base + timedelta(days=dias)
        processo.save(update_fields=["prazo_limite"])


class Migration(migrations.Migration):

    dependencies = [
        ("processos", "0016_aluno_data_defesa_aluno_deposito_versao_final_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="processo",
            name="prazo_limite",
            field=models.DateField(blank=True, null=True),
        ),
        migrations.RunPython(popular_prazo_limite, migrations.RunPython.noop),
    ]
