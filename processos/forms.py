from datetime import datetime

from django import forms
from django.contrib.auth import get_user_model
from django.utils import timezone

from .models import (
    Aluno,
    DisponibilidadeSala,
    Documento,
    Processo,
    ReservaAmbiente,
    Sala,
    Setor,
    TrajetoriaAcademica,
)


User = get_user_model()


class UserProfileForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ["nome", "email"]


class SetorComissaoForm(forms.ModelForm):
    docentes = forms.ModelMultipleChoiceField(
        queryset=User.objects.filter(tipo_usuario=User.TipoUsuario.DOCENTE, is_active=True).order_by("nome", "email"),
        required=False,
        label="Docentes",
        widget=forms.CheckboxSelectMultiple,
    )
    servidores = forms.ModelMultipleChoiceField(
        queryset=User.objects.filter(tipo_usuario=User.TipoUsuario.SERVIDOR, is_active=True).order_by("nome", "email"),
        required=False,
        label="Servidores",
        widget=forms.CheckboxSelectMultiple,
    )
    alunos = forms.ModelMultipleChoiceField(
        queryset=User.objects.filter(tipo_usuario=User.TipoUsuario.ALUNO).order_by("nome", "email"),
        required=False,
        label="Alunos",
        widget=forms.CheckboxSelectMultiple,
    )

    class Meta:
        model = Setor
        fields = ["nome", "descricao", "email", "ativo", "docentes", "servidores", "alunos"]


class SalaForm(forms.ModelForm):
    class Meta:
        model = Sala
        fields = ["nome", "capacidade", "ativa"]


class DisponibilidadeSalaForm(forms.ModelForm):
    class Meta:
        model = DisponibilidadeSala
        fields = ["sala", "dia_semana", "hora_inicio", "hora_fim"]
        widgets = {
            "hora_inicio": forms.TimeInput(attrs={"type": "time"}),
            "hora_fim": forms.TimeInput(attrs={"type": "time"}),
        }

    def __init__(self, *args, polo=None, **kwargs):
        super().__init__(*args, **kwargs)
        queryset = Sala.objects.filter(ativa=True).order_by("nome")
        if polo:
            queryset = queryset.filter(polo=polo)
        self.fields["sala"].queryset = queryset


