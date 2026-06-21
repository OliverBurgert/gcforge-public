from django.db import models


class Tag(models.Model):
    name = models.CharField(max_length=100, unique=True)
    default_ref_point = models.ForeignKey(
        'preferences.ReferencePoint',
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='default_for_tags',
    )

    def __str__(self):
        return self.name


class Attribute(models.Model):
    class Source(models.TextChoices):
        GC = "gc", "geocaching.com"
        OC = "oc", "opencaching.de"

    source = models.CharField(max_length=2, choices=Source, default=Source.GC)
    attribute_id = models.IntegerField()
    name = models.CharField(max_length=100)
    is_positive = models.BooleanField(default=True)

    class Meta:
        unique_together = ("source", "attribute_id", "is_positive")

    def __str__(self):
        return f"{self.name} ({'yes' if self.is_positive else 'no'})"
