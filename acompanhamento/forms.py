from django import forms
from .models import MarcoCalendario

class MarcoCalendarioForm(forms.ModelForm):
    class Meta:
        model = MarcoCalendario
        fields = ["tipo", "semestre", "data_limite"]
        widgets = {
            "data_limite": forms.DateInput(attrs={"type": "date", "class": "form-control"}),
        }
