from django.contrib import admin
from django.utils.html import format_html

from .models import (
    Adventure,
    ALJournalEntry,
    ALReview,
    ALStageDetail,
    Attribute,
    CacheFusionRecord,
    CachedImage,
    CacheMapState,
    CacheTrackableMention,
    CorrectedCoordinates,
    CustomField,
    DistanceCache,
    GCNotification,
    Geocache,
    IgnoreListEntry,
    Image,
    Log,
    Note,
    OCExtension,
    OCNotification,
    SavedAreaFilter,
    SavedFilter,
    SavedWhereClause,
    Souvenir,
    SouvenirTag,
    SyncQuota,
    SyncState,
    Tag,
    Trackable,
    TrackableImage,
    TrackableLog,
    TreasureCollection,
    Waypoint,
)


# ---------------------------------------------------------------------------
# Inlines
# ---------------------------------------------------------------------------

class CorrectedCoordinatesInline(admin.StackedInline):
    model = CorrectedCoordinates
    extra = 0
    fields = ("latitude", "longitude", "note", "updated_at")
    readonly_fields = ("updated_at",)


class WaypointInline(admin.TabularInline):
    model = Waypoint
    extra = 0
    fields = ("waypoint_type", "prefix", "name", "lookup", "latitude", "longitude", "is_user_created", "is_hidden", "is_completed", "is_user_modified")
    show_change_link = True


class LogInline(admin.TabularInline):
    model = Log
    extra = 0
    fields = ("logged_date", "logged_at", "log_type", "source", "user_name", "user_id", "source_id", "sequence_number", "is_local", "text")
    ordering = ("-logged_date",)
    show_change_link = True
    max_num = 20
    classes = ("collapse",)


class NoteInline(admin.StackedInline):
    model = Note
    extra = 0
    fields = (
        "note_type", "format", "log_type", "logged_at", "body",
        "draft_body", "submitted_at",
        ("bulk_draft", "bulk_dismissed"),
        "submit_error", "sequence_number",
        "created_at", "updated_at",
    )
    readonly_fields = ("created_at", "updated_at")


class CustomFieldInline(admin.TabularInline):
    model = CustomField
    extra = 1
    fields = ("key", "value")


class ImageInline(admin.TabularInline):
    model = Image
    extra = 0
    fields = ("name", "url", "description", "preview")
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
        ("rating", "recommendations", "user_recommended"),
        ("trip_time", "trip_distance"),
        ("needs_maintenance", "req_passwd"),
        "passphrase",
        "preview_image_url",
        "attribution_html",
        ("short_description", "long_description"),
    )


class ALStageDetailInline(admin.StackedInline):
    model = ALStageDetail
    extra = 0
    fields = (
        ("stage_number", "al_stage_uuid"),
        ("challenge_type", "is_final"),
        "question_text",
        ("answer_hash", "answer_is_correct"),
        "answer_choices",
        "answer_code_hashes",
        "key_image_url",
        "geofencing_radius",
        "user_answer",
        "updated_at",
    )
    readonly_fields = ("updated_at",)
    classes = ("collapse",)


class ALJournalEntryInline(admin.StackedInline):
    model = ALJournalEntry
    extra = 0
    fields = ("journal_message", "journal_image_url", "updated_at")
    readonly_fields = ("updated_at",)
    classes = ("collapse",)


class SyncStateInline(admin.TabularInline):
    model = SyncState
    extra = 0
    fields = ("platform", "last_synced", "last_modified", "sync_mode", "sync_error")
    readonly_fields = ("last_synced", "last_modified")
    classes = ("collapse",)


class AdventureStageInline(admin.TabularInline):
    model = Geocache
    fk_name = "adventure"
    extra = 0
    fields = ("al_code", "name", "latitude", "longitude", "found")
    readonly_fields = ("al_code",)
    ordering = ("al_code",)
    show_change_link = True
    verbose_name = "Stage"
    verbose_name_plural = "Stages"


