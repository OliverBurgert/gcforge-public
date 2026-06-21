from django.db import models


class OCExtension(models.Model):
    geocache = models.OneToOneField(
        "geocaches.Geocache", on_delete=models.CASCADE, related_name="oc_extension"
    )
    rating = models.FloatField(null=True, blank=True)
    recommendations = models.IntegerField(null=True, blank=True)
    needs_maintenance = models.BooleanField(default=False)
    trip_time = models.FloatField(null=True, blank=True)
    trip_distance = models.FloatField(null=True, blank=True)
    user_recommended = models.BooleanField(null=True, blank=True)  # True if current user gave an OC recommendation
    req_passwd = models.BooleanField(default=False)
    passphrase = models.TextField(blank=True, default="")  # user-stored passphrase for req_passwd caches
    preview_image_url = models.URLField(max_length=500, blank=True)
    attribution_html = models.TextField(blank=True)       # OC copyright notice (mandatory per OC ToS)
    long_description = models.TextField(blank=True)       # OC description preserved when GC overwrites main
    short_description = models.TextField(blank=True)      # OC short description preserved when GC overwrites main
    # GC code as stated by the OC platform (owner-confirmed cross-reference).
    # Populated from oc:other_code in GPX imports and gc_code in OKAPI responses.
    # Preserved even when the pair is not yet fused.
    related_gc_code = models.CharField(max_length=20, blank=True)

    def __str__(self):
        return f"OC data for {self.geocache}"
