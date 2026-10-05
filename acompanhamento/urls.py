from django.urls import path

from . import views

app_name = "acompanhamento"

urlpatterns = [
    # O nome da rota é único no projeto porque o menu lateral acende o item
    # ativo comparando só o nome da rota, sem o prefixo do módulo.
    path("", views.inicio, name="acompanhamento_inicio"),
    path("docentes/", views.painel_docentes, name="painel_docentes"),
]