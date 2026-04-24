"""
URL configuration for ppgec project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.conf import settings
from django.conf.urls.static import static
from django.urls import path
from django.contrib.auth.views import LoginView, LogoutView

from processos.views import (
    aluno_documento_historico_view,
    aluno_documento_vinculo_view,
    caixa_processos_view,
    coordenacao_dashboard_view,
    home_view,
    menu_processos_pleno_view,
    menu_meus_orientandos_view,
    menu_meus_processos_view,
    menu_processos_orientandos_view,
    menu_ciencias_manifestadas_view,
    me_view,
    novo_processo_view,
    processo_detalhe_view,
    processos_view,
)

urlpatterns = [
    path("", home_view, name="home"),
    path("login/", LoginView.as_view(template_name="registration/login.html"), name="login"),
    path("logout/", LogoutView.as_view(), name="logout"),
    path("me/", me_view, name="me"),
    path("menu/meus-processos/", menu_meus_processos_view, name="menu_meus_processos"),
    path("menu/processos-pleno/", menu_processos_pleno_view, name="menu_processos_pleno"),
    path("menu/processos-orientandos/", menu_processos_orientandos_view, name="menu_processos_orientandos"),
    path("menu/ciencias-manifestadas/", menu_ciencias_manifestadas_view, name="menu_ciencias_manifestadas"),
    path("menu/meus-orientandos/", menu_meus_orientandos_view, name="menu_meus_orientandos"),
    path("aluno/documento-vinculo/", aluno_documento_vinculo_view, name="aluno_documento_vinculo"),
    path("aluno/documento-historico/", aluno_documento_historico_view, name="aluno_documento_historico"),
    path("processos/novo/", novo_processo_view, name="novo_processo"),
    path("coordenacao/dashboard/", coordenacao_dashboard_view, name="coordenacao_dashboard"),
    path("coordenacao/processos/", processos_view, name="coordenacao_processos"),
    path("coordenacao/processos/<int:processo_id>/", processo_detalhe_view, name="processo_detalhe"),
    path("coordenacao/caixa-processos/", caixa_processos_view, name="coordenacao_caixa_processos"),
    path('admin/', admin.site.urls),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