class ALReviewInline(admin.TabularInline):
    model = ALReview
    extra = 0
    fields = ("review_id", "player_username", "rating", "recommended", "is_creator", "review_text", "created_utc")
    readonly_fields = ("review_id", "created_utc")
    ordering = ("-created_utc",)
    max_num = 20
    classes = ("collapse",)


class TrackableLogInline(admin.TabularInline):
    model = TrackableLog
    extra = 0
    fields = ("logged_date", "log_type", "user_name", "geocache_ref_code", "is_local", "text")
    ordering = ("-logged_date",)
    show_change_link = True
    max_num = 20
    classes = ("collapse",)


class TrackableImageInline(admin.TabularInline):
    model = TrackableImage
    fk_name = "trackable"
    extra = 0
    fields = ("source_id", "caption", "url", "uploaded_at")
    readonly_fields = ("uploaded_at",)
    classes = ("collapse",)


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
        ("Extended AL data", {
            "fields": (
                ("adventure_type", "smart_link"),
                "key_image_url",
                "published_utc",
                ("median_time_to_complete", "ratings_average", "ratings_total_count"),
                ("is_highly_recommended", "completion_date"),
                "owner_public_guid",
            ),
            "classes": ("collapse",),
        }),
        ("Timestamps", {
            "fields": (("imported_at", "updated_at"),),
        }),
    )

    inlines = [AdventureStageInline, ALReviewInline]


# ---------------------------------------------------------------------------
# ALReview admin
# ---------------------------------------------------------------------------

@admin.register(ALReview)
class ALReviewAdmin(admin.ModelAdmin):
    list_display = ("review_id", "adventure", "player_username", "rating", "recommended", "is_creator", "created_utc")
    list_filter = ("recommended", "is_creator")
    search_fields = ("player_username", "review_text")
    raw_id_fields = ("adventure",)
    readonly_fields = ("created_utc", "modified_utc")
    date_hierarchy = "created_utc"
    ordering = ("-created_utc",)


# ---------------------------------------------------------------------------
# ALStageDetail admin
# ---------------------------------------------------------------------------

@admin.register(ALStageDetail)
class ALStageDetailAdmin(admin.ModelAdmin):
    list_display = ("geocache", "stage_number", "challenge_type", "is_final", "answer_is_correct")
    list_filter = ("challenge_type", "is_final", "answer_is_correct")
    search_fields = ("geocache__gc_code", "geocache__al_code", "geocache__name", "al_stage_uuid")
    raw_id_fields = ("geocache",)
    readonly_fields = ("updated_at",)


# ---------------------------------------------------------------------------
# ALJournalEntry admin
# ---------------------------------------------------------------------------

