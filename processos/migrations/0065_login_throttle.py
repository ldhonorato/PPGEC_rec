from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("processos", "0064_alter_trajetoria_nivel_pos_doutorado")]

    operations = [
        migrations.CreateModel(
            name="LoginThrottle",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("scope", models.CharField(max_length=16)),
                ("fingerprint", models.CharField(max_length=64)),
                ("failure_count", models.PositiveIntegerField(default=0)),
                ("window_started_at", models.DateTimeField()),
                ("locked_until", models.DateTimeField(blank=True, null=True)),
            ],
            options={
                "constraints": [
                    models.UniqueConstraint(
                        fields=("scope", "fingerprint"),
                        name="processos_login_throttle_scope_fingerprint_uniq",
                    )
                ]
            },
        )
    ]
