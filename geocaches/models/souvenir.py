from django.db import models


class SouvenirTag(models.Model):
    """User-managed tag for souvenirs (dedicated — not the cache Tag model).

    Users create their own tags (e.g. "difficult", "lovely artwork"). A
    "Countries" tag is seeded by migration and auto-applied to country-named
    souvenirs on first import.
    """
    name = models.CharField(max_length=100, unique=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class Souvenir(models.Model):
    """A geocaching.com souvenir earned by the user.

    Source: ``GET /v1/users/me/souvenirs`` (only id/title/description/image/
    foundDate/url — see ``docs/reference/geocaching-com.md`` §4).  Categorisation
    is via user-managed :class:`SouvenirTag`s rather than a fixed field.
    ``extra`` keeps any future API fields without needing a migration.
    """

    account = models.ForeignKey(
        "accounts.UserAccount", on_delete=models.SET_NULL,
        null=True, blank=True, related_name="souvenirs",
    )
    gc_id = models.IntegerField(unique=True, help_text="GC API souvenir id")
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    image_path = models.CharField(max_length=500, blank=True)
    thumb_image_path = models.CharField(max_length=500, blank=True)
    url = models.CharField(max_length=500, blank=True)
    found_date = models.DateTimeField(null=True, blank=True)
    tags = models.ManyToManyField(SouvenirTag, blank=True, related_name="souvenirs")
    extra = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-found_date", "title"]

    def __str__(self) -> str:
        return self.title

    @property
    def found_year(self):
        return self.found_date.year if self.found_date else None
