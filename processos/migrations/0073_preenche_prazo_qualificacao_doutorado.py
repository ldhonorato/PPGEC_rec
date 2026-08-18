from django.db import migrations


def preencher_quinto_semestre(apps, schema_editor):
    TrajetoriaAcademica = apps.get_model("processos", "TrajetoriaAcademica")
    trajetorias = TrajetoriaAcademica.objects.filter(nivel_curso="DOUTORADO").only("id", "ingresso")
    for trajetoria in trajetorias.iterator():
        if not trajetoria.ingresso:
            continue
        ano, periodo = (int(parte) for parte in trajetoria.ingresso.split("."))
        indice = ano * 2 + (periodo - 1) + 4
        prazo = f"{indice // 2}.{indice % 2 + 1}"
        TrajetoriaAcademica.objects.filter(pk=trajetoria.pk).update(prazo_qualificacao=prazo)


class Migration(migrations.Migration):
    dependencies = [("processos", "0072_trajetoriaacademica_data_limite_qualificacao_and_more")]

    operations = [migrations.RunPython(preencher_quinto_semestre, migrations.RunPython.noop)]
