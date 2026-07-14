import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("processos", "0044_solicitacaomatricula_tipo_matricula"),
    ]

    operations = [
        migrations.AddField(
            model_name="periodoletivo",
            name="data_inicio",
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="periodoletivo",
            name="data_fim",
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="periodoletivo",
            name="prazo_agendamento_aulas_presenciais",
            field=models.DateField(blank=True, null=True),
        ),
        migrations.CreateModel(
            name="AulaPresencialOferta",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("data", models.DateField()),
                ("atualizado_em", models.DateTimeField(auto_now=True)),
                ("criado_em", models.DateTimeField(auto_now_add=True)),
                (
                    "criado_por",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="aulas_presenciais_ofertas_criadas",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "encontro",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="aulas_presenciais",
                        to="processos.encontrooferta",
                    ),
                ),
                (
                    "oferta",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="aulas_presenciais",
                        to="processos.ofertadisciplina",
                    ),
                ),
                (
                    "reserva",
                    models.OneToOneField(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="aula_presencial_oferta",
                        to="processos.reservaambiente",
                    ),
                ),
                (
                    "sala",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="aulas_presenciais_ofertas",
                        to="processos.sala",
                    ),
                ),
            ],
            options={
                "ordering": ["data", "encontro__hora_inicio"],
            },
        ),
        migrations.AddConstraint(
            model_name="aulapresencialoferta",
            constraint=models.UniqueConstraint(
                fields=("oferta", "data", "encontro"),
                name="unique_aula_presencial_oferta_data_encontro",
            ),
        ),
    ]
