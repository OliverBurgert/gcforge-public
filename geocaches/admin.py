from django.contrib import admin
from django.utils.html import format_html

from .models import (
    Adventure,
    Attribute,
    CacheFusionRecord,
    CorrectedCoordinates,
    CustomField,
    Geocache,
    Image,
    Log,
    Note,
    OCExtension,
    SavedFilter,
    SavedWhereClause,
    Tag,
    Waypoint,
)


# ---------------------------------------------------------------------------
# Inlines
# ---------------------------------------------------------------------------

class CorrectedCoordinatesInline(admin.StackedInline):
    model = CorrectedCoordinates
    extra = 0
    fields = ("latitude", "longitude", "note")


class WaypointInline(admin.TabularInline):
    model = Waypoint
    extra = 0
    fields = ("waypoint_type", "prefix", "name", "lookup", "latitude", "longitude", "is_user_created", "is_hidden", "is_completed", "is_user_modified")
    show_change_link = True


class LogInline(admin.TabularInline):
    model = Log
    extra = 0
    fields = ("logged_date", "log_type", "source", "user_name", "user_id", "text")
    ordering = ("-logged_date",)
    show_change_link = True
    max_num = 20
    classes = ("collapse",)


class NoteInline(admin.StackedInline):
    model = Note
    extra = 0
    fields = ("note_type", "format", "log_type", "logged_at", "body", "created_at", "updated_at")
    readonly_fields = ("created_at", "updated_at")


class CustomFieldInline(admin.TabularInline):
    model = CustomField
    extra = 1
    fields = ("key", "value")


class ImageInline(admin.TabularInline):
    model = Image
    extra = 0
    fields = ("name", "url", "description")
    readonly_fields = ("preview",)

    def preview(self, obj):
        if obj.url:
            return format_html('<img src="{}" style="max-height:60px;">', obj.url)
        return "-"
    preview.short_description = "Preview"


class OCExtensionInline(admin.StackedInline):
    model = OCExtension
    extra = 0
    fields = (
        "related_gc_code",
        ("rating", "recommendations"),
        ("trip_time", "trip_distance"),
        ("needs_maintenance", "req_passwd"),
        "passphrase",
        "preview_image_url",
    )


class AdventureStageInline(admin.TabularInline):
    model = Geocache
    fk_name = "adventure"
    extra = 0
    fields = ("gc_code", "stage_number", "name", "latitude", "longitude", "found")
    readonly_fields = ("gc_code",)
    ordering = ("stage_number",)
    show_change_link = True
    verbose_name = "Stage"
    verbose_name_plural = "Stages"


# ---------------------------------------------------------------------------
# Adventure admin
# ---------------------------------------------------------------------------

@admin.register(Adventure)
class AdventureAdmin(admin.ModelAdmin):
    list_display = ("code", "title", "owner", "stage_count", "status", "latitude", "longitude")
    list_display_links = ("code", "title")
    search_fields = ("code", "title", "owner", "adventure_guid")
    list_filter = ("status",)
    ordering = ("code",)
    readonly_fields = ("imported_at", "updated_at")

    fieldsets = (
        ("Identity", {
            "fields": (("code", "adventure_guid"), "title", "owner"),
        }),
        ("Location", {
            "fields": (("latitude", "longitude"), "url"),
        }),
        ("Metadata", {
            "fields": (("stage_count", "status"), "themes", "description"),
        }),
        ("Timestamps", {
            "fields": (("imported_at", "updated_at"),),
        }),
    )

    inlines = [AdventureStageInline]


# ---------------------------------------------------------------------------
# Geocache admin
# ---------------------------------------------------------------------------

