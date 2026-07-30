"""Converte Disciplina.tipo de texto livre para as tres categorias do programa.

O campo era um CharField de 120 caracteres sem choices, preenchido a mao no
formulario de cadastro de disciplinas. Nas 47 disciplinas cadastradas isso
produziu cinco grafias para tres categorias:

    Disciplina Eletiva Geral   29
    Disciplina Básica           9
    Disciplina Eletiva Área     7
    Disciplina Basica           1   <- sem acento
    Obrigatória                 1

O prejuizo nao e so de escrita. Enquanto o tipo e texto livre, nao ha como
filtrar disciplinas por categoria nem contar quantas eletivas o aluno cursou --
e a contagem de eletivas e parte da integralizacao.

Sobre o mapeamento de "Obrigatória": e uma unica disciplina, PPGEC027 (Projeto
de Pesquisa). Nao existe categoria "obrigatoria" na estrutura de tres niveis, e
Projeto de Pesquisa e componente que todo aluno cursa, entao vai para BASICA. Se
o programa entender que ela e outra coisa, e uma alteracao pontual na tela de
disciplinas -- nao um novo problema de dados.

Valor nao reconhecido fica em branco em vez de ser descartado silenciosamente:
em branco a tela mostra "—" e a coordenacao ve que falta classificar, enquanto um
valor invalido no banco quebraria get_tipo_display() na leitura.
"""

from django.db import migrations, models

# Grafia encontrada no banco -> categoria. As chaves estao em minusculas e sem
# espacos nas pontas; a comparacao normaliza o valor lido antes de procurar aqui,
# para que "Disciplina Basica" e "disciplina  basica" caiam no mesmo lugar.
DE_PARA = {
    "disciplina básica": "BASICA",
    "disciplina basica": "BASICA",
    "básica": "BASICA",
    "basica": "BASICA",
    "obrigatória": "BASICA",
    "obrigatoria": "BASICA",
    "disciplina eletiva geral": "ELETIVA_GERAL",
    "eletiva geral": "ELETIVA_GERAL",
    "disciplina eletiva área": "ELETIVA_ESPECIFICA",
    "disciplina eletiva area": "ELETIVA_ESPECIFICA",
    "disciplina eletiva específica": "ELETIVA_ESPECIFICA",
    "disciplina eletiva especifica": "ELETIVA_ESPECIFICA",
    "eletiva específica": "ELETIVA_ESPECIFICA",
    "eletiva especifica": "ELETIVA_ESPECIFICA",
    "eletiva área": "ELETIVA_ESPECIFICA",
    "eletiva area": "ELETIVA_ESPECIFICA",
}

ROTULOS = {
    "BASICA": "Básica",
    "ELETIVA_GERAL": "Eletiva geral",
    "ELETIVA_ESPECIFICA": "Eletiva específica",
}


def _normalizar(valor):
    return " ".join((valor or "").split()).lower()


def converter(apps, schema_editor):
    Disciplina = apps.get_model("processos", "Disciplina")
    nao_reconhecidos = {}
    for disciplina in Disciplina.objects.exclude(tipo=""):
        novo = DE_PARA.get(_normalizar(disciplina.tipo))
        if novo is None:
            nao_reconhecidos.setdefault(disciplina.tipo, []).append(disciplina.codigo)
            novo = ""
        Disciplina.objects.filter(pk=disciplina.pk).update(tipo=novo)

    if nao_reconhecidos:
        # Nao interrompe a migracao: a disciplina fica sem categoria e aparece
        # como "—" na tela, que e um estado corrigivel. O aviso existe para que
        # quem rodar a migracao saiba o que precisa reclassificar.
        for valor, codigos in nao_reconhecidos.items():
            print(f"  tipo de disciplina nao reconhecido, em branco: {valor!r} -> {', '.join(codigos)}")


def desfazer(apps, schema_editor):
    """Volta para o rotulo legivel, nao para a grafia original.

    As cinco grafias colapsaram em tres categorias; nao ha como saber qual delas
    cada registro tinha. Reverter para o rotulo ("Básica") devolve um texto valido
    e consistente, que e o melhor disponivel -- e o que se perde e justamente a
    inconsistencia.
    """
    Disciplina = apps.get_model("processos", "Disciplina")
    for chave, rotulo in ROTULOS.items():
        Disciplina.objects.filter(tipo=chave).update(tipo=rotulo)


class Migration(migrations.Migration):

    dependencies = [
        ("processos", "0061_alter_user_email_alter_user_nome"),
    ]

    operations = [
        # A conversao vem antes da troca de max_length: os valores antigos tem
        # ate 120 caracteres e nao caberiam no campo novo.
        migrations.RunPython(converter, desfazer),
        migrations.AlterField(
            model_name="disciplina",
            name="tipo",
            field=models.CharField(
                blank=True,
                choices=[
                    ("BASICA", "Básica"),
                    ("ELETIVA_GERAL", "Eletiva geral"),
                    ("ELETIVA_ESPECIFICA", "Eletiva específica"),
                ],
                max_length=25,
            ),
        ),
    ]
