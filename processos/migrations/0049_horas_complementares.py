from decimal import Decimal

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def seed_norma_001_2019(apps, schema_editor):
    Norma = apps.get_model("processos", "NormaHorasComplementares")
    Grupo = apps.get_model("processos", "GrupoLimiteHorasComplementares")
    Tipo = apps.get_model("processos", "TipoAtividadeHorasComplementares")
    Lancamento = apps.get_model("processos", "LancamentoHorasComplementares")

    grupos = [
        ("APRESENTACAO_CONFERENCIA", "Apresentação em conferência", Decimal("20.00"), 10),
        ("PARTICIPACAO_CONFERENCIA", "Participação em conferência", Decimal("20.00"), 20),
        ("PALESTRAS", "Palestras", None, 30),
        ("CURSOS_TREINAMENTOS", "Cursos e treinamentos", Decimal("20.00"), 40),
        ("BANCAS", "Bancas", None, 50),
        ("REPRESENTACAO_DISCENTE", "Representação discente", Decimal("20.00"), 60),
        ("SALDO_ANTERIOR", "Saldo anterior", None, 999),
    ]
    tipos = [
        ("APRESENTACAO_CONFERENCIA", "Apresentação de artigo em conferência internacional", "apresentação", Decimal("5.00"), 10),
        ("APRESENTACAO_CONFERENCIA", "Apresentação de artigo em conferência nacional", "apresentação", Decimal("3.00"), 20),
        ("PARTICIPACAO_CONFERENCIA", "Participação em conferência internacional", "dias", Decimal("5.00"), 30),
        ("PARTICIPACAO_CONFERENCIA", "Participação em conferência nacional", "dias", Decimal("3.00"), 40),
        ("PALESTRAS", "Participação em palestra", "participação", Decimal("1.00"), 50),
        ("CURSOS_TREINAMENTOS", "Curso presencial ou treinamento", "curso", Decimal("3.00"), 60),
        ("BANCAS", "Participação em banca de mestrado", "banca", Decimal("3.00"), 70),
        ("BANCAS", "Participação em banca de doutorado", "banca", Decimal("3.00"), 80),
        ("REPRESENTACAO_DISCENTE", "Representação discente do Programa", "semestre", Decimal("3.00"), 90),
        ("SALDO_ANTERIOR", "Saldo anterior de horas complementares", "hora", Decimal("1.00"), 999),
    ]

    for nivel in ("MESTRADO", "DOUTORADO"):
        norma, _ = Norma.objects.get_or_create(
            identificacao="Norma 001/2019",
            nivel_curso=nivel,
            defaults={
                "nome": "Seminários de Complementação",
                "descricao": "Norma 001/2019 do PPGEC para controle de horas complementares.",
                "inicio_vigencia": "2019-01-01",
                "carga_horaria_exigida": 45,
                "status": "VIGENTE",
            },
        )
        grupos_criados = {}
        for codigo, nome, limite, ordem in grupos:
            grupo, _ = Grupo.objects.get_or_create(
                norma=norma,
                nome=nome,
                defaults={"limite_maximo": limite, "ordem": ordem},
            )
            grupos_criados[codigo] = grupo
        for codigo_grupo, nome, unidade, horas, ordem in tipos:
            Tipo.objects.get_or_create(
                norma=norma,
                nome=nome,
                defaults={
                    "grupo_limite": grupos_criados[codigo_grupo],
                    "unidade_calculo": unidade,
                    "horas_por_unidade": horas,
                    "ordem": ordem,
                    "ativo": True,
                },
            )

    table = "processos_aluno"
    column = "horas_complementares_cursadas"
    with schema_editor.connection.cursor() as cursor:
        columns = [col.name for col in schema_editor.connection.introspection.get_table_description(cursor, table)]
        if column not in columns:
            return
        cursor.execute(f"SELECT user_ptr_id, {column} FROM {table} WHERE {column} IS NOT NULL AND {column} > 0")
        saldos = cursor.fetchall()

    for aluno_id, saldo in saldos:
        norma = Norma.objects.filter(nivel_curso="MESTRADO", status="VIGENTE").first()
        tipo = Tipo.objects.filter(norma=norma, nome="Saldo anterior de horas complementares").first()
        if not norma or not tipo:
            continue
        Lancamento.objects.get_or_create(
            aluno_id=aluno_id,
            tipo_atividade=tipo,
            descricao="Saldo existente antes da implantação do controle por lançamentos",
            origem_migracao=True,
            defaults={
                "norma": norma,
                "grupo_limite": tipo.grupo_limite,
                "quantidade": saldo,
                "unidade_quantidade": tipo.unidade_calculo,
                "horas_solicitadas": saldo,
                "horas_calculadas": saldo,
                "horas_aprovadas": saldo,
                "observacoes_secretaria": "Saldo existente antes da implantação do controle por lançamentos.",
                "referencia_decisao": "Migração de dados",
                "criado_por_id": aluno_id,
                "status": "ATIVO",
                "limite_grupo_no_lancamento": None,
                "limite_individual_no_lancamento": None,
            },
        )


