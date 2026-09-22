from django.db import models


class TreasureCollection(models.Model):
    """A geocaching.com Digital Treasure collection (read-only, scraped).

    Source: the ``/play/treasure`` website (no partner API exists — see
    ``docs/reference/geocaching-com.md`` §5).  ``criteria`` holds the parsed
    earning rule (cache types / sizes / min favourite points) so we can offer a
    GCForge-DB "candidates" filter equivalent to gc.com's Treasure map.
    """

    collection_id = models.IntegerField(unique=True, help_text="gc.com collection id")
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    image_url = models.CharField(max_length=500, blank=True)
    found = models.IntegerField(default=0)
    total = models.IntegerField(default=0)
    is_completed = models.BooleanField(default=False)
    premium_only = models.BooleanField(default=False)
    start_date = models.DateField(null=True, blank=True)
    # Parsed earning criteria: {"type_ids", "types", "size_ids", "sizes",
    # "min_fp", "raw"}.  Empty when not (yet) fetched or not translatable.
    criteria = models.JSONField(default=dict, blank=True)
    criteria_url = models.CharField(max_length=500, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        # Incomplete (in-progress) first, then by how close to done.
        ordering = ["is_completed", "-found", "name"]

    def __str__(self) -> str:
        return self.name

    @property
    def progress_pct(self) -> int:
        return round(self.found / self.total * 100) if self.total else 0

    @property
    def has_candidates(self) -> bool:
        """True when we can offer a candidates filter (incomplete + parsed criteria)."""
        return not self.is_completed and bool(self.criteria.get("type_ids") or
                                              self.criteria.get("size_ids") or
                                              self.criteria.get("min_fp"))
