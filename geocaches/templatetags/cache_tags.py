import json
from urllib.parse import urlencode

from django import template
from django.utils.safestring import mark_safe

from geocaches.geo.countries import iso_to_name
from geocaches.log_format import render_for_display, sanitize_html

register = template.Library()


@register.filter
def country_name(iso_code):
    """Convert an ISO 3166-1 alpha-2 code to English country name."""
    return iso_to_name(iso_code) if iso_code else ""


@register.filter
def al_theme_badges(themes):
    """Map a list of raw Adventure Lab theme tokens to display badges.

    Returns ``[{value, label, icon}, …]`` with prettified labels + emoji icons.
    """
    from geocaches.al_themes import theme_badges
    return theme_badges(themes)


@register.filter
def cached_image(value, category):
    """Wrap a remote image URL so it goes through the local image cache."""
    from geocaches.services.image_cache import url_for
    return url_for(value or "", category=category)


@register.simple_tag
def cached_cache_image(source_url, cache):
    """Resolve a cache-listing image URL through the cache.

    Picks ``cache_listing_gc`` vs ``cache_listing_other`` from the URL host
    and the found/unfound/mine state from the parent ``cache``.
    """
    from geocaches.services.image_cache import (
        category_for_cache_image, state_for_cache, url_for,
    )
    if not source_url:
        return ""
    return url_for(
        source_url,
        category=category_for_cache_image(source_url),
        state=state_for_cache(cache),
        linked_type="geocache",
        linked_id=cache.pk,
    )


@register.simple_tag
def cached_alc_image(source_url, cache):
    """Resolve an Adventure Lab image URL through the cache."""
    from geocaches.services.image_cache import state_for_cache, url_for
    if not source_url:
        return ""
    return url_for(source_url, category="alc", state=state_for_cache(cache),
                   linked_type="geocache", linked_id=cache.pk)


@register.simple_tag
def cache_description_html(value, cache):
    """Render a cache description HTML body: sanitise + rewrite <img src>
    URLs through the image cache (gc/other split per URL)."""
    from geocaches.services.image_cache import rewrite_html_for_cache
    if not value:
        return ""
    return mark_safe(rewrite_html_for_cache(sanitize_html(value), cache))


@register.simple_tag
def cache_log_html(value, source, cache, log=None):
    """Render a cache-log text body: pipe through the source-aware mini-markup
    renderer, then rewrite <img src> URLs through the image cache."""
    from geocaches.services.image_cache import rewrite_html_for_log
    if not value:
        return ""
    return mark_safe(rewrite_html_for_log(render_for_display(value, source), cache, log=log))


@register.simple_tag
def tb_log_html(value, trackable):
    """Render a TB-log text body: sanitise HTML and route <img src> URLs
    through the image cache (tb_log category)."""
    from geocaches.services.image_cache import rewrite_html_for_tb_log
    if not value:
        return ""
    return mark_safe(rewrite_html_for_tb_log(sanitize_html(value), trackable))


@register.filter(is_safe=True)
def safe_html(value):
    """Strip script/style/event-handler content; mark result safe for HTML rendering."""
    if not value:
        return value
    return mark_safe(sanitize_html(value))


@register.filter(is_safe=True)
def render_log_text(text, source=''):
    """Render a geocache log entry as safe HTML.

    OC logs (source starts with 'oc_') are treated as HTML and sanitised.
    GC / unknown-source logs are converted from GC mini-markup (smileys,
    GC markdown subset, BBCode, old inline HTML) to safe HTML.
    All links get target="_blank" rel="noopener".
    """
    return render_for_display(text, source)


_ROT13 = str.maketrans(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz",
    "NOPQRSTUVWXYZABCDEFGHIJKLMnopqrstuvwxyzabcdefghijklm",
)


@register.filter
def rot13(value):
    """Encode/decode a string with ROT13."""
    if not value:
        return value
    return value.translate(_ROT13)


_COMPASS = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]


@register.filter
def multiply(value, arg):
    """Multiply a numeric value by arg (e.g. for unit conversion)."""
    try:
        return float(value) * float(arg)
    except (TypeError, ValueError):
        return value


@register.filter
def tojson(value):
    """Serialize a Python value to a JSON string for use in HTML attributes.
    Do NOT mark safe — Django's HTML auto-escaping must convert " to &quot;."""
    return json.dumps(value)


@register.filter
def bearing_label(deg):
    """Format a bearing in degrees as '274° W'."""
    if deg is None:
        return "—"
    direction = _COMPASS[round(deg / 45) % 8]
    return f"{deg:.0f}° {direction}"


@register.simple_tag
def coords(lat, lon, fmt="dd"):
    """
    Format a lat/lon pair in the requested display format.
    Returns a 2-tuple (lat_str, lon_str) — use with {% coords lat lon fmt as c %}
    and then {{ c.0 }} / {{ c.1 }}.
    """
    from geocaches.geo.coords import format_coords
    return format_coords(lat, lon, fmt)


@register.simple_tag(takes_context=True)
def sort_header(context, field, label, current_sort, current_order):
    """Render a <th> with a sortable column header link."""
    request = context.get("request")

    if current_sort == field:
        next_order = "desc" if current_order == "asc" else "asc"
        arrow = (
            '<span class="sort-arrow active">▲</span>'
            if current_order == "asc"
            else '<span class="sort-arrow active">▼</span>'
        )
    else:
        next_order = "asc"
        arrow = '<span class="sort-arrow">▲</span>'

    params = request.GET.copy() if request else {}
    params["sort"] = field
    params["order"] = next_order
    params.pop("page", None)
    url = f"?{urlencode(params)}"

    return mark_safe(
        f'<th>'
        f'<a hx-get="{url}" hx-target="#cache-table-container" href="{url}">'
        f'{label} {arrow}'
        f'</a>'
        f'</th>'
    )
