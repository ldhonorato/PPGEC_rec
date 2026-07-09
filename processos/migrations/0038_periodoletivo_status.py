from django.db import migrations, models
from django.utils import timezone


def inicializar_status_periodos(apps, schema_editor):
    PeriodoLetivo = apps.get_model("processos", "PeriodoLetivo")
    hoje = timezone.localdate()

    for periodo in PeriodoLetivo.objects.all().iterator():
        if periodo.encerrado_manualmente_em:
            status = "ENCERRADO"
        elif periodo.matricula_inicio <= hoje <= periodo.matricula_fim:
            status = "MATRICULA_ABERTA"
        elif periodo.modificacao_inicio <= hoje <= periodo.modificacao_fim:
            status = "MODIFICACAO_MATRICULA"
        elif hoje > periodo.modificacao_fim:
            status = "ENCERRADO"
        else:
            status = "PLANEJAMENTO"
        PeriodoLetivo.objects.filter(pk=periodo.pk).update(status=status)


class Migration(migrations.Migration):

    dependencies = [
        ("processos", "0037_ofertadisciplina_docente_colaborador_remove_ativa"),
    ]

    operations = [
        migrations.AddField(
            model_name="periodoletivo",
            name="status",
            field=models.CharField(
                choices=[
                    ("PLANEJAMENTO", "Planejamento"),
                    ("MATRICULA_ABERTA", "Matrícula aberta"),
                    ("MODIFICACAO_MATRICULA", "Modificação de matrícula"),
                    ("ENCERRADO", "Encerrado"),
                ],
                default="PLANEJAMENTO",
                max_length=25,
            ),
        ),
        migrations.RunPython(inicializar_status_periodos, migrations.RunPython.noop),
    ]
