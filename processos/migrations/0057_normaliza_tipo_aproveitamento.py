"""Normaliza o tipo de processo APROVEITAMENTO_CREDITOS.

A migração 0019 renomeou a opção `APROVEITAMENTO_CREDITOS` para
`APROVEITAMENTO_DISPENSA_CREDITOS` nas choices do campo, mas não migrou os
registros que já existiam. O valor ficou órfão: `get_tipo_display()` não
encontra correspondência e devolve a própria chave, então a listagem de
processos mostra "APROVEITAMENTO_CREDITOS" em maiúsculas ao lado de rótulos
normais como "Prorrogação de Prazo".

Atinge qualquer processo criado antes da 0019 e também os dados de exemplo,
porque a seed da migração 0003 grava a chave antiga e roda antes da 0019.
"""

from django.db import migrations

ANTIGO = "APROVEITAMENTO_CREDITOS"
NOVO = "APROVEITAMENTO_DISPENSA_CREDITOS"


def renomear(apps, schema_editor):
    Processo = apps.get_model("processos", "Processo")
    Processo.objects.filter(tipo=ANTIGO).update(tipo=NOVO)


def desfazer(apps, schema_editor):
    """Volta apenas o que esta migração alteraria.

    Não é possível distinguir os registros convertidos aqui dos que já usavam
    o valor novo, então o reverso devolve todos ao valor antigo -- o que
    recria a situação anterior à migração para o campo como um todo.
    """
    Processo = apps.get_model("processos", "Processo")
    Processo.objects.filter(tipo=NOVO).update(tipo=ANTIGO)


class Migration(migrations.Migration):

    dependencies = [
        ("processos", "0056_alter_alteracaoaluno_tipo_and_more"),
    ]

    operations = [
        migrations.RunPython(renomear, desfazer),
    ]
