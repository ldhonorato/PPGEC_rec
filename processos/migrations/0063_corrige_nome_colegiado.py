"""Corrige "Colegiando PPGEC (Pleno)" para "Colegiado PPGEC (Pleno)".

O orgao chama-se colegiado. "Colegiando" e erro de digitacao, e a propria
descricao do setor sempre disse a palavra certa: "Deliberacoes do colegiado
pleno."

O erro entrou pela migracao 0005, que normalizava os setores oficiais e tinha
"Colegiado PPGEC (Pleno)" entre os apelidos a renomear -- ou seja, renomeava o
nome correto para o errado. Dali em diante o codigo passou a comparar com a
grafia errada em quatro lugares, e o nome aparecia assim em toda tela que mostra
o setor de um processo.

O nome agora vive em Setor.NOME_PLENO. Enquanto estava escrito a mao em cada
comparacao, corrigir o dado sem corrigir as quatro copias faria o sistema deixar
de reconhecer o pleno: nenhum docente veria "Processos no Pleno" e ninguem
poderia deliberar. E por isso que esta migracao e a troca das referencias andam
no mesmo commit.
"""

from django.db import migrations

ERRADO = "Colegiando PPGEC (Pleno)"
CORRETO = "Colegiado PPGEC (Pleno)"


def corrigir(apps, schema_editor):
    Setor = apps.get_model("processos", "Setor")

    # nome e unique. Se as duas grafias existirem, o registro correto e o que
    # fica, e o errado precisa ter suas referencias transferidas antes de sair --
    # senao o update violaria a restricao.
    errado = Setor.objects.filter(nome=ERRADO).first()
    if errado is None:
        return

    correto = Setor.objects.filter(nome=CORRETO).exclude(pk=errado.pk).first()
    if correto is None:
        Setor.objects.filter(pk=errado.pk).update(nome=CORRETO)
        return

    Processo = apps.get_model("processos", "Processo")
    TramitacaoProcesso = apps.get_model("processos", "TramitacaoProcesso")
    SetorMembro = apps.get_model("processos", "SetorMembro")

    Processo.objects.filter(setor_atual_id=errado.pk).update(setor_atual_id=correto.pk)
    TramitacaoProcesso.objects.filter(setor_destino_id=errado.pk).update(setor_destino_id=correto.pk)
    TramitacaoProcesso.objects.filter(setor_origem_id=errado.pk).update(setor_origem_id=correto.pk)
    # Membro ja presente no setor correto nao pode ser duplicado.
    ja_no_correto = set(
        SetorMembro.objects.filter(setor_id=correto.pk).values_list("usuario_id", flat=True)
    )
    SetorMembro.objects.filter(setor_id=errado.pk, usuario_id__in=ja_no_correto).delete()
    SetorMembro.objects.filter(setor_id=errado.pk).update(setor_id=correto.pk)
    Setor.objects.filter(pk=errado.pk).delete()


def desfazer(apps, schema_editor):
    """Sem reverso.

    Voltar a grafia errada nao tem valor -- e o codigo, que agora compara com
    Setor.NOME_PLENO, deixaria de reconhecer o pleno.
    """
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("processos", "0062_disciplina_tipo_categorias"),
    ]

    operations = [
        migrations.RunPython(corrigir, desfazer),
    ]