class ReservaAmbienteForm(forms.Form):
    RECORRENCIA_NENHUMA = "NENHUMA"
    RECORRENCIA_DIARIA = "DIARIA"
    RECORRENCIA_SEMANAL = "SEMANAL"
    RECORRENCIA_MENSAL = "MENSAL"

    sala = forms.ModelChoiceField(queryset=Sala.objects.none(), label="Sala")
    docente = forms.ModelChoiceField(
        queryset=User.objects.filter(tipo_usuario=User.TipoUsuario.DOCENTE, is_active=True).order_by("nome"),
        required=False,
        label="Docente",
    )
    tipo = forms.ChoiceField(choices=ReservaAmbiente.TipoReserva.choices, label="Tipo de reserva")
    titulo = forms.CharField(max_length=255, required=False, label="Titulo")
    data_inicio = forms.DateField(
        label="Data de inicio",
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    hora_inicio = forms.TimeField(
        label="Hora de inicio",
        widget=forms.TimeInput(attrs={"type": "time"}),
    )
    hora_fim = forms.TimeField(
        label="Hora de fim",
        widget=forms.TimeInput(attrs={"type": "time"}),
    )
    recorrencia = forms.ChoiceField(
        choices=(
            (RECORRENCIA_NENHUMA, "Nao repetir"),
            (RECORRENCIA_DIARIA, "Diaria"),
            (RECORRENCIA_SEMANAL, "Semanal"),
            (RECORRENCIA_MENSAL, "Mensal"),
        ),
        label="Recorrencia",
    )
    duracao_recorrencia_meses = forms.IntegerField(
        required=False,
        min_value=1,
        max_value=6,
        label="Duracao da recorrencia em meses",
        widget=forms.NumberInput(attrs={"min": "1", "max": "6"}),
    )

    def __init__(self, *args, user=None, polo=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        queryset = Sala.objects.filter(ativa=True, polo__ativo=True).select_related("polo").order_by("polo__nome", "nome")
        if polo:
            queryset = queryset.filter(polo=polo)
        self.fields["sala"].queryset = queryset
        if user and user.tipo_usuario == User.TipoUsuario.DOCENTE:
            self.fields["docente"].required = False
            self.fields["docente"].widget = forms.HiddenInput()
        else:
            self.fields["docente"].required = True

    def clean(self):
        cleaned_data = super().clean()
        data_inicio = cleaned_data.get("data_inicio")
        hora_inicio = cleaned_data.get("hora_inicio")
        hora_fim = cleaned_data.get("hora_fim")
        recorrencia = cleaned_data.get("recorrencia")
        duracao_recorrencia_meses = cleaned_data.get("duracao_recorrencia_meses")

        inicio = None
        if data_inicio and hora_inicio:
            inicio = datetime.combine(data_inicio, hora_inicio)
            if timezone.is_naive(inicio):
                inicio = timezone.make_aware(inicio)
            cleaned_data["inicio"] = inicio

        if data_inicio and hora_fim:
            fim = datetime.combine(data_inicio, hora_fim)
            if timezone.is_naive(fim):
                fim = timezone.make_aware(fim)
            cleaned_data["fim"] = fim

        if hora_inicio and hora_fim and hora_fim <= hora_inicio:
            self.add_error("hora_fim", "A hora de fim deve ser posterior a hora de inicio no mesmo dia.")
        if recorrencia != self.RECORRENCIA_NENHUMA:
            if not duracao_recorrencia_meses:
                self.add_error("duracao_recorrencia_meses", "Informe por quantos meses repetir.")
        else:
            cleaned_data["duracao_recorrencia_meses"] = None
        return cleaned_data


class DocumentoCadastroForm(forms.Form):
    titulo = forms.CharField(max_length=255, label="Titulo")
    tipo_documento = forms.ChoiceField(
        choices=[("", "Selecione")] + list(Documento.TipoDocumento.choices),
        required=False,
        label="Tipo de documento",
    )
    arquivo = forms.FileField(
        required=True,
        label="Arquivo do documento",
    )
    restricao_tipo = forms.ChoiceField(
        choices=Documento.RestricaoAcesso.choices,
        required=True,
        label="Documento restrito",
    )


class EncaminhamentoForm(forms.Form):
    setor_destino = forms.ModelChoiceField(
        queryset=Setor.objects.none(),
        label="Setor de destino",
    )
    despacho = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
        label="Despacho",
    )
    prazo_pleno = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
        label="Data limite para deliberação",
    )

    def __init__(self, *args, current_setor_id=None, allowed_setor_ids=None, **kwargs):
        super().__init__(*args, **kwargs)
        queryset = Setor.objects.filter(ativo=True).order_by("nome")
        if allowed_setor_ids is not None:
            queryset = queryset.filter(id__in=allowed_setor_ids)
        if current_setor_id:
            queryset = queryset.exclude(id=current_setor_id)
        self.fields["setor_destino"].queryset = queryset

    def clean(self):
        cleaned_data = super().clean()
        setor = cleaned_data.get("setor_destino")
        if setor and "pleno" in setor.nome.lower() and not cleaned_data.get("prazo_pleno"):
            self.add_error("prazo_pleno", "Informe a data limite para deliberação no Pleno.")
        return cleaned_data


class ProcessoAberturaForm(forms.ModelForm):
    class Meta:
        model = Processo
        fields = ["tipo", "assunto", "descricao"]
        widgets = {
            "descricao": forms.Textarea(attrs={"rows": 5}),
        }


class SolicitarCienteOrientadorForm(forms.Form):
    mensagem_solicitacao = forms.CharField(
        required=False,
        label="Observacao da solicitacao",
        widget=forms.Textarea(attrs={"rows": 3}),
    )


class ManifestarCienteOrientadorForm(forms.Form):
    mensagem_manifestacao = forms.CharField(
        required=False,
        label="Mensagem da manifestacao",
        widget=forms.Textarea(attrs={"rows": 3}),
    )


class ComentarioProcessoForm(forms.Form):
    anonimo = forms.BooleanField(required=False, label="Comentario anonimo")
    texto = forms.CharField(
        label="Comentario",
        widget=forms.Textarea(attrs={"rows": 4}),
    )


class FinalizarProcessoForm(forms.Form):
    termo_finalizacao = forms.CharField(
        label="Termo de finalizacao",
        widget=forms.Textarea(attrs={"rows": 5}),
    )


