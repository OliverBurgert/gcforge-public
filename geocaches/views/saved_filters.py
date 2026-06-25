import json
from urllib.parse import urlencode, urlparse

from django.http import (
    HttpResponse,
    HttpResponseBadRequest,
    HttpResponseNotAllowed,
    JsonResponse,
)
from django.shortcuts import redirect, render
from django.urls import reverse

from ..filter_expr import FilterExprError, Group, to_url_param, validate_depth
from ..models import SavedFilter, SavedWhereClause


def _wants_json(request) -> bool:
    """True when the caller is XHR/HTMX and expects a JSON-shaped response."""
    return (
        request.headers.get("HX-Request") == "true"
        or "application/json" in request.headers.get("Accept", "")
        or request.headers.get("X-Requested-With") == "XMLHttpRequest"
    )


def saved_filter_delete(request, pk):
    """POST: delete a saved filter (built-in filters are protected)."""
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    SavedFilter.objects.filter(pk=pk, is_builtin=False).delete()
    if request.headers.get("HX-Request"):
        all_filters = list(SavedFilter.objects.all())
        ctx = {
            "builtin_filters": [f for f in all_filters if f.is_builtin],
            "user_filters": [f for f in all_filters if not f.is_builtin],
        }
        return render(request, "geocaches/partials/_saved_filters_options.html", ctx)
    return redirect("geocaches:list")


def tree_filter_apply(request):
    """POST: validate a tree JSON, encode to ?fx=, redirect to the cache list.

    Preserves existing query-string params from the Referer (tag, type, scope
    etc.) so applying the modal doesn't reset the rest of the user's filter
    bar.  Encoding is done server-side to avoid pulling in a JS zlib library.
    """
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    raw = request.POST.get("tree_json", "").strip()
    json_mode = _wants_json(request)

    def _bad(msg: str):
        if json_mode:
            return JsonResponse({"error": msg}, status=400)
        return HttpResponseBadRequest(msg)

    if not raw:
        return _bad("tree_json required")
    try:
        data = json.loads(raw)
        tree = Group.from_dict(data)
        validate_depth(tree)
        encoded = to_url_param(tree)
    except (ValueError, FilterExprError) as exc:
        return _bad(f"invalid tree: {exc}")

    # Preserve the user's current URL params (tag, type, …) — only overwrite
    # ``fx``.  Pull them from the Referer; fall back to a bare ?fx= when the
    # browser doesn't send one (e.g. private-mode strip).
    referer = request.META.get("HTTP_REFERER", "")
    qs_pairs: list[tuple[str, str]] = []
    if referer:
        parsed = urlparse(referer)
        for kv in parsed.query.split("&"):
            if not kv:
                continue
            if "=" in kv:
                k, v = kv.split("=", 1)
            else:
                k, v = kv, ""
            if k in ("fx", "page"):
                continue  # we set fx ourselves; reset pagination
            qs_pairs.append((k, v))
    qs_pairs.append(("fx", encoded))
    url = reverse("geocaches:list") + "?" + urlencode(qs_pairs)
    if json_mode:
        return JsonResponse({"redirect": url})
    return redirect(url)


def tree_filter_save(request):
    """POST: store a tree JSON as a named SavedFilter.tree (upsert by name).

    Keeps ``params`` empty for tree-only saves; the legacy path is not used
    for new tree entries.  When invoked with HX-Request, returns updated
    options HTML for the saved-filter dropdown so the toolbar refreshes.
    """
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    name = request.POST.get("name", "").strip()
    raw = request.POST.get("tree_json", "").strip()
    if not name:
        return HttpResponseBadRequest("name required")
    if not raw:
        return HttpResponseBadRequest("tree_json required")
    try:
        data = json.loads(raw)
        tree = Group.from_dict(data)
        validate_depth(tree)
    except (ValueError, FilterExprError) as exc:
        return HttpResponseBadRequest(f"invalid tree: {exc}")

    SavedFilter.objects.update_or_create(
        name=name, defaults={"tree": tree.to_dict(), "params": {}},
    )

    if request.headers.get("HX-Request"):
        all_filters = list(SavedFilter.objects.all())
        ctx = {
            "builtin_filters": [f for f in all_filters if f.is_builtin],
            "user_filters": [f for f in all_filters if not f.is_builtin],
        }
        return render(request, "geocaches/partials/_saved_filters_options.html", ctx)

    next_url = request.POST.get("next", "").strip()
    if next_url and next_url.startswith("/"):
        return redirect(next_url)
    return redirect("geocaches:list")


def where_clause_save(request):
    """POST: save a named where clause."""
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    name = request.POST.get("name", "").strip()
    sql = request.POST.get("sql", "").strip()
    if not name or not sql:
        return HttpResponseBadRequest("name and sql required")
    SavedWhereClause.objects.update_or_create(name=name, defaults={"sql": sql})
    if request.headers.get("HX-Request"):
        named = list(SavedWhereClause.objects.filter(name__gt="").order_by("name").values("id", "name", "sql"))
        return HttpResponse(json.dumps(named), content_type="application/json")
    return redirect("geocaches:list")


def where_clause_delete(request, pk):
    """POST: delete a where clause (named or recent)."""
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    SavedWhereClause.objects.filter(pk=pk).delete()
    if request.headers.get("HX-Request"):
        named = list(SavedWhereClause.objects.filter(name__gt="").order_by("name").values("id", "name", "sql"))
        recent = list(SavedWhereClause.objects.filter(name="").order_by("-updated_at")[:10].values("id", "sql", "updated_at"))
        return HttpResponse(json.dumps({"named": named, "recent": recent}), content_type="application/json")
    return redirect("geocaches:list")
