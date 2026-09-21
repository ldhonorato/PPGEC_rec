from django.db import migrations


def remover_coluna_obsoleta(apps, schema_editor):
    """Remove uma coluna que não pertence ao estado atual do modelo.

    Alguns bancos receberam ``progresso_tangivel`` fora do histórico de
    migrações disponível no projeto. Como ela é NOT NULL, qualquer inclusão
    feita pelo ORM falha antes mesmo que uma prorrogação possa ser registrada.
    """
    tabela = "processos_prorrogacaotrajetoria"
    with schema_editor.connection.cursor() as cursor:
        colunas = {
            coluna.name
            for coluna in schema_editor.connection.introspection.get_table_description(cursor, tabela)
        }

    if "progresso_tangivel" in colunas:
        quote = schema_editor.quote_name
        schema_editor.execute(
            f"ALTER TABLE {quote(tabela)} DROP COLUMN {quote('progresso_tangivel')}"
        )


class Migration(migrations.Migration):
    dependencies = [
        ("processos", "0074_processo_aluno_interessado"),
    ]

    operations = [
        migrations.RunPython(remover_coluna_obsoleta, migrations.RunPython.noop),
    ]
