from django import forms
from django.contrib.auth import get_user_model

from .models import Aluno, Documento, Processo, Setor, TrajetoriaAcademica, EstagioDocencia


User = get_user_model()


class UserProfileForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ["nome", "email"]


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

    def __init__(self, *args, current_setor_id=None, allowed_setor_ids=None, **kwargs):
        super().__init__(*args, **kwargs)
        queryset = Setor.objects.filter(ativo=True).order_by("nome")
        if allowed_setor_ids is not None:
            queryset = queryset.filter(id__in=allowed_setor_ids)
        if current_setor_id:
            queryset = queryset.exclude(id=current_setor_id)
        self.fields["setor_destino"].queryset = queryset


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


class EstagioDocenciaForm(AlunoComentarioForm):
   
    estagio_id = forms.IntegerField(widget=forms.HiddenInput())
    supervisor = forms.CharField(max_length=255, label="Supervisor")


class EstagioDocenciaEdicaoForm(AlunoComentarioForm):
    
    estagio_id = forms.IntegerField(widget=forms.HiddenInput())
    supervisor = forms.CharField(max_length=255, label="Supervisor")
    

    status = forms.ChoiceField(
        choices=EstagioDocencia.Status.choices, 
        label="Status do Estágio"
    )
    
    data_inicio = forms.DateField(
        label="Data de Início", 
        widget=forms.DateInput(attrs={"type": "date"})
    )
    data_termino = forms.DateField(
        label="Data de Término", 
        widget=forms.DateInput(attrs={"type": "date"})
    )

    def clean(self):
        cleaned_data = super().clean()
        data_inicio = cleaned_data.get("data_inicio")
        data_termino = cleaned_data.get("data_termino")

        # Validação lógica: data de término não pode ser anterior ao início
        if data_inicio and data_termino:
            if data_termino < data_inicio:
                self.add_error(
                    "data_termino", 
                    "A data de término não pode ser anterior à data de início."
                )

        return cleaned_data