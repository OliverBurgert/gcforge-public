from django.contrib import admin
from .models import ColumnPreset, ReferencePoint, UserPreference

admin.site.register(UserPreference)
admin.site.register(ReferencePoint)
admin.site.register(ColumnPreset)