@admin.register(Geocache)
class GeocacheAdmin(admin.ModelAdmin):
    list_display = (
        "gc_code", "oc_code", "name", "cache_type", "effective_size",
        "status", "difficulty", "terrain", "fav_points",
        "found", "ftf", "dnf", "user_flag", "watch",
        "country", "hidden_date", "adventure",
    )
    list_display_links = ("gc_code", "oc_code", "name")
    list_filter = (
        "cache_type",
        "status",
        "size",
        "found",
        "ftf",
        "dnf",
        "user_flag",
        "watch",
        "is_premium",
        "has_trackable",
        "import_locked",
        "country",
    )
    list_editable = ("user_flag", "watch")
    search_fields = ("gc_code", "oc_code", "name", "owner", "country", "state", "county")
    date_hierarchy = "hidden_date"
    ordering = ("gc_code",)
    filter_horizontal = ("tags", "attributes")
    raw_id_fields = ("parent", "adventure")

    readonly_fields = ("imported_at", "updated_at")

    fieldsets = (
        ("Identity", {
            "fields": (("gc_code", "oc_code"), "name", ("owner", "placed_by", "owner_gc_id"), "parent"),
        }),
        ("Classification", {
            "fields": (
                ("cache_type", "status"),
                ("size", "size_override"),
                ("difficulty", "terrain"),
                "is_premium",
            ),
        }),
        ("Location", {
            "fields": (
                ("latitude", "longitude"),
                ("country", "state", "county"),
                ("elevation", "elevation_user"),
            ),
        }),
        ("Dates", {
            "fields": (("hidden_date", "last_found_date"),),
        }),
        ("Description", {
            "fields": ("short_description", "long_description", "hint"),
            "classes": ("collapse",),
        }),
        ("Platform stats", {
            "fields": (
                ("fav_points", "recommendations", "platform_log_count"),
                ("has_trackable", "needs_maintenance"),
                "primary_source",
            ),
        }),
        ("Personal tracking", {
            "fields": (
                ("found", "found_date", "found_count"),
                ("ftf", "dnf", "dnf_date"),
                ("user_flag", "watch", "color"),
                "user_sort",
                "gc_note",
            ),
        }),
        ("Adventure Lab", {
            "fields": (
                ("adventure", "stage_number"),
                "question_text",
                ("al_stage_uuid", "al_answer_hash"),
                "al_journal_text",
            ),
            "classes": ("collapse",),
        }),
        ("Tags & Attributes", {
            "fields": ("tags", "attributes"),
        }),
        ("Import / sync", {
            "fields": (("import_locked", "last_gpx_date"), ("imported_at", "updated_at")),
        }),
    )

    inlines = [
        CorrectedCoordinatesInline,
        WaypointInline,
        LogInline,
        NoteInline,
        CustomFieldInline,
        ImageInline,
        OCExtensionInline,
    ]

    def effective_size(self, obj):
        return obj.size_override or obj.size
    effective_size.short_description = "Size"


# ---------------------------------------------------------------------------
# Log admin
# ---------------------------------------------------------------------------

@admin.register(Log)
class LogAdmin(admin.ModelAdmin):
    list_display = ("geocache", "log_type", "source", "user_name", "user_id", "logged_date")
    list_filter = ("log_type", "source")
    search_fields = ("geocache__gc_code", "geocache__name", "user_name", "user_id", "text")
    date_hierarchy = "logged_date"
    ordering = ("-logged_date",)
    raw_id_fields = ("geocache",)


# ---------------------------------------------------------------------------
# Waypoint admin
# ---------------------------------------------------------------------------

@admin.register(Waypoint)
class WaypointAdmin(admin.ModelAdmin):
    list_display = ("geocache", "waypoint_type", "name", "lookup", "latitude", "longitude", "is_user_created")
    list_filter = ("waypoint_type", "is_user_created")
    search_fields = ("geocache__gc_code", "geocache__name", "name", "lookup")
    raw_id_fields = ("geocache",)


# ---------------------------------------------------------------------------
# Tag admin
# ---------------------------------------------------------------------------

@admin.register(Tag)
class TagAdmin(admin.ModelAdmin):
    list_display = ("name", "cache_count")
    search_fields = ("name",)

    def cache_count(self, obj):
        return obj.geocaches.count()
    cache_count.short_description = "Caches"


# ---------------------------------------------------------------------------
# Attribute admin
# ---------------------------------------------------------------------------

@admin.register(Attribute)
class AttributeAdmin(admin.ModelAdmin):
    list_display = ("name", "source", "attribute_id", "is_positive")
    list_filter = ("source", "is_positive")
    search_fields = ("name",)
    ordering = ("source", "name")


# ---------------------------------------------------------------------------
# Note admin
# ---------------------------------------------------------------------------

@admin.register(Note)
class NoteAdmin(admin.ModelAdmin):
    list_display = ("geocache", "created_at", "updated_at")
    search_fields = ("geocache__gc_code", "geocache__name", "body")
    raw_id_fields = ("geocache",)
    readonly_fields = ("created_at", "updated_at")


# ---------------------------------------------------------------------------
# SavedFilter / SavedWhereClause admin
# ---------------------------------------------------------------------------

@admin.register(SavedFilter)
class SavedFilterAdmin(admin.ModelAdmin):
    list_display = ("name", "created_at", "updated_at")
    search_fields = ("name",)
    readonly_fields = ("created_at", "updated_at")


@admin.register(SavedWhereClause)
class SavedWhereClauseAdmin(admin.ModelAdmin):
    list_display = ("name", "sql_preview", "created_at", "updated_at")
    search_fields = ("name", "sql")
    readonly_fields = ("created_at", "updated_at")

    def sql_preview(self, obj):
        return obj.sql[:80] + ("…" if len(obj.sql) > 80 else "")
    sql_preview.short_description = "SQL"


# ---------------------------------------------------------------------------
# CacheFusionRecord admin
# ---------------------------------------------------------------------------

@admin.register(CacheFusionRecord)
class CacheFusionRecordAdmin(admin.ModelAdmin):
    list_display = ("gc_code", "oc_code", "auto_linked", "user_decision", "updated_at")
    list_filter = ("auto_linked", "user_decision")
    search_fields = ("gc_code", "oc_code")
    readonly_fields = ("created_at", "updated_at")
    ordering = ("gc_code",)