@admin.register(ALJournalEntry)
class ALJournalEntryAdmin(admin.ModelAdmin):
    list_display = ("geocache", "updated_at")
    search_fields = ("geocache__gc_code", "geocache__al_code", "geocache__name")
    raw_id_fields = ("geocache",)
    readonly_fields = ("updated_at",)


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
        "is_placeholder",
        "map_hidden_always",
        "country",
    )
    list_editable = ("user_flag", "watch")
    search_fields = ("gc_code", "oc_code", "al_code", "name", "owner", "country", "state", "county")
    date_hierarchy = "hidden_date"
    ordering = ("gc_code",)
    filter_horizontal = ("tags", "attributes")
    raw_id_fields = ("parent", "adventure")
    readonly_fields = ("imported_at", "updated_at")

    fieldsets = (
        ("Identity", {
            "fields": (("gc_code", "oc_code", "al_code"), "name", ("owner", "placed_by", "owner_gc_id"), "parent"),
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
                ("iso_country_code", "elevation", "elevation_user"),
                "has_corrected_coordinates",
            ),
        }),
        ("Dates", {
            "fields": (
                ("hidden_date", "last_found_date"),
                ("event_start_time", "event_end_time"),
            ),
        }),
        ("Description", {
            "fields": ("short_description", "long_description", "hint"),
            "classes": ("collapse",),
        }),
        ("Platform stats", {
            "fields": (
                ("fav_points", "user_favorited"),
                ("recommendations", "platform_log_count"),
                ("has_trackable", "needs_maintenance"),
                ("primary_source", "background_image_url"),
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
                ("adventure", "completed"),
            ),
            "classes": ("collapse",),
        }),
        ("Tags & Attributes", {
            "fields": ("tags", "attributes"),
        }),
        ("Import / sync", {
            "fields": (
                ("import_locked", "last_gpx_date"),
                ("is_placeholder", "map_hidden_always"),
                ("imported_at", "updated_at"),
            ),
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
        ALStageDetailInline,
        ALJournalEntryInline,
        SyncStateInline,
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
    list_display = ("name", "default_ref_point", "cache_count")
    search_fields = ("name",)
    raw_id_fields = ("default_ref_point",)

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
    list_display = ("geocache", "note_type", "created_at", "updated_at")
    list_filter = ("note_type", "bulk_draft", "bulk_dismissed")
    search_fields = ("geocache__gc_code", "geocache__name", "body")
    raw_id_fields = ("geocache",)
    readonly_fields = ("created_at", "updated_at")


# ---------------------------------------------------------------------------
# SavedFilter / SavedWhereClause admin
# ---------------------------------------------------------------------------

@admin.register(SavedFilter)
class SavedFilterAdmin(admin.ModelAdmin):
    list_display = ("name", "is_builtin", "created_at", "updated_at")
    list_filter = ("is_builtin",)
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


# ---------------------------------------------------------------------------
# IgnoreListEntry admin
# ---------------------------------------------------------------------------

@admin.register(IgnoreListEntry)
class IgnoreListEntryAdmin(admin.ModelAdmin):
    list_display = ("code", "source", "oc_platform", "status", "last_status_refresh", "added")
    search_fields = ("code", "name", "notes")
    list_filter = ("source", "oc_platform", "status")
    readonly_fields = ("added", "updated")
    ordering = ("-added",)


# ---------------------------------------------------------------------------
# SyncState / SyncQuota admin
# ---------------------------------------------------------------------------

@admin.register(SyncState)
class SyncStateAdmin(admin.ModelAdmin):
    list_display = ("geocache", "platform", "last_synced", "last_modified", "sync_mode", "sync_error_preview")
    list_filter = ("platform", "sync_mode")
    search_fields = ("geocache__gc_code", "geocache__name")
    raw_id_fields = ("geocache",)
    readonly_fields = ("last_synced", "last_modified")

    def sync_error_preview(self, obj):
        if obj.sync_error:
            return obj.sync_error[:60] + ("…" if len(obj.sync_error) > 60 else "")
        return ""
    sync_error_preview.short_description = "Error"


@admin.register(SyncQuota)
class SyncQuotaAdmin(admin.ModelAdmin):
    list_display = ("platform", "mode", "date", "used", "limit")
    list_filter = ("platform", "mode")
    date_hierarchy = "date"
    ordering = ("-date", "platform")


# ---------------------------------------------------------------------------
# CachedImage admin
# ---------------------------------------------------------------------------

@admin.register(CachedImage)
class CachedImageAdmin(admin.ModelAdmin):
    list_display = ("category", "filename", "mime_type", "bytes", "downloaded_at", "last_seen_at", "thumbnail")
    list_filter = ("category",)
    search_fields = ("source_url", "filename")
    readonly_fields = ("downloaded_at", "last_seen_at", "thumbnail")
    filter_horizontal = ("linked_geocaches", "linked_trackables", "linked_adventures", "linked_logs", "linked_trackable_logs")

    def thumbnail(self, obj):
        if obj.source_url:
            return format_html('<img src="{}" style="max-height:60px;">', obj.source_url)
        return "-"
    thumbnail.short_description = "Preview"


# ---------------------------------------------------------------------------
# DistanceCache admin
# ---------------------------------------------------------------------------

@admin.register(DistanceCache)
class DistanceCacheAdmin(admin.ModelAdmin):
    list_display = ("geocache", "ref_point", "distance_km", "bearing_deg")
    list_filter = ("ref_point",)
    raw_id_fields = ("geocache",)
    ordering = ("ref_point", "distance_km")


# ---------------------------------------------------------------------------
# CacheMapState admin
# ---------------------------------------------------------------------------

@admin.register(CacheMapState)
class CacheMapStateAdmin(admin.ModelAdmin):
    list_display = ("geocache", "zoom", "lat", "lon", "updated_at")
    search_fields = ("geocache__gc_code", "geocache__name")
    raw_id_fields = ("geocache",)
    readonly_fields = ("updated_at",)


# ---------------------------------------------------------------------------
# SavedAreaFilter admin
# ---------------------------------------------------------------------------

@admin.register(SavedAreaFilter)
class SavedAreaFilterAdmin(admin.ModelAdmin):
    list_display = ("name", "created_at")
    search_fields = ("name",)
    readonly_fields = ("created_at",)


# ---------------------------------------------------------------------------
# Souvenir admin
# ---------------------------------------------------------------------------

@admin.register(Souvenir)
class SouvenirAdmin(admin.ModelAdmin):
    list_display = ("title", "tag_list", "found_date", "gc_id", "account")
    list_filter = ("tags", "account")
    search_fields = ("title", "description", "gc_id")
    readonly_fields = ("created_at", "updated_at")
    filter_horizontal = ("tags",)
    date_hierarchy = "found_date"
    ordering = ("-found_date",)

    @admin.display(description="Tags")
    def tag_list(self, obj):
        return ", ".join(t.name for t in obj.tags.all())


@admin.register(TreasureCollection)
class TreasureCollectionAdmin(admin.ModelAdmin):
    list_display = ("name", "collection_id", "found", "total", "is_completed",
                    "premium_only", "updated_at")
    list_filter = ("is_completed", "premium_only")
    search_fields = ("name", "collection_id")
    readonly_fields = ("updated_at",)
    ordering = ("is_completed", "name")


@admin.register(SouvenirTag)
class SouvenirTagAdmin(admin.ModelAdmin):
    list_display = ("name", "souvenir_count")
    search_fields = ("name",)
    ordering = ("name",)

    @admin.display(description="Souvenirs")
    def souvenir_count(self, obj):
        return obj.souvenirs.count()


# ---------------------------------------------------------------------------
# GCNotification admin
# ---------------------------------------------------------------------------

@admin.register(GCNotification)
class GCNotificationAdmin(admin.ModelAdmin):
    list_display = ("name", "location", "latitude", "longitude", "radius_km", "cache_type_id", "enabled", "created_at")
    list_filter = ("enabled", "cache_type_id")
    search_fields = ("name", "server_id")
    raw_id_fields = ("location",)
    readonly_fields = ("created_at", "updated_at", "last_synced_at", "server_hash")
    ordering = ("location__name", "name")

    fieldsets = (
        ("Identity", {
            "fields": (("server_id", "source"), "name"),
        }),
        ("Location", {
            "fields": (("latitude", "longitude"), ("radius_km", "location")),
        }),
        ("Notification Settings", {
            "fields": (("cache_type_id", "enabled"), "log_event_ids", "recipient_email"),
        }),
        ("Notes & Sync", {
            "fields": ("notes", ("last_synced_at", "server_hash")),
            "classes": ("collapse",),
        }),
        ("Timestamps", {
            "fields": (("created_at", "updated_at"),),
        }),
    )


# ---------------------------------------------------------------------------
# OCNotification admin
# ---------------------------------------------------------------------------

@admin.register(OCNotification)
class OCNotificationAdmin(admin.ModelAdmin):
    list_display = ("platform", "server_id", "name", "location", "latitude", "longitude", "radius_km", "enabled", "created_at")
    list_filter = ("platform", "enabled")
    search_fields = ("server_id", "name", "location__name")
    raw_id_fields = ("location",)
    readonly_fields = ("created_at", "updated_at", "last_synced_at")
    ordering = ("platform", "server_id")

    fieldsets = (
        ("Identity", {
            "fields": (("platform", "server_id"), "name"),
        }),
        ("Location", {
            "fields": (("latitude", "longitude"), ("radius_km", "location")),
        }),
        ("Notification Settings", {
            "fields": ("enabled", ("notify_oconly", "notify_logs"), "frequency"),
        }),
        ("Sync", {
            "fields": ("last_synced_at",),
            "classes": ("collapse",),
        }),
        ("Timestamps", {
            "fields": (("created_at", "updated_at"),),
        }),
    )


# ---------------------------------------------------------------------------
# Trackable admin
# ---------------------------------------------------------------------------

@admin.register(Trackable)
class TrackableAdmin(admin.ModelAdmin):
    list_display = ("reference_code", "name", "kind", "holder_state", "owner_name", "last_log_date", "is_active", "is_archived")
    list_filter = ("kind", "holder_state", "is_active", "is_archived", "is_collectible", "auto_visit_enabled")
    search_fields = ("reference_code", "name", "owner_name", "tracking_code")
    raw_id_fields = ("current_geocache",)
    filter_horizontal = ("tags",)
    readonly_fields = ("imported_at", "updated_at")
    ordering = ("name",)

    fieldsets = (
        ("Identity", {
            "fields": (("reference_code", "tracking_code"), "name", "series", ("kind", "owner_name", "owner_gc_id"), "icon_url"),
        }),
        ("Status", {
            "fields": (("is_active", "is_archived", "is_collectible"), ("released_date", "origin")),
        }),
        ("Description", {
            "fields": ("goal", "about"),
            "classes": ("collapse",),
        }),
        ("Location", {
            "fields": (
                "holder_state",
                ("current_geocache", "current_geocache_code", "current_geocache_name"),
                "current_holder_name",
                ("current_lat", "current_lon", "coords_user_override"),
            ),
        }),
        ("Stats", {
            "fields": (("total_distance_km", "total_visits", "last_log_date"),),
        }),
        ("Auto-visit", {
            "fields": ("auto_visit_enabled", "auto_visit_text"),
            "classes": ("collapse",),
        }),
        ("Tags & Timestamps", {
            "fields": ("tags", ("imported_at", "updated_at")),
        }),
    )

    inlines = [TrackableLogInline, TrackableImageInline]


# ---------------------------------------------------------------------------
# TrackableLog admin
# ---------------------------------------------------------------------------

@admin.register(TrackableLog)
class TrackableLogAdmin(admin.ModelAdmin):
    list_display = ("trackable", "log_type", "logged_date", "user_name", "geocache_ref_code", "is_local")
    list_filter = ("log_type", "is_local")
    search_fields = ("trackable__reference_code", "trackable__name", "user_name", "geocache_ref_code")
    raw_id_fields = ("trackable", "geocache")
    date_hierarchy = "logged_date"
    ordering = ("-logged_date",)


# ---------------------------------------------------------------------------
# TrackableImage admin
# ---------------------------------------------------------------------------

@admin.register(TrackableImage)
class TrackableImageAdmin(admin.ModelAdmin):
    list_display = ("trackable", "log", "source_id", "caption", "uploaded_at")
    search_fields = ("trackable__reference_code", "source_id", "caption")
    raw_id_fields = ("trackable", "log")
    readonly_fields = ("uploaded_at",)


# ---------------------------------------------------------------------------
# CacheTrackableMention admin
# ---------------------------------------------------------------------------

@admin.register(CacheTrackableMention)
class CacheTrackableMentionAdmin(admin.ModelAdmin):
    list_display = ("geocache", "ref_code", "name", "gc_id", "last_seen_at")
    search_fields = ("ref_code", "name", "geocache__gc_code")
    raw_id_fields = ("geocache",)
    readonly_fields = ("last_seen_at",)
