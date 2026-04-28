from django import forms
from django.contrib.auth import get_user_model

from .models import Aluno, Documento, Processo, Setor


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
        fields = ["tipo", "assunto", "descricao", "prioridade"]
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


class AlunoQualificacaoForm(AlunoComentarioForm):
    isQualificado = forms.BooleanField(required=False, label="Aluno qualificado")


class AlunoPrazoForm(AlunoComentarioForm):
    valor_semestre = forms.CharField(
        label="Semestre (YYYY.1 ou YYYY.2)",
        max_length=6,
    )


class AlunoDefesaForm(AlunoComentarioForm):
    numero_defesa = forms.CharField(label="Numero da defesa", max_length=80)
    data_defesa = forms.DateField(
        label="Data da defesa",
        widget=forms.DateInput(attrs={"type": "date"}),
    )


class AlunoDepositoFinalForm(AlunoComentarioForm):
    deposito_versao_final = forms.BooleanField(required=False, label="Deposito da versao final")
