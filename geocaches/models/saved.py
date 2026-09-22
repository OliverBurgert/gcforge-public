from django.db import models


class SavedFilter(models.Model):
    """Named filter, stored as a v2 filter-expression tree.

    Legacy ``params`` JSONField removed in migration 0025; built-in
    entries were rewritten with explicit ``tree`` values, user entries
    re-backfilled from their pre-removal ``params`` via
    ``legacy_params_to_tree``.  ``?f=<name>`` URL resolution loads ``tree``
    directly.
    """
    name = models.CharField(max_length=100, unique=True)
    tree = models.JSONField()
    is_builtin = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["is_builtin", "name"]

    def __str__(self):
        return self.name


class SavedWhereClause(models.Model):
    name = models.CharField(max_length=100, blank=True)  # blank = recent/unnamed
    sql = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]

    def __str__(self):
        return self.name or f"Recent: {self.sql[:50]}"

    @classmethod
    def add_recent(cls, sql: str) -> None:
        """Record sql as most-recently-used; keep at most 10 unnamed entries."""
        from django.utils import timezone
        sql = sql.strip()
        if not sql:
            return
        # Don't duplicate an existing named entry
        if cls.objects.filter(name__gt="", sql=sql).exists():
            return
        obj, created = cls.objects.get_or_create(name="", sql=sql)
        if not created:
            cls.objects.filter(pk=obj.pk).update(updated_at=timezone.now())
        # Prune oldest unnamed beyond 10
        keep_ids = list(
            cls.objects.filter(name="").order_by("-updated_at").values_list("pk", flat=True)[:10]
        )
        cls.objects.filter(name="").exclude(pk__in=keep_ids).delete()


class CacheMapState(models.Model):
    """Persists the last map zoom/pan position for a cache's detail view."""
    geocache = models.OneToOneField(
        "geocaches.Geocache", on_delete=models.CASCADE, related_name="map_state"
    )
    zoom = models.SmallIntegerField()
    lat = models.FloatField()
    lon = models.FloatField()
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Map state for {self.geocache}"


class SavedAreaFilter(models.Model):
    """Named geographic area filter (union of rectangles and circles)."""
    name = models.CharField(max_length=100, unique=True)
    # [{"type":"rect","bbox":[s,w,n,e]}, {"type":"circle","center":[lat,lon],"radius_m":5000}]
    regions = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class SavedRoute(models.Model):
    """Named route — ordered waypoints plus the travel profile and corridor width.

    ``path`` caches the routed geometry so loading a saved route redraws its
    corridor immediately, without a fresh BRouter round-trip; it may be empty if
    the route was saved before it was ever computed.
    """
    name = models.CharField(max_length=100, unique=True)
    # [{"lat":..,"lon":..,"label":..,"kind":..,"code":..}, ...]
    waypoints = models.JSONField()
    profile = models.CharField(max_length=40, default="hiking-beta")
    width_m = models.IntegerField(default=1000)
    path = models.JSONField(default=list, blank=True)  # [[lon,lat], ...]
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name
