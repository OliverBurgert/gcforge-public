"""Shared service helpers for the preferences app."""

from dataclasses import dataclass, field


@dataclass
class ResolvedReferencePoint:
    """Resolved active reference point with metadata.

    *source* indicates how the reference point was chosen:
      "selected"         — explicit ?ref=<pk> query param, matched a record
      "default"          — picked from the default-flagged or first record
      "coords"           — reserved for future lat/lon query-param override
      "current_position" — reserved for future browser geolocation
      None               — no reference points exist
    """
    lat: float | None
    lon: float | None
    label: str
    source: str | None
    ref_point: object = field(repr=False, default=None)  # ReferencePoint model instance


def resolve_active_reference_point(request, ref_points=None) -> ResolvedReferencePoint:
    """Return the active reference point for *request*.

    Pass a pre-fetched *ref_points* list to avoid an extra DB query when the
    caller already holds one (e.g. cache_list which also needs the list for
    the template dropdown).
    """
    from preferences.models import ReferencePoint

    if ref_points is None:
        ref_points = list(ReferencePoint.objects.all())

    ref_id = request.GET.get("ref", "")
    if ref_id:
        ref = next((r for r in ref_points if str(r.pk) == ref_id), None)
        source = "selected" if ref else None
    else:
        ref = next((r for r in ref_points if r.is_default), None) or (ref_points[0] if ref_points else None)
        source = "default" if ref else None

    if ref:
        return ResolvedReferencePoint(
            lat=ref.latitude, lon=ref.longitude, label=ref.name,
            source=source, ref_point=ref,
        )
    return ResolvedReferencePoint(lat=None, lon=None, label="", source=None, ref_point=None)
