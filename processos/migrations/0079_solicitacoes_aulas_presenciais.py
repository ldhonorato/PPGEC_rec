from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def preencher_solicitacoes_existentes(apps, schema_editor):
    AulaPresencialOferta = apps.get_model("processos", "AulaPresencialOferta")
    for aula in AulaPresencialOferta.objects.select_related("sala").all():
        aula.polo_solicitado_id = aula.sala.polo_id
        aula.status_agendamento = "ATENDIDA" if aula.reserva_id else "PENDENTE"
        aula.save(update_fields=["polo_solicitado", "status_agendamento"])


class Migration(migrations.Migration):
    dependencies = [("processos", "0078_alter_documento_arquivo_max_length")]

    operations = [
        migrations.AddField(
            model_name="aulapresencialoferta", name="polo_solicitado",
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.PROTECT, related_name="solicitacoes_aulas_presenciais", to="processos.polo"),
        ),
        migrations.AlterField(
            model_name="aulapresencialoferta", name="sala",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="aulas_presenciais_ofertas", to="processos.sala"),
        ),
        migrations.AddField(model_name="aulapresencialoferta", name="status_agendamento", field=models.CharField(choices=[("PENDENTE", "Pendente"), ("ATENDIDA", "Atendida"), ("NAO_ATENDIDA", "Não atendida")], default="PENDENTE", max_length=15)),
        migrations.AddField(model_name="aulapresencialoferta", name="observacao_atendimento", field=models.TextField(blank=True)),
        migrations.AddField(model_name="aulapresencialoferta", name="atendida_em", field=models.DateTimeField(blank=True, null=True)),
        migrations.AddField(
            model_name="aulapresencialoferta", name="atendida_por",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="aulas_presenciais_atendidas", to=settings.AUTH_USER_MODEL),
        ),
        migrations.RunPython(preencher_solicitacoes_existentes, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="aulapresencialoferta", name="polo_solicitado",
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="solicitacoes_aulas_presenciais", to="processos.polo"),
        ),
    ]