class AlunoComentarioForm(forms.Form):
    comentario = forms.CharField(
        label="Comentario da alteracao",
        widget=forms.Textarea(attrs={"rows": 3}),
    )


class AlunoStatusForm(AlunoComentarioForm):
    status_aluno = forms.ChoiceField(choices=Aluno.StatusAluno.choices, label="Status do aluno")


class AlunoDadosForm(AlunoComentarioForm):
    nome = forms.CharField(max_length=255, label="Nome")
    email = forms.EmailField(label="Email")
    matricula = forms.CharField(max_length=50, required=False, label="Matricula")

    def __init__(self, *args, aluno=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.aluno = aluno

    def clean_email(self):
        email = self.cleaned_data["email"]
        queryset = User.objects.filter(email__iexact=email)
        if self.aluno:
            queryset = queryset.exclude(pk=self.aluno.pk)
        if queryset.exists():
            raise forms.ValidationError("Ja existe um usuario com este email.")
        return email


class AlunoQualificacaoForm(AlunoComentarioForm):
    isQualificado = forms.BooleanField(required=False, label="Aluno qualificado")


class AlunoPrazoForm(AlunoComentarioForm):
    valor_semestre = forms.CharField(
        label="Semestre (YYYY.1 ou YYYY.2)",
        max_length=6,
    )


class AlunoReingressoForm(AlunoComentarioForm):
    ingresso = forms.CharField(
        label="Novo ingresso (YYYY.1 ou YYYY.2)",
        max_length=6,
    )
    prazo_qualificacao = forms.CharField(
        label="Novo prazo",
        max_length=6,
    )
    prazo_defesa = forms.CharField(
        label="Novo prazo de defesa",
        max_length=6,
    )


class AlunoIniciarDoutoradoForm(AlunoComentarioForm):
    ingresso = forms.CharField(
        label="Ingresso no doutorado (YYYY.1 ou YYYY.2)",
        max_length=6,
    )
    prazo_qualificacao = forms.CharField(
        label="Prazo de qualificacao (YYYY.1 ou YYYY.2)",
        max_length=6,
    )
    prazo_defesa = forms.CharField(
        label="Prazo de defesa (YYYY.1 ou YYYY.2)",
        max_length=6,
    )
    orientador = forms.ModelChoiceField(
        queryset=User.objects.filter(tipo_usuario=User.TipoUsuario.DOCENTE).order_by("nome"),
        required=False,
        label="Orientador do doutorado",
    )


class AlunoDefesaForm(AlunoComentarioForm):
    numero_defesa = forms.CharField(label="Numero da defesa", max_length=80)
    data_defesa = forms.DateField(
        label="Data da defesa",
        widget=forms.DateInput(attrs={"type": "date"}),
    )


class AlunoDepositoFinalForm(AlunoComentarioForm):
    deposito_versao_final = forms.BooleanField(required=False, label="Deposito da versao final")


class AlunoOrientadorForm(AlunoComentarioForm):
    orientador = forms.ModelChoiceField(
        queryset=User.objects.filter(tipo_usuario=User.TipoUsuario.DOCENTE).order_by("nome"),
        required=False,
        label="Orientador",
    )


class AlunoCoorientadorForm(AlunoComentarioForm):
    class TipoCoorientador:
        NENHUM = "NENHUM"
        CADASTRADO = "CADASTRADO"
        EXTERNO = "EXTERNO"

        choices = (
            (NENHUM, "Sem coorientador"),
            (CADASTRADO, "Docente cadastrado"),
            (EXTERNO, "Coorientador externo"),
        )

    tipo_coorientador = forms.ChoiceField(
        choices=TipoCoorientador.choices,
        label="Tipo de coorientador",
    )
    coorientador = forms.ModelChoiceField(
        queryset=User.objects.filter(tipo_usuario=User.TipoUsuario.DOCENTE).order_by("nome"),
        required=False,
        label="Coorientador cadastrado",
    )
    coorientador_externo_nome = forms.CharField(
        max_length=255,
        required=False,
        label="Nome do coorientador externo",
    )
    coorientador_externo_email = forms.EmailField(
        required=False,
        label="Email do coorientador externo",
    )
    coorientador_externo_instituicao = forms.CharField(
        max_length=255,
        required=False,
        label="Instituicao do coorientador externo",
    )

    def clean(self):
        cleaned_data = super().clean()
        tipo_coorientador = cleaned_data.get("tipo_coorientador")
        coorientador = cleaned_data.get("coorientador")
        externo_nome = (cleaned_data.get("coorientador_externo_nome") or "").strip()

        if tipo_coorientador == self.TipoCoorientador.CADASTRADO and not coorientador:
            self.add_error("coorientador", "Selecione um docente cadastrado.")

        if tipo_coorientador == self.TipoCoorientador.EXTERNO and not externo_nome:
            self.add_error("coorientador_externo_nome", "Informe o nome do coorientador externo.")

        return cleaned_data


class TrajetoriaAcademicaForm(AlunoComentarioForm):
    class TipoCoorientador:
        NENHUM = "NENHUM"
        CADASTRADO = "CADASTRADO"
        EXTERNO = "EXTERNO"

        choices = (
            (NENHUM, "Sem coorientador"),
            (CADASTRADO, "Docente cadastrado"),
            (EXTERNO, "Coorientador externo"),
        )

    trajetoria_id = forms.IntegerField(required=False, widget=forms.HiddenInput)
    nivel_curso = forms.ChoiceField(choices=Aluno.NivelCurso.choices, label="Nivel")
    status = forms.ChoiceField(choices=TrajetoriaAcademica.Status.choices, label="Status da trajetoria")
    ingresso = forms.CharField(label="Ingresso (YYYY.1 ou YYYY.2)", max_length=6)
    prazo_qualificacao = forms.CharField(label="Prazo", max_length=6, required=False)
    prazo_defesa = forms.CharField(label="Prazo de defesa", max_length=6, required=False)
    reingressante = forms.BooleanField(required=False, label="Reingressante")
    isQualificado = forms.BooleanField(required=False, label="Projeto/qualificacao concluido")
    orientador = forms.ModelChoiceField(
        queryset=User.objects.filter(tipo_usuario=User.TipoUsuario.DOCENTE).order_by("nome"),
        required=False,
        label="Orientador",
    )
    tipo_coorientador = forms.ChoiceField(choices=TipoCoorientador.choices, label="Tipo de coorientador")
    coorientador = forms.ModelChoiceField(
        queryset=User.objects.filter(tipo_usuario=User.TipoUsuario.DOCENTE).order_by("nome"),
        required=False,
        label="Coorientador cadastrado",
    )
    coorientador_externo_nome = forms.CharField(max_length=255, required=False, label="Nome do coorientador externo")
    coorientador_externo_email = forms.EmailField(required=False, label="Email do coorientador externo")
    coorientador_externo_instituicao = forms.CharField(
        max_length=255,
        required=False,
        label="Instituicao do coorientador externo",
    )
    numero_defesa = forms.CharField(max_length=80, required=False, label="Numero da defesa")
    data_defesa = forms.DateField(required=False, label="Data da defesa", widget=forms.DateInput(attrs={"type": "date"}))
    deposito_versao_final = forms.BooleanField(required=False, label="Deposito da versao final")

    def clean(self):
        cleaned_data = super().clean()
        tipo_coorientador = cleaned_data.get("tipo_coorientador")
        coorientador = cleaned_data.get("coorientador")
        externo_nome = (cleaned_data.get("coorientador_externo_nome") or "").strip()
        status = cleaned_data.get("status")
        numero_defesa = (cleaned_data.get("numero_defesa") or "").strip()
        data_defesa = cleaned_data.get("data_defesa")

        if tipo_coorientador == self.TipoCoorientador.CADASTRADO and not coorientador:
            self.add_error("coorientador", "Selecione um docente cadastrado.")
        if tipo_coorientador == self.TipoCoorientador.EXTERNO and not externo_nome:
            self.add_error("coorientador_externo_nome", "Informe o nome do coorientador externo.")
        if status == TrajetoriaAcademica.Status.CONCLUIDA:
            if not numero_defesa:
                self.add_error("numero_defesa", "Informe o numero da defesa.")
            if not data_defesa:
                self.add_error("data_defesa", "Informe a data da defesa.")

        return cleaned_data


class TrajetoriaStatusForm(AlunoComentarioForm):
    status = forms.ChoiceField(
        choices=(
            (TrajetoriaAcademica.Status.ATIVA, "Ativo"),
            (TrajetoriaAcademica.Status.DESLIGADA, "Desligado"),
            (TrajetoriaAcademica.Status.TRANCADA, "Trancado"),
        ),
        label="Status",
    )