class Migration(migrations.Migration):

    dependencies = [
        ("processos", "0048_aula_presencial_horario_avulso"),
    ]

    operations = [
        migrations.AlterField(
            model_name="alteracaoaluno",
            name="tipo",
            field=models.CharField(
                choices=[
                    ("STATUS", "Status"),
                    ("QUALIFICACAO", "Qualificacao"),
                    ("HORAS_COMPLEMENTARES", "Horas complementares"),
                    ("DEFESA", "Defesa"),
                    ("DEPOSITO_FINAL", "Deposito versao final"),
                    ("PRAZO_QUALIFICACAO", "Prazo qualificacao"),
                    ("PRAZO_DEFESA", "Prazo defesa"),
                    ("ORIENTADOR", "Orientador"),
                    ("COORIENTADOR", "Coorientador"),
                    ("REINGRESSO", "Reingresso"),
                    ("TRAJETORIA", "Trajetoria academica"),
                    ("ESTAGIO_DOCENCIA", "Estagio docencia"),
                ],
                max_length=25,
            ),
        ),
        migrations.AlterField(
            model_name="processo",
            name="tipo",
            field=models.CharField(
                choices=[
                    ("APROVEITAMENTO_DISPENSA_CREDITOS", "Aproveitamento de Créditos ou Dispensa de Disciplina"),
                    ("DEFESA_MESTRADO", "Defesa de Mestrado"),
                    ("DEFESA_DOUTORADO", "Defesa de Doutorado"),
                    ("QUALIFICACAO_DOUTORADO", "Qualificação de Doutorado"),
                    ("ESTAGIO_DOCENCIA", "Estágio docência"),
                    ("HORAS_COMPLEMENTARES", "Horas complementares"),
                    ("TRANCAMENTO_MATRICULA", "Trancamento de Matrícula"),
                    ("PRORROGACAO_PRAZO", "Prorrogação de Prazo"),
                    ("REINGRESSO", "Reingresso"),
                    ("MUDANCA_ORIENTADOR", "Mudança de Orientador(a)"),
                    ("OUTRO", "Outro"),
                ],
                max_length=40,
            ),
        ),
        migrations.CreateModel(
            name="NormaHorasComplementares",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("nome", models.CharField(max_length=255)),
                ("identificacao", models.CharField(max_length=80)),
                ("descricao", models.TextField(blank=True)),
                ("inicio_vigencia", models.DateField()),
                ("fim_vigencia", models.DateField(blank=True, null=True)),
                ("carga_horaria_exigida", models.PositiveIntegerField(default=45)),
                ("nivel_curso", models.CharField(choices=[("MESTRADO", "Mestrado"), ("DOUTORADO", "Doutorado"), ("POSDOUTORADO", "Posdoutorado"), ("ALUNO_ESPECIAL", "Aluno especial")], max_length=20)),
                ("status", models.CharField(choices=[("RASCUNHO", "Rascunho"), ("VIGENTE", "Vigente"), ("REVOGADA", "Revogada")], default="RASCUNHO", max_length=12)),
                ("criado_em", models.DateTimeField(auto_now_add=True)),
                ("atualizado_em", models.DateTimeField(auto_now=True)),
            ],
            options={"ordering": ["-inicio_vigencia", "identificacao", "nivel_curso"], "unique_together": {("identificacao", "nivel_curso")}},
        ),
        migrations.CreateModel(
            name="GrupoLimiteHorasComplementares",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("nome", models.CharField(max_length=120)),
                ("descricao", models.TextField(blank=True)),
                ("limite_maximo", models.DecimalField(blank=True, decimal_places=2, max_digits=6, null=True)),
                ("ordem", models.PositiveIntegerField(default=0)),
                ("norma", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="grupos_limite", to="processos.normahorascomplementares")),
            ],
            options={"ordering": ["ordem", "nome"], "unique_together": {("norma", "nome")}},
        ),
        migrations.CreateModel(
            name="TipoAtividadeHorasComplementares",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("nome", models.CharField(max_length=180)),
                ("descricao", models.TextField(blank=True)),
                ("unidade_calculo", models.CharField(max_length=40)),
                ("horas_por_unidade", models.DecimalField(decimal_places=2, max_digits=6)),
                ("limite_individual", models.DecimalField(blank=True, decimal_places=2, max_digits=6, null=True)),
                ("ativo", models.BooleanField(default=True)),
                ("ordem", models.PositiveIntegerField(default=0)),
                ("grupo_limite", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="tipos_atividade", to="processos.grupolimitehorascomplementares")),
                ("norma", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="tipos_atividade", to="processos.normahorascomplementares")),
            ],
            options={"ordering": ["ordem", "nome"], "unique_together": {("norma", "nome")}},
        ),
        migrations.CreateModel(
            name="LancamentoHorasComplementares",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("descricao", models.CharField(max_length=255)),
                ("periodo_realizacao", models.CharField(blank=True, max_length=120)),
                ("quantidade", models.DecimalField(decimal_places=2, max_digits=8)),
                ("unidade_quantidade", models.CharField(max_length=40)),
                ("horas_solicitadas", models.DecimalField(decimal_places=2, default=0, max_digits=6)),
                ("horas_calculadas", models.DecimalField(decimal_places=2, default=0, max_digits=6)),
                ("horas_aprovadas", models.DecimalField(decimal_places=2, max_digits=6)),
                ("limite_grupo_no_lancamento", models.DecimalField(blank=True, decimal_places=2, max_digits=6, null=True)),
                ("limite_individual_no_lancamento", models.DecimalField(blank=True, decimal_places=2, max_digits=6, null=True)),
                ("observacoes_secretaria", models.TextField(blank=True)),
                ("referencia_decisao", models.TextField(blank=True)),
                ("justificativa_excepcional", models.TextField(blank=True)),
                ("excepcional_autorizado", models.BooleanField(default=False)),
                ("criado_em", models.DateTimeField(auto_now_add=True)),
                ("atualizado_em", models.DateTimeField(auto_now=True)),
                ("status", models.CharField(choices=[("ATIVO", "Ativo"), ("CANCELADO", "Cancelado"), ("RETIFICADO", "Retificado")], default="ATIVO", max_length=12)),
                ("cancelado_em", models.DateTimeField(blank=True, null=True)),
                ("justificativa_cancelamento", models.TextField(blank=True)),
                ("origem_migracao", models.BooleanField(default=False)),
                ("justificativa_sem_processo", models.TextField(blank=True)),
                ("aluno", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="lancamentos_horas_complementares", to="processos.aluno")),
                ("cancelado_por", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="lancamentos_horas_complementares_cancelados", to=settings.AUTH_USER_MODEL)),
                ("criado_por", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="lancamentos_horas_complementares_criados", to=settings.AUTH_USER_MODEL)),
                ("grupo_limite", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="lancamentos", to="processos.grupolimitehorascomplementares")),
                ("norma", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="lancamentos", to="processos.normahorascomplementares")),
                ("processo_origem", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="lancamentos_horas_complementares", to="processos.processo")),
                ("substitui_lancamento", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="retificacoes", to="processos.lancamentohorascomplementares")),
                ("tipo_atividade", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="lancamentos", to="processos.tipoatividadehorascomplementares")),
            ],
            options={"ordering": ["-criado_em"]},
        ),
        migrations.RunPython(seed_norma_001_2019, migrations.RunPython.noop),
    ]
