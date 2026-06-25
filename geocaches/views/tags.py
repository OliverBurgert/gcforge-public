from django.http import JsonResponse
from django.shortcuts import redirect, render

from .list import _filtered_qs


def tag_management(request):
    from geocaches.models import Tag
    from geocaches.services import manage_tags
    from django.db.models import Count

    if request.method == "POST":
        action = request.POST.get("_action")
        tag_id = request.POST.get("tag_id")
        new_name = request.POST.get("new_name", "").strip()
        rp_id = request.POST.get("rp_id", "").strip()
        manage_tags(action, tag_id=tag_id, new_name=new_name, rp_id=rp_id)
        return redirect("geocaches:tags")

    from django.db.models import Q
    from preferences.models import ReferencePoint
    tags = (
        Tag.objects.annotate(
            cache_count=Count("geocaches"),
            active_count=Count("geocaches", filter=Q(geocaches__deleted_at__isnull=True)),
            deleted_count=Count("geocaches", filter=Q(geocaches__deleted_at__isnull=False)),
        )
        .select_related("default_ref_point")
        .order_by("name")
    )
    return render(request, "geocaches/tags.html", {
        "tags": tags,
        "ref_points": ReferencePoint.objects.order_by("name"),
    })


def tags_json(request):
    from geocaches.models import Tag
    names = list(Tag.objects.order_by("name").values_list("name", flat=True))
    return JsonResponse(names, safe=False)


def bulk_tag_add(request):
    from geocaches.services import manage_tags

    qs, _ = _filtered_qs(request)
    query_string = request.GET.urlencode()

    if request.method == "POST":
        name = request.POST.get("tag_name", "").strip()
        propagate_alc = request.POST.get("propagate") == "yes"
        if name:
            manage_tags("bulk_add", tag_name=name, queryset=qs, propagate_alc=propagate_alc)
        from django.urls import reverse
        from urllib.parse import parse_qs, urlencode as _urlencode
        params = parse_qs(query_string, keep_blank_values=True)
        params.pop("tag", None)
        qs_str = _urlencode(params, doseq=True)
        return redirect(f"{reverse('geocaches:list')}?{qs_str}")

    count = qs.count()
    from geocaches.models import Geocache, Tag
    existing_tags = Tag.objects.order_by("name")
    alc_parents = qs.filter(adventure__isnull=False, al_detail__isnull=True)
    alc_parent_count = alc_parents.count()
    alc_stage_count = 0
    if alc_parent_count:
        adv_ids = list(alc_parents.values_list("adventure_id", flat=True))
        alc_stage_count = Geocache.objects.filter(
            adventure_id__in=adv_ids, al_detail__isnull=False
        ).count()
    return render(request, "geocaches/tools/bulk_tag_add.html", {
        "count": count,
        "existing_tags": existing_tags,
        "query_string": query_string,
        "alc_parent_count": alc_parent_count,
        "alc_stage_count": alc_stage_count,
    })


def bulk_tag_remove(request):
    from geocaches.models import Geocache, Tag
    from geocaches.services import manage_tags

    qs, _ = _filtered_qs(request)
    query_string = request.GET.urlencode()

    if request.method == "POST":
        tag_id = request.POST.get("tag_id", "").strip()
        propagate_alc = request.POST.get("propagate") == "yes"
        if tag_id:
            manage_tags("bulk_remove", tag_id=tag_id, queryset=qs, propagate_alc=propagate_alc)
        from django.urls import reverse
        from urllib.parse import parse_qs, urlencode as _urlencode
        params = parse_qs(query_string, keep_blank_values=True)
        params.pop("tag", None)
        qs_str = _urlencode(params, doseq=True)
        return redirect(f"{reverse('geocaches:list')}?{qs_str}")

    count = qs.count()
    tags_on_filtered = (
        Tag.objects.filter(geocaches__in=qs).distinct().order_by("name")
    )
    alc_parents = qs.filter(adventure__isnull=False, al_detail__isnull=True)
    alc_parent_count = alc_parents.count()
    alc_stage_count = 0
    if alc_parent_count:
        adv_ids = list(alc_parents.values_list("adventure_id", flat=True))
        alc_stage_count = Geocache.objects.filter(
            adventure_id__in=adv_ids, al_detail__isnull=False
        ).count()
    return render(request, "geocaches/tools/bulk_tag_remove.html", {
        "count": count,
        "tags": tags_on_filtered,
        "query_string": query_string,
        "alc_parent_count": alc_parent_count,
        "alc_stage_count": alc_stage_count,
    })


def cache_tag_edit(request, gc_code):
    from .detail import _get_cache
    cache = _get_cache(gc_code)
    if request.method == "POST":
        action = request.POST.get("_action")
        tag = None
        tag_id = None

        if action == "add":
            name = request.POST.get("tag_name", "").strip()
            if name:
                from geocaches.models import Tag
                tag, _ = Tag.objects.get_or_create(name=name)
                cache.tags.add(tag)
        elif action == "remove":
            tag_id = request.POST.get("tag_id")
            if tag_id:
                cache.tags.remove(tag_id)

        if request.POST.get("propagate") == "yes" and cache.adventure_id:
            from geocaches.models import ALStageDetail, Geocache
            if not ALStageDetail.objects.filter(geocache=cache).exists():
                stages_qs = Geocache.objects.filter(
                    adventure_id=cache.adventure_id, al_detail__isnull=False
                )
                if action == "add" and tag:
                    stage_ids = list(stages_qs.values_list("id", flat=True))
                    Through = Geocache.tags.through
                    Through.objects.bulk_create(
                        [Through(geocache_id=sid, tag_id=tag.id) for sid in stage_ids],
                        ignore_conflicts=True,
                    )
                elif action == "remove" and tag_id:
                    Geocache.tags.through.objects.filter(
                        geocache__in=stages_qs, tag_id=tag_id
                    ).delete()

    return redirect("geocaches:detail", gc_code=gc_code)
