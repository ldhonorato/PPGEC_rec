from django.db import migrations


def concluir_trajetorias_aluno_especial(apps, schema_editor):
    TrajetoriaAcademica = apps.get_model("processos", "TrajetoriaAcademica")
    TrajetoriaAcademica.objects.filter(
        nivel_curso="ALUNO_ESPECIAL",
        status="ATIVA",
    ).update(status="CONCLUIDA")


class Migration(migrations.Migration):

    dependencies = [
        ("processos", "0054_aluno_cpf_aluno_genero"),
    ]

    operations = [
        migrations.RunPython(concluir_trajetorias_aluno_especial, migrations.RunPython.noop),
    ]
