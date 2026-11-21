from django.contrib import admin



from .models import MarcoCalendario, PlanilhaBaixada


@admin.register(MarcoCalendario)
class MarcoCalendarioAdmin(admin.ModelAdmin):
    list_display = ("semestre", "tipo", "data_limite", "alterado_por", "alterado_em")
    list_filter = ("tipo", "semestre")
    search_fields = ("semestre",)
    ordering = ("-semestre", "tipo")
    readonly_fields = ("alterado_por", "alterado_em")
   
    def save_model(self, request, obj, form, change):
        obj.alterado_por = request.user
        super().save_model(request, obj, form, change)


@admin.register(PlanilhaBaixada)
class PlanilhaBaixadaAdmin(admin.ModelAdmin):
    list_display = ("baixada_por", "baixada_em", "filtros")
    list_filter = ("baixada_em",)
    search_fields = ("baixada_por__email", "baixada_por__nome")
    readonly_fields = ("baixada_por", "baixada_em", "filtros")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False