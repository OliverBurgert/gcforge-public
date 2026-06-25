from django import template
from django.utils.html import escape
from django.utils.safestring import mark_safe

from geocaches.icons import (
    get_attribute_icon_url,
    get_cache_type_icon_url,
    get_cache_type_color,
    get_waypoint_type_icon_url,
)

register = template.Library()


@register.simple_tag(takes_context=True)
def cache_type_icon(context, cache_type, size="20", cache=None):
    """Render a cache type icon inside a colored shape, or empty string if 'text'.

    GC caches get a circle, OC-only caches get a rounded rectangle.
    """
    icon_set = context.get("icon_set", "text")
    url = get_cache_type_icon_url(cache_type, icon_set)
    if not url:
        return ""
    bg = get_cache_type_color(cache_type)
    is_oc = cache and getattr(cache, "oc_code", None) and not getattr(cache, "gc_code", None)
    radius = "4px" if is_oc else "50%"
    return mark_safe(
        f'<span class="gcf-type-icon-wrap" style="width:{size}px;height:{size}px;background:{bg};border-radius:{radius}" '
        f'title="{escape(cache_type)}">'
        f'<img src="{url}" class="gcf-icon gcf-icon-type" alt="">'
        f'</span>'
    )


@register.simple_tag(takes_context=True)
def waypoint_type_icon(context, wpt_type, size="16"):
    """Render a waypoint type icon <img> or empty string if icon_set is 'text'."""
    icon_set = context.get("icon_set", "text")
    url = get_waypoint_type_icon_url(wpt_type, icon_set)
    if not url:
        return ""
    return mark_safe(
        f'<img src="{url}" class="gcf-icon gcf-icon-wpt" '
        f'width="{size}" height="{size}" alt="" '
        f'title="{escape(wpt_type)}">'
    )


@register.simple_tag(takes_context=True)
def attribute_icon(context, attr):
    """Render an attribute icon with tooltip, or empty string if unmapped/text mode.

    For negative attributes, wraps in a container with a strikethrough overlay.
    Returns empty string if no icon is available (caller should fall back to badge).
    """
    icon_set = context.get("icon_set", "text")
    url = get_attribute_icon_url(attr.source, attr.attribute_id, icon_set)
    if not url:
        return ""
    name = escape(attr.name)
    pos_label = "yes" if attr.is_positive else "no"
    title = f"{name} ({pos_label})"
    neg_cls = " gcf-attr-negative" if not attr.is_positive else ""
    return mark_safe(
        f'<span class="gcf-attr-icon-wrap{neg_cls}" title="{title}" data-bs-toggle="tooltip">'
        f'<img src="{url}" class="gcf-icon gcf-icon-attr" width="28" height="28" alt="">'
        f'</span>'
    )
