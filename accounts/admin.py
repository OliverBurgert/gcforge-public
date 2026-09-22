from django.contrib import admin

from .models import UserAccount


@admin.register(UserAccount)
class UserAccountAdmin(admin.ModelAdmin):
    list_display = ("get_label", "platform", "user_id", "username", "is_default", "membership_level")
    list_filter = ("platform", "is_default")
    search_fields = ("username", "user_id", "label", "notes")
    ordering = ("platform", "username")
