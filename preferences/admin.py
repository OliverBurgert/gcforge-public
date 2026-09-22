from django.contrib import admin

from .models import ColumnPreset, GpxExportPreset, LogTemplate, OfflineMapArea, ReferencePoint, UserPreference


@admin.register(UserPreference)
class UserPreferenceAdmin(admin.ModelAdmin):
    list_display = ("key", "value_preview")
    search_fields = ("key",)
    ordering = ("key",)

    def value_preview(self, obj):
        return obj.value[:80] + ("…" if len(obj.value) > 80 else "")
    value_preview.short_description = "Value"


@admin.register(ReferencePoint)
class ReferencePointAdmin(admin.ModelAdmin):
    list_display = ("name", "latitude", "longitude", "is_default", "is_home", "valid_from", "note")
    list_filter = ("is_default", "is_home")
    search_fields = ("name",)
    ordering = ("name", "-valid_from")


@admin.register(ColumnPreset)
class ColumnPresetAdmin(admin.ModelAdmin):
    list_display = ("name", "is_builtin")
    list_filter = ("is_builtin",)
    search_fields = ("name",)
    ordering = ("name",)


@admin.register(GpxExportPreset)
class GpxExportPresetAdmin(admin.ModelAdmin):
    list_display = ("name",)
    search_fields = ("name",)
    ordering = ("name",)


@admin.register(LogTemplate)
class LogTemplateAdmin(admin.ModelAdmin):
    list_display = ("name", "scope", "is_default")
    list_filter = ("scope", "is_default")
    search_fields = ("name", "body")
    ordering = ("scope", "name")


@admin.register(OfflineMapArea)
class OfflineMapAreaAdmin(admin.ModelAdmin):
    list_display = ("name", "status", "progress", "tile_count", "file_size_bytes", "min_zoom", "max_zoom", "created_at", "last_downloaded_at")
    list_filter = ("status",)
    search_fields = ("name",)
    readonly_fields = ("created_at", "last_downloaded_at")
    ordering = ("name",)
