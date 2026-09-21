from django.db import migrations, models
import django.db.models.deletion


def migrar_reservas_existentes(apps, schema_editor):
    Aula = apps.get_model("processos", "AulaPresencialOferta")
    Ambiente = apps.get_model("processos", "AmbienteAulaPresencial")
    for aula in Aula.objects.exclude(sala_id=None).exclude(reserva_id=None):
        Ambiente.objects.create(aula_id=aula.pk, sala_id=aula.sala_id, reserva_id=aula.reserva_id)


class Migration(migrations.Migration):
    dependencies = [("processos", "0079_solicitacoes_aulas_presenciais")]

    operations = [
        migrations.CreateModel(
            name="AmbienteAulaPresencial",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("criado_em", models.DateTimeField(auto_now_add=True)),
                ("aula", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="ambientes_reservados", to="processos.aulapresencialoferta")),
                ("reserva", models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name="ambiente_aula_presencial", to="processos.reservaambiente")),
                ("sala", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="alocacoes_aulas_presenciais", to="processos.sala")),
            ],
            options={"ordering": ["sala__polo__nome", "sala__nome"]},
        ),
        migrations.AddConstraint(model_name="ambienteaulapresencial", constraint=models.UniqueConstraint(fields=("aula", "sala"), name="unique_sala_por_aula_presencial")),
        migrations.RunPython(migrar_reservas_existentes, migrations.RunPython.noop),
        migrations.RemoveField(model_name="aulapresencialoferta", name="reserva"),
        migrations.RemoveField(model_name="aulapresencialoferta", name="sala"),
    ]
