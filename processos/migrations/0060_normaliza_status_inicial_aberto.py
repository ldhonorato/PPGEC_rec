"""Normaliza o status_inicial "ABERTO", que não existe nas choices do campo.

`Processo.status_inicial` guarda o status que o processo tinha ao ser criado, e
o próprio modelo o preenche com `self.status` (models.py, no save de criação).
As choices são as mesmas de `status`, e "ABERTO" não está entre elas.

O valor entrou pelos dados de exemplo: a seed da migração 0003 grava
`status_inicial="ABERTO"` explicitamente. A aplicação nunca produz esse valor.

Efeito visível: `get_status_inicial_display()` não encontra correspondência e
devolve a chave, então o histórico do processo mostrava "Status inicial:
ABERTO" em maiúsculas, ao lado de rótulos normais.

Converte para EM_ANALISE, que é o status com que um processo nasce.
"""

from django.db import migrations

ANTIGO = "ABERTO"
NOVO = "EM_ANALISE"


def normalizar(apps, schema_editor):
    Processo = apps.get_model("processos", "Processo")
    Processo.objects.filter(status_inicial=ANTIGO).update(status_inicial=NOVO)


def desfazer(apps, schema_editor):
    """Sem reverso real.

    Depois da conversão não há como distinguir os registros que tinham
    "ABERTO" dos que já nasceram "EM_ANALISE" -- e devolver todos ao valor
    antigo recriaria o defeito em registros que nunca o tiveram. Como "ABERTO"
    não é um valor válido do campo, manter os dados consistentes é preferível
    a poder desfazer.
    """
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("processos", "0059_alter_disciplina_codigo_and_more"),
    ]

    operations = [
        migrations.RunPython(normalizar, desfazer),
    ]
