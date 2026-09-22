"""Compare Profiles tool (P4) — "caches in this area that neither of us has
found yet." See docs/multi-profile-plan.md §9.
"""

from pathlib import Path

from django.conf import settings as django_settings
from django.contrib import messages
from django.http import QueryDict
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.translation import gettext as _

from gcforge import profiles as gcforge_profiles
from geocaches.models import Geocache
from geocaches.services import manage_tags
from geocaches.services.profile_compare import compare_found, schema_compat

from ._common import _filtered_qs

_BUCKETS = ("neither", "only_me", "only_them", "both")


class _GetOnlyRequest:
    """Minimal request-like wrapper so _filtered_qs() — which only ever reads
    request.GET (apply_all() + resolve_active_reference_point(), see
    geocaches/views/list.py) — can be reused against a reconstructed query
    string from the "Tag these caches" POST, without duplicating its
    reference-point resolution logic.
    """

    def __init__(self, get):
        self.GET = get


def _other_profiles(active_db_path: Path):
    return [
        p for p in gcforge_profiles.list_profiles(django_settings.DATA_DIR)
        if p.path.resolve() != active_db_path.resolve()
    ]


def _resolve_other(other_path_str: str, other_profiles) -> Path | None:
    if not other_path_str:
        return None
    other_path = Path(other_path_str)
    known = {p.path.resolve() for p in other_profiles}
    if other_path.resolve() not in known:
        return None
    return other_path


def tools_compare_profiles(request):
    active_db_path = Path(django_settings.DATABASES["default"]["NAME"])
    other_profiles = _other_profiles(active_db_path)

    if request.method == "POST" and request.POST.get("action") == "tag":
        return _handle_tag(request, active_db_path, other_profiles)

    other_path_str = request.GET.get("other", "").strip()
    bucket = request.GET.get("bucket", "neither")
    if bucket not in _BUCKETS:
        bucket = "neither"

    # Everything except other/bucket — the underlying scope filters (?fx=,
    # map bbox, ref, ...). Tab links and the tag-form's hidden field append
    # their own other=/bucket= on top of this, so it must not carry those
    # two keys itself or they'd appear twice with conflicting values.
    filter_params = request.GET.copy()
    filter_params.pop("other", None)
    filter_params.pop("bucket", None)

    context = {
        "other_profiles": other_profiles,
        "selected_other": other_path_str,
        "bucket": bucket,
        "query_string": filter_params.urlencode(),
        "filter_items": filter_params.items(),
        "bucket_counts": None,
        "results": [],
        "warning": "",
        "error": "",
    }

    if not other_path_str:
        return render(request, "geocaches/tools/tools_compare_profiles.html", context)

    other_path = _resolve_other(other_path_str, other_profiles)
    if other_path is None:
        context["error"] = _("Not a known profile.")
        return render(request, "geocaches/tools/tools_compare_profiles.html", context)

    ok, msg = schema_compat(other_path)
    if not ok:
        context["error"] = msg
        return render(request, "geocaches/tools/tools_compare_profiles.html", context)
    context["warning"] = msg

    qs, __ = _filtered_qs(request)
    pks = qs.values_list("pk", flat=True).iterator()
    results = compare_found(active_db_path, other_path, pks)

    bucket_counts = {b: 0 for b in _BUCKETS}
    for r in results:
        bucket_counts[r["bucket"]] += 1
        r["code"] = r["gc_code"] or r["oc_code"] or r["al_code"]

    context["bucket_counts"] = bucket_counts
    context["results"] = [r for r in results if r["bucket"] == bucket]
    return render(request, "geocaches/tools/tools_compare_profiles.html", context)


def _handle_tag(request, active_db_path: Path, other_profiles):
    other_path_str = request.POST.get("other", "").strip()
    bucket = request.POST.get("bucket", "neither")
    if bucket not in _BUCKETS:
        bucket = "neither"
    query_string = request.POST.get("query_string", "")
    tag_name = request.POST.get("tag_name", "").strip()

    redirect_qd = QueryDict(query_string, mutable=True)
    redirect_qd["other"] = other_path_str
    redirect_qd["bucket"] = bucket
    redirect_url = f"{reverse('geocaches:tools_compare_profiles')}?{redirect_qd.urlencode()}"

    other_path = _resolve_other(other_path_str, other_profiles)
    if other_path is None or not tag_name:
        return redirect(redirect_url)

    fake_request = _GetOnlyRequest(QueryDict(query_string))
    qs, __ = _filtered_qs(fake_request)
    pks = qs.values_list("pk", flat=True).iterator()
    results = compare_found(active_db_path, other_path, pks)
    bucket_pks = [r["id"] for r in results if r["bucket"] == bucket]

    if bucket_pks:
        tag_qs = Geocache.objects.filter(pk__in=bucket_pks)
        n = manage_tags("bulk_add", tag_name=tag_name, queryset=tag_qs)
        messages.success(request, _("Tagged %(n)d cache(s) as %(tag)s.") % {"n": n, "tag": tag_name})

    return redirect(redirect_url)
