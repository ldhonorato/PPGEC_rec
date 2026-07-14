import django.db.models.deletion
from django.db import migrations, models


def preencher_trajetoria_lancamentos(apps, schema_editor):
    Lancamento = apps.get_model("processos", "LancamentoHorasComplementares")
    Trajetoria = apps.get_model("processos", "TrajetoriaAcademica")

    for lancamento in Lancamento.objects.select_related("norma").all():
        trajetoria = (
            Trajetoria.objects.filter(
                aluno_id=lancamento.aluno_id,
                nivel_curso=lancamento.norma.nivel_curso,
                status="ATIVA",
            )
            .order_by("-criado_em")
            .first()
        )
        if not trajetoria:
            trajetoria = (
                Trajetoria.objects.filter(
                    aluno_id=lancamento.aluno_id,
                    nivel_curso=lancamento.norma.nivel_curso,
                )
                .order_by("-criado_em")
                .first()
            )
        if not trajetoria:
            trajetoria = Trajetoria.objects.filter(aluno_id=lancamento.aluno_id).order_by("-criado_em").first()
        if trajetoria:
            lancamento.trajetoria_id = trajetoria.id
            lancamento.save(update_fields=["trajetoria"])


class Migration(migrations.Migration):

    dependencies = [
        ("processos", "0049_horas_complementares"),
    ]

    operations = [
        migrations.AddField(
            model_name="lancamentohorascomplementares",
            name="trajetoria",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="lancamentos_horas_complementares",
                to="processos.trajetoriaacademica",
            ),
        ),
        migrations.RunPython(preencher_trajetoria_lancamentos, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="lancamentohorascomplementares",
            name="trajetoria",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="lancamentos_horas_complementares",
                to="processos.trajetoriaacademica",
            ),
        ),
        migrations.RemoveField(
            model_name="lancamentohorascomplementares",
            name="aluno",
        ),
    ]
