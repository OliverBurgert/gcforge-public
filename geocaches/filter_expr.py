"""
Filter expression tree — phase 1 of the v2 filter system.

See ``docs/filtering.md`` for the architecture.  Two node types compose the
tree:

    Group(op, children)        op ∈ {"and", "or", "not"}; children of Group/Condition
    Condition(field, op, v)    field/op identifies a registered compiler; v is JSON

Depth is capped at ``MAX_DEPTH`` (= 2) so the dialog stays simple; deeper logic
goes through the raw WHERE clause escape hatch.

Compilation: ``compile_tree(node)`` returns a Django ``Q`` ready to feed to
``Geocache.objects.filter(...)``.  M2M / reverse-FK conditions compile to
``Q(pk__in=<subquery>)`` so group-level NOT (``~Q(...)``) stays semantically
correct (otherwise ``qs.exclude(tags__name=X)`` ≠ ``qs.filter(~Q(tags__name=X))``
on a many-to-many join).

URL encoding: ``to_url_param`` / ``from_url_param`` serialise the tree as
JSON then urlsafe-base64.  No zlib — keeps client-side encoding trivial
in JS (``btoa`` / ``atob``) without bringing in a JS compression lib.
Bound for the ``?fx=`` query param introduced in phase 4.

This module is callable today but no view consumes it yet — phase 2 wires it
into the list view alongside the legacy chain.
"""

from __future__ import annotations

import base64
import contextvars
import datetime as _dt
import json
from dataclasses import dataclass, field
from typing import Any, Callable

from .models import FOUND_LOG_TYPES as _FOUND_LOG_TYPES

# Request-scoped active ReferencePoint.  Set by ``apply_all`` (and any other
# caller that resolves an active ref) so compile functions like
# ``_alc_loggable_from_ref`` can pick up the user's current toolbar selection
# rather than always falling back to the default home location.
_active_ref_var: "contextvars.ContextVar[Any]" = contextvars.ContextVar(
    "gcf_filter_expr_active_ref", default=None,
)


def set_active_ref(ref):
    """Store the active ReferencePoint for the current call stack.
    Returns a token to pass to ``reset_active_ref``."""
    return _active_ref_var.set(ref)


def reset_active_ref(token) -> None:
    """Reset the active ReferencePoint back to its previous value."""
    _active_ref_var.reset(token)


def get_active_ref():
    """Return the currently set active ReferencePoint, or None."""
    return _active_ref_var.get()

from django.db.models import Q


# ---------------------------------------------------------------------------
# Tree primitives
# ---------------------------------------------------------------------------

OP_AND = "and"
OP_OR = "or"
OP_NOT = "not"
_GROUP_OPS = frozenset({OP_AND, OP_OR, OP_NOT})

MAX_DEPTH = 2


class FilterExprError(ValueError):
    """Raised for malformed expressions or unknown conditions."""


@dataclass
class Condition:
    """A leaf: ``field`` + ``op`` resolves to one registered compiler."""
    field: str
    op: str
    value: Any = None

    def to_dict(self) -> dict:
        return {"f": self.field, "op": self.op, "v": self.value}

    @classmethod
    def from_dict(cls, d: dict) -> "Condition":
        return cls(field=d["f"], op=d["op"], value=d.get("v"))


@dataclass
class Group:
    """Boolean combiner.  ``op`` is one of ``OP_AND`` / ``OP_OR`` / ``OP_NOT``.

    ``OP_NOT`` requires exactly one child; that's checked at compile time
    rather than construction so trees can be built incrementally by a UI.
    """
    op: str
    children: list = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.op not in _GROUP_OPS:
            raise FilterExprError(f"Unknown group op: {self.op!r}")

    def to_dict(self) -> dict:
        return {"g": self.op, "c": [c.to_dict() for c in self.children]}

    @classmethod
    def from_dict(cls, d: dict) -> "Group":
        if "g" not in d:
            raise FilterExprError("Group dict missing 'g' key")
        children: list = []
        for raw in d.get("c", []):
            if not isinstance(raw, dict):
                raise FilterExprError(f"Child must be a dict, got {type(raw).__name__}")
            if "g" in raw:
                children.append(cls.from_dict(raw))
            elif "f" in raw and "op" in raw:
                children.append(Condition.from_dict(raw))
            else:
                raise FilterExprError(f"Unrecognised child shape: {raw!r}")
        return cls(op=d["g"], children=children)


def validate_depth(node, max_depth: int = MAX_DEPTH) -> None:
    """Raise ``FilterExprError`` if the tree exceeds ``max_depth`` Group levels.

    A root Group with only Condition children is depth 1.  A root Group whose
    children include another Group is depth 2.  Deeper requires the raw WHERE
    clause escape hatch.
    """

    def _depth(n) -> int:
        if isinstance(n, Condition):
            return 0
        if isinstance(n, Group):
            return 1 + max((_depth(c) for c in n.children), default=0)
        raise FilterExprError(f"Unknown node type: {type(n).__name__}")

    actual = _depth(node)
    if actual > max_depth:
        raise FilterExprError(
            f"Filter expression too deeply nested (depth {actual} > {max_depth}). "
            f"Use the raw WHERE clause for more complex logic."
        )


# ---------------------------------------------------------------------------
# Compiler registry
# ---------------------------------------------------------------------------

CompilerFn = Callable[[Any], Q]
_REGISTRY: dict[tuple[str, str], CompilerFn] = {}


def register(field_name: str, op: str) -> Callable[[CompilerFn], CompilerFn]:
    """Decorator: register a compiler for ``(field, op)``."""
    def _wrap(fn: CompilerFn) -> CompilerFn:
        key = (field_name, op)
        if key in _REGISTRY:
            raise FilterExprError(f"Compiler already registered for {key}")
        _REGISTRY[key] = fn
        return fn
    return _wrap


def compile_condition(c: Condition) -> Q:
    fn = _REGISTRY.get((c.field, c.op))
    if fn is None:
        raise FilterExprError(f"Unknown condition: field={c.field!r}, op={c.op!r}")
    return fn(c.value)


def compile_tree(node) -> Q:
    """Recursively compile a tree to a Django ``Q``.

    Empty groups compile to an empty ``Q()`` (no-op).  NOT groups must have
    exactly one child.
    """
    if isinstance(node, Condition):
        return compile_condition(node)
    if isinstance(node, Group):
        if not node.children:
            return Q()
        if node.op == OP_AND:
            q = Q()
            for c in node.children:
                q &= compile_tree(c)
            return q
        if node.op == OP_OR:
            q: Q | None = None
            for c in node.children:
                child_q = compile_tree(c)
                q = child_q if q is None else (q | child_q)
            return q if q is not None else Q()
        if node.op == OP_NOT:
            if len(node.children) != 1:
                raise FilterExprError("NOT requires exactly one child")
            return ~compile_tree(node.children[0])
    raise FilterExprError(f"Unknown node type: {type(node).__name__}")


# ---------------------------------------------------------------------------
# URL encoding
# ---------------------------------------------------------------------------

def to_url_param(tree: Group) -> str:
    """Serialize a tree to the ``?fx=`` payload (urlsafe-base64 of JSON).

    Plain base64 (no compression).  Keeps the client-side encoder a
    one-liner (``btoa(JSON.stringify(tree))`` plus URL-safe char swap)
    without a JS zlib dependency.  Typical 4-leaf tree comes out around
    300 bytes — well under any URL length limit.
    """
    raw = json.dumps(tree.to_dict(), separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def from_url_param(s: str) -> Group:
    """Reverse of ``to_url_param``.  Raises ``FilterExprError`` on bad input."""
    padding = b"=" * (-len(s) % 4)
    try:
        raw = base64.urlsafe_b64decode(s.encode("ascii") + padding)
        data = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise FilterExprError(f"Malformed fx param: {exc}") from exc
    if not isinstance(data, dict) or "g" not in data:
        raise FilterExprError("fx must encode a root Group")
    tree = Group.from_dict(data)
    validate_depth(tree)
    return tree


# ---------------------------------------------------------------------------
# Value coercion helpers (used by registered compilers)
# ---------------------------------------------------------------------------

def _normalise_list(v) -> list[str]:
    """Coerce ``v`` to a list of trimmed non-empty strings.  Accepts list, str
    (semicolon-separated), or any iterable.  Returns ``[]`` for None/empty."""
    if v is None:
        return []
    if isinstance(v, str):
        return [x.strip() for x in v.split(";") if x.strip()]
    if isinstance(v, (list, tuple)):
        return [str(x).strip() for x in v if str(x).strip()]
    return []


def _num(v) -> float | None:
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return v
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _date(v) -> _dt.date | None:
    if v is None or v == "":
        return None
    if isinstance(v, _dt.date):
        return v
    try:
        return _dt.date.fromisoformat(str(v))
    except (TypeError, ValueError):
        return None


def _range_bounds(v) -> tuple[Any, Any]:
    """Pull (lo, hi) from either ``{"gte": x, "lte": y}`` or ``[x, y]``."""
    if isinstance(v, dict):
        return v.get("gte"), v.get("lte")
    if isinstance(v, (list, tuple)) and len(v) == 2:
        return v[0], v[1]
    return None, None


# ---------------------------------------------------------------------------
# Compiler factories — keep registration declarative and avoid the for-loop
# late-binding trap.
# ---------------------------------------------------------------------------

def _register_scalar_text(field_name: str) -> None:
    def contains(v):
        return Q(**{f"{field_name}__icontains": v}) if v else Q()

    def not_contains(v):
        return ~Q(**{f"{field_name}__icontains": v}) if v else Q()

    def equals(v):
        return Q(**{f"{field_name}__iexact": v}) if v else Q()

    def not_equals(v):
        return ~Q(**{f"{field_name}__iexact": v}) if v else Q()

    def starts_with(v):
        return Q(**{f"{field_name}__istartswith": v}) if v else Q()

    def in_list(v):
        items = _normalise_list(v)
        return Q(**{f"{field_name}__in": items}) if items else Q()

    def not_in_list(v):
        items = _normalise_list(v)
        return ~Q(**{f"{field_name}__in": items}) if items else Q()

    def is_empty(_v):
        return Q(**{field_name: ""}) | Q(**{f"{field_name}__isnull": True})

    def is_not_empty(_v):
        return ~(Q(**{field_name: ""}) | Q(**{f"{field_name}__isnull": True}))

    for op_name, fn in (
        ("contains", contains), ("not_contains", not_contains),
        ("equals", equals), ("not_equals", not_equals),
        ("starts_with", starts_with),
        ("in_list", in_list), ("not_in_list", not_in_list),
        ("is_empty", is_empty), ("is_not_empty", is_not_empty),
    ):
        _REGISTRY[(field_name, op_name)] = fn


def _register_enum(field_name: str, *, internal: str | None = None) -> None:
    target = internal or field_name

    def _in(v):
        items = _normalise_list(v)
        return Q(**{f"{target}__in": items}) if items else Q()

    def _not_in(v):
        items = _normalise_list(v)
        return ~Q(**{f"{target}__in": items}) if items else Q()

    def is_none(_v):
        return Q(**{target: ""})

    _REGISTRY[(field_name, "in")] = _in
    _REGISTRY[(field_name, "not_in")] = _not_in
    _REGISTRY[(field_name, "is_none")] = is_none


def _register_range(field_name: str) -> None:
    def gte(v):
        n = _num(v)
        return Q(**{f"{field_name}__gte": n}) if n is not None else Q()

    def lte(v):
        n = _num(v)
        return Q(**{f"{field_name}__lte": n}) if n is not None else Q()

    def eq(v):
        n = _num(v)
        return Q(**{field_name: n}) if n is not None else Q()

    def between(v):
        lo, hi = _range_bounds(v)
        lo_n, hi_n = _num(lo), _num(hi)
        q = Q()
        if lo_n is not None:
            q &= Q(**{f"{field_name}__gte": lo_n})
        if hi_n is not None:
            q &= Q(**{f"{field_name}__lte": hi_n})
        return q

    def is_null(v):
        return Q(**{f"{field_name}__isnull": bool(v)})

    for op_name, fn in (
        ("gte", gte), ("lte", lte), ("eq", eq),
        ("between", between), ("is_null", is_null),
    ):
        _REGISTRY[(field_name, op_name)] = fn


def _register_bool(field_name: str) -> None:
    _REGISTRY[(field_name, "is_true")] = lambda _v: Q(**{field_name: True})
    _REGISTRY[(field_name, "is_false")] = lambda _v: Q(**{field_name: False})


def _register_date(field_name: str) -> None:
    # Absolute operators
    def gte(v):
        d = _date(v)
        return Q(**{f"{field_name}__gte": d}) if d else Q()

    def lte(v):
        d = _date(v)
        return Q(**{f"{field_name}__lte": d}) if d else Q()

    def eq(v):
        d = _date(v)
        return Q(**{field_name: d}) if d else Q()

    def between(v):
        lo, hi = _range_bounds(v)
        lo_d, hi_d = _date(lo), _date(hi)
        q = Q()
        if lo_d:
            q &= Q(**{f"{field_name}__gte": lo_d})
        if hi_d:
            q &= Q(**{f"{field_name}__lte": hi_d})
        return q

    def is_null(v):
        return Q(**{f"{field_name}__isnull": bool(v)})

    # Relative operators — resolved against today() at compile time so the
    # filter ages correctly.  Never store an absolute date in the tree for
    # these.
    def relative_days(v):
        today = _dt.date.today()
        if isinstance(v, (int, float)):
            return Q(**{field_name: today + _dt.timedelta(days=int(v))})
        if isinstance(v, dict):
            q = Q()
            if v.get("gte") is not None:
                q &= Q(**{f"{field_name}__gte": today + _dt.timedelta(days=int(v["gte"]))})
            if v.get("lte") is not None:
                q &= Q(**{f"{field_name}__lte": today + _dt.timedelta(days=int(v["lte"]))})
            return q
        return Q()

    def last_n_days(v):
        try:
            n = int(v)
        except (TypeError, ValueError):
            return Q()
        today = _dt.date.today()
        return Q(**{
            f"{field_name}__gte": today - _dt.timedelta(days=n),
            f"{field_name}__lte": today,
        })

    def in_future(_v):
        return Q(**{f"{field_name}__gt": _dt.date.today()})

    def in_past(_v):
        return Q(**{f"{field_name}__lt": _dt.date.today()})

    def this_week(_v):
        today = _dt.date.today()
        start = today - _dt.timedelta(days=today.weekday())  # Monday
        end = start + _dt.timedelta(days=6)
        return Q(**{f"{field_name}__gte": start, f"{field_name}__lte": end})

    def this_month(_v):
        today = _dt.date.today()
        start = today.replace(day=1)
        next_first = (
            start.replace(year=start.year + 1, month=1)
            if start.month == 12
            else start.replace(month=start.month + 1)
        )
        end = next_first - _dt.timedelta(days=1)
        return Q(**{f"{field_name}__gte": start, f"{field_name}__lte": end})

    def this_year(_v):
        today = _dt.date.today()
        return Q(**{
            f"{field_name}__gte": _dt.date(today.year, 1, 1),
            f"{field_name}__lte": _dt.date(today.year, 12, 31),
        })

    for op_name, fn in (
        ("gte", gte), ("lte", lte), ("eq", eq),
        ("between", between), ("is_null", is_null),
        ("relative_days", relative_days),
        ("last_n_days", last_n_days),
        ("in_future", in_future), ("in_past", in_past),
        ("this_week", this_week), ("this_month", this_month), ("this_year", this_year),
    ):
        _REGISTRY[(field_name, op_name)] = fn


# ---------------------------------------------------------------------------
# Condition registry — phase 1 set
# ---------------------------------------------------------------------------

for _f in ("name", "owner", "placed_by", "gc_code", "oc_code"):
    _register_scalar_text(_f)


# "code" is multi-field OR across gc_code / oc_code / al_code.
def _code_q(v: str, *, negate: bool) -> Q:
    if not v:
        return Q()
    q = Q(gc_code__icontains=v) | Q(oc_code__icontains=v) | Q(al_code__icontains=v)
    return ~q if negate else q


_REGISTRY[("code", "contains")] = lambda v: _code_q(v, negate=False)
_REGISTRY[("code", "not_contains")] = lambda v: _code_q(v, negate=True)


# Full-text across description fields + hint.
def _text_q(v: str, *, negate: bool) -> Q:
    if not v:
        return Q()
    q = (
        Q(short_description__icontains=v)
        | Q(long_description__icontains=v)
        | Q(hint__icontains=v)
    )
    return ~q if negate else q


_REGISTRY[("description", "contains")] = lambda v: _text_q(v, negate=False)
_REGISTRY[("description", "not_contains")] = lambda v: _text_q(v, negate=True)


_register_enum("cache_type")
_register_enum("status")


# Size respects size_override (override wins; fall back to size).
def _size_q(v, *, negate: bool) -> Q:
    items = _normalise_list(v)
    if not items:
        return Q()
    q = Q(size_override__in=items) | Q(size_override__isnull=True, size__in=items)
    return ~q if negate else q


_REGISTRY[("size", "in")] = lambda v: _size_q(v, negate=False)
_REGISTRY[("size", "not_in")] = lambda v: _size_q(v, negate=True)


for _f in ("difficulty", "terrain", "fav_points", "elevation", "found_count"):
    _register_range(_f)


for _f in (
    "found", "ftf", "dnf", "user_flag", "is_premium", "has_trackable",
    "needs_maintenance", "watch", "has_corrected_coordinates", "import_locked",
    "completed",
):
    _register_bool(_f)


for _f in ("hidden_date", "last_found_date", "found_date", "dnf_date"):
    _register_date(_f)


def _register_datetime_as_date(field_name: str) -> None:
    """Register date operators for a DateTimeField using __date lookups."""
    df = f"{field_name}__date"

    def gte(v):
        d = _date(v)
        return Q(**{f"{df}__gte": d}) if d else Q()

    def lte(v):
        d = _date(v)
        return Q(**{f"{df}__lte": d}) if d else Q()

    def eq(v):
        d = _date(v)
        return Q(**{df: d}) if d else Q()

    def between(v):
        lo, hi = _range_bounds(v)
        lo_d, hi_d = _date(lo), _date(hi)
        q = Q()
        if lo_d:
            q &= Q(**{f"{df}__gte": lo_d})
        if hi_d:
            q &= Q(**{f"{df}__lte": hi_d})
        return q

    def is_null(v):
        return Q(**{f"{field_name}__isnull": bool(v)})

    def last_n_days(v):
        try:
            n = int(v)
        except (TypeError, ValueError):
            return Q()
        today = _dt.date.today()
        return Q(**{f"{df}__gte": today - _dt.timedelta(days=n), f"{df}__lte": today})

    def in_past(_v):
        return Q(**{f"{df}__lt": _dt.date.today()})

    def in_future(_v):
        return Q(**{f"{df}__gt": _dt.date.today()})

    def this_week(_v):
        today = _dt.date.today()
        start = today - _dt.timedelta(days=today.weekday())
        return Q(**{f"{df}__gte": start, f"{df}__lte": start + _dt.timedelta(days=6)})

    def this_month(_v):
        today = _dt.date.today()
        start = today.replace(day=1)
        next_first = (
            start.replace(year=start.year + 1, month=1)
            if start.month == 12
            else start.replace(month=start.month + 1)
        )
        return Q(**{f"{df}__gte": start, f"{df}__lte": next_first - _dt.timedelta(days=1)})

    def this_year(_v):
        today = _dt.date.today()
        return Q(**{f"{df}__gte": _dt.date(today.year, 1, 1), f"{df}__lte": _dt.date(today.year, 12, 31)})

    def not_today(_v):
        return ~Q(**{df: _dt.date.today()})

    for op_name, fn in (
        ("gte", gte), ("lte", lte), ("eq", eq),
        ("between", between), ("is_null", is_null),
        ("last_n_days", last_n_days),
        ("in_past", in_past), ("in_future", in_future),
        ("this_week", this_week), ("this_month", this_month), ("this_year", this_year),
        ("not_today", not_today),
    ):
        _REGISTRY[(field_name, op_name)] = fn


for _f in ("updated_at", "last_gpx_date", "imported_at"):
    _register_datetime_as_date(_f)


# Public name "country" → model field iso_country_code.  Reduces UI/storage
# coupling to the schema.
_register_enum("country", internal="iso_country_code")
_register_enum("state")
_register_enum("county")


# Tags (M2M).  Compiled via pk-in-subquery so group-level NOT works.
def _tags_in(v):
    items = _normalise_list(v)
    if not items:
        return Q()
    from .models import Geocache
    return Q(pk__in=Geocache.objects.filter(tags__name__in=items).values("pk"))


def _tags_not_in(v):
    items = _normalise_list(v)
    if not items:
        return Q()
    from .models import Geocache
    return ~Q(pk__in=Geocache.objects.filter(tags__name__in=items).values("pk"))


def _tags_is_none(_v):
    from .models import Geocache
    return Q(pk__in=Geocache.objects.filter(tags__isnull=True).values("pk"))


_REGISTRY[("tags", "in")] = _tags_in
_REGISTRY[("tags", "not_in")] = _tags_not_in
_REGISTRY[("tags", "is_none")] = _tags_is_none


# Attributes (M2M).  Same pk-in-subquery pattern as tags.  Values are
# attribute primary keys (integers).
def _normalise_int_list(v) -> list[int]:
    """Coerce ``v`` to a list of ints; accept list-of-anything or CSV string."""
    if v is None:
        return []
    if isinstance(v, str):
        items = v.split(",")
    elif isinstance(v, (list, tuple)):
        items = v
    else:
        return []
    out: list[int] = []
    for x in items:
        try:
            out.append(int(str(x).strip()))
        except (TypeError, ValueError):
            continue
    return out


def _attributes_has_all(v):
    ids = _normalise_int_list(v)
    if not ids:
        return Q()
    from .models import Geocache
    qs = Geocache.objects.all()
    for aid in ids:
        qs = qs.filter(attributes__id=aid)
    return Q(pk__in=qs.values("pk"))


def _attributes_has_any(v):
    ids = _normalise_int_list(v)
    if not ids:
        return Q()
    from .models import Geocache
    return Q(pk__in=Geocache.objects.filter(attributes__id__in=ids).values("pk"))


def _attributes_has_none(v):
    ids = _normalise_int_list(v)
    if not ids:
        return Q()
    from .models import Geocache
    return ~Q(pk__in=Geocache.objects.filter(attributes__id__in=ids).values("pk"))


_REGISTRY[("attributes", "has_all")] = _attributes_has_all
_REGISTRY[("attributes", "has_any")] = _attributes_has_any
_REGISTRY[("attributes", "has_none")] = _attributes_has_none


# Distance / bearing.  Both rely on the ``distance_km`` / ``bearing_deg``
# annotations added by ``query.annotate_distance``; the orchestrator
# (``apply_all``) does this when a reference point is active.  If the user
# crafts an fx URL with a distance condition but no ref is set, Django will
# raise FieldError during query — that surfaces as ``fx_error`` via the
# wiring in ``apply_filter_expr``.

def _distance_gte(v):
    n = _num(v)
    return Q(distance_km__gte=n) if n is not None else Q()


def _distance_lte(v):
    n = _num(v)
    return Q(distance_km__lte=n) if n is not None else Q()


def _distance_between(v):
    lo, hi = _range_bounds(v)
    lo_n, hi_n = _num(lo), _num(hi)
    q = Q()
    if lo_n is not None:
        q &= Q(distance_km__gte=lo_n)
    if hi_n is not None:
        q &= Q(distance_km__lte=hi_n)
    return q


_REGISTRY[("distance", "gte")] = _distance_gte
_REGISTRY[("distance", "lte")] = _distance_lte
_REGISTRY[("distance", "between")] = _distance_between


def _bearing_direction_in(v):
    """Compile a compass-direction list (e.g. ``["N", "NE"]``) to a Q on
    ``bearing_deg``.  Each direction maps to one or two degree ranges via the
    shared ``BEARING_RANGES`` table — N wraps around so it has two ranges."""
    dirs = [s.upper() for s in _normalise_list(v) if s] if isinstance(v, str) else \
        [str(d).strip().upper() for d in (v or []) if str(d).strip()]
    if not dirs:
        return Q()
    from .query import BEARING_RANGES
    q = Q()
    for d in dirs:
        for lo, hi in BEARING_RANGES.get(d, []):
            q |= Q(bearing_deg__gte=lo, bearing_deg__lte=hi)
    return q if q else Q(pk__in=[])  # unknown direction(s) → match nothing


def _bearing_degrees_between(v):
    lo, hi = _range_bounds(v)
    lo_n, hi_n = _num(lo), _num(hi)
    if lo_n is None and hi_n is None:
        return Q()
    # Support wrap-around: if lo > hi the user means "from lo to 360 OR 0 to hi".
    if lo_n is not None and hi_n is not None and lo_n > hi_n:
        return (
            Q(bearing_deg__gte=lo_n, bearing_deg__lte=360)
            | Q(bearing_deg__gte=0, bearing_deg__lte=hi_n)
        )
    q = Q()
    if lo_n is not None:
        q &= Q(bearing_deg__gte=lo_n)
    if hi_n is not None:
        q &= Q(bearing_deg__lte=hi_n)
    return q


_REGISTRY[("bearing", "direction_in")] = _bearing_direction_in
_REGISTRY[("bearing", "degrees_between")] = _bearing_degrees_between


# ---------------------------------------------------------------------------
# ALC condition pack (phase 3)
# ---------------------------------------------------------------------------
#
# All conditions use field name "alc"; the operator distinguishes them.
# M2M / reverse-FK conditions use the pk-in-subquery pattern so group-level
# NOT (~Q(...)) stays semantically correct.
#
# loggable_from_ref deferred — needs compile context, see phase 4.
# ---------------------------------------------------------------------------

def _alc_is_adventure(v):
    return Q(cache_type="Adventure Lab")


def _alc_is_stage(_v):
    return Q(al_detail__isnull=False)


def _register_alc_range(op_prefix: str, target_field: str) -> None:
    """Register gte / lte / between compilers for a direct (non-subquery) ALC field."""
    def gte(v):
        n = _num(v)
        return Q(**{f"{target_field}__gte": n}) if n is not None else Q()

    def lte(v):
        n = _num(v)
        return Q(**{f"{target_field}__lte": n}) if n is not None else Q()

    def between(v):
        lo, hi = _range_bounds(v)
        lo_n, hi_n = _num(lo), _num(hi)
        q = Q()
        if lo_n is not None:
            q &= Q(**{f"{target_field}__gte": lo_n})
        if hi_n is not None:
            q &= Q(**{f"{target_field}__lte": hi_n})
        return q

    _REGISTRY[("alc", f"{op_prefix}_gte")] = gte
    _REGISTRY[("alc", f"{op_prefix}_lte")] = lte
    _REGISTRY[("alc", f"{op_prefix}_between")] = between


# stages_total — uses denormalized Adventure.stage_count via direct FK traversal.
_register_alc_range("stages_total", "adventure__stage_count")

# geofencing_radius — direct field on ALStageDetail.
_register_alc_range("geofencing_radius", "al_detail__geofencing_radius")


def _alc_completed_count_subquery():
    """Return a subquery QuerySet of Adventure PKs annotated with completed stage count.

    Used by both stages_completed_* and stages_remaining_* compilers.
    Returns (adventure_qs, annotation_name) — callers filter on the annotation.
    """
    from django.db.models import Count, Q as _Q
    from .models import Adventure
    return Adventure.objects.annotate(
        completed_count=Count(
            "stages",
            filter=_Q(stages__al_detail__isnull=False) & (_Q(stages__completed=True) | _Q(stages__found=True)),
        )
    )


def _alc_stages_completed_gte(v):
    n = _num(v)
    if n is None:
        return Q()
    from .models import Geocache
    adv_pks = _alc_completed_count_subquery().filter(completed_count__gte=n).values("pk")
    return Q(pk__in=Geocache.objects.filter(adventure__pk__in=adv_pks).values("pk"))


def _alc_stages_completed_lte(v):
    n = _num(v)
    if n is None:
        return Q()
    from .models import Geocache
    adv_pks = _alc_completed_count_subquery().filter(completed_count__lte=n).values("pk")
    return Q(pk__in=Geocache.objects.filter(adventure__pk__in=adv_pks).values("pk"))


def _alc_stages_completed_between(v):
    lo, hi = _range_bounds(v)
    lo_n, hi_n = _num(lo), _num(hi)
    if lo_n is None and hi_n is None:
        return Q()
    from .models import Geocache
    qs = _alc_completed_count_subquery()
    if lo_n is not None:
        qs = qs.filter(completed_count__gte=lo_n)
    if hi_n is not None:
        qs = qs.filter(completed_count__lte=hi_n)
    return Q(pk__in=Geocache.objects.filter(adventure__pk__in=qs.values("pk")).values("pk"))


_REGISTRY[("alc", "stages_completed_gte")] = _alc_stages_completed_gte
_REGISTRY[("alc", "stages_completed_lte")] = _alc_stages_completed_lte
_REGISTRY[("alc", "stages_completed_between")] = _alc_stages_completed_between


def _alc_remaining_count_subquery():
    """Annotate adventures with remaining (not completed, not found) stage count."""
    from django.db.models import Count, Q as _Q
    from .models import Adventure
    return Adventure.objects.annotate(
        remaining_count=Count(
            "stages",
            filter=_Q(stages__al_detail__isnull=False) & _Q(stages__completed=False) & _Q(stages__found=False),
        )
    )


def _alc_stages_remaining_gte(v):
    n = _num(v)
    if n is None:
        return Q()
    from .models import Geocache
    adv_pks = _alc_remaining_count_subquery().filter(remaining_count__gte=n).values("pk")
    return Q(pk__in=Geocache.objects.filter(adventure__pk__in=adv_pks).values("pk"))


def _alc_stages_remaining_lte(v):
    n = _num(v)
    if n is None:
        return Q()
    from .models import Geocache
    adv_pks = _alc_remaining_count_subquery().filter(remaining_count__lte=n).values("pk")
    return Q(pk__in=Geocache.objects.filter(adventure__pk__in=adv_pks).values("pk"))


def _alc_stages_remaining_between(v):
    lo, hi = _range_bounds(v)
    lo_n, hi_n = _num(lo), _num(hi)
    if lo_n is None and hi_n is None:
        return Q()
    from .models import Geocache
    qs = _alc_remaining_count_subquery()
    if lo_n is not None:
        qs = qs.filter(remaining_count__gte=lo_n)
    if hi_n is not None:
        qs = qs.filter(remaining_count__lte=hi_n)
    return Q(pk__in=Geocache.objects.filter(adventure__pk__in=qs.values("pk")).values("pk"))


_REGISTRY[("alc", "stages_remaining_gte")] = _alc_stages_remaining_gte
_REGISTRY[("alc", "stages_remaining_lte")] = _alc_stages_remaining_lte
_REGISTRY[("alc", "stages_remaining_between")] = _alc_stages_remaining_between


def _alc_in_progress(_v):
    from .models import Geocache
    from .filters import apply_alc_in_progress_filter
    return Q(pk__in=apply_alc_in_progress_filter(Geocache.objects.all()).values("pk"))


def _alc_geofence_contains_point(v):
    """Materialise pks of stages whose geofence circle contains the given point.

    ``v`` must be a dict with ``lat`` and ``lon`` keys (floats).  Iterates
    over candidate stages in Python (same approach as the existing area filter)
    so that no database-side trigonometry is needed.
    """
    if not isinstance(v, dict):
        return Q()
    try:
        pt_lat = float(v["lat"])
        pt_lon = float(v["lon"])
    except (KeyError, TypeError, ValueError):
        return Q()

    from .geo import haversine_km as _haversine_km
    from .models import Geocache

    candidates = (
        Geocache.objects.filter(
            al_detail__geofencing_radius__isnull=False,
            al_detail__geofencing_radius__gt=0,
        )
        .values("pk", "latitude", "longitude", "al_detail__geofencing_radius")
    )
    pks = [
        row["pk"]
        for row in candidates
        if _haversine_km(pt_lat, pt_lon, row["latitude"], row["longitude"])
        <= row["al_detail__geofencing_radius"] / 1000.0
    ]
    return Q(pk__in=pks)


def _alc_is_final(_v):
    return Q(al_detail__is_final=True)


def _alc_is_not_final(_v):
    return Q(al_detail__is_final=False)


def _alc_has_theme_image(_v):
    # key_image_url exists on both ALStageDetail and Adventure.
    # gt="" is the simplest non-empty check that works in SQLite.
    return (
        Q(al_detail__key_image_url__gt="")
        | Q(adventure__key_image_url__gt="")
    )


def _alc_adventure_owner_in(v):
    items = _normalise_list(v)
    if not items:
        return Q()
    from .models import Geocache
    return Q(pk__in=Geocache.objects.filter(adventure__owner__in=items).values("pk"))


def _alc_adventure_owner_not_in(v):
    items = _normalise_list(v)
    if not items:
        return Q()
    from .models import Geocache
    return ~Q(pk__in=Geocache.objects.filter(adventure__owner__in=items).values("pk"))


def _alc_is_highly_recommended(_v):
    return Q(adventure__is_highly_recommended=True)


def _alc_loggable_from_ref(v):
    """Stages whose geofence circle contains a reference point.

    Resolution order for the reference point:
      1. ``v`` is an int / numeric string → that ``ReferencePoint`` by pk.
      2. The request-scoped active ref (set by ``apply_all`` from the toolbar
         ``?ref=`` selection).
      3. The user's default home location.
      4. Any ``ReferencePoint`` at all.

    The point's coordinates are resolved at compile time; the outer queryset
    does NOT need a ``distance_km`` annotation (the inner subquery applies
    its own).  Matches nothing when no usable ref point exists.

    Tree equivalent of the legacy ``?flag=alc_loggable_at_center`` URL flag.
    """
    from preferences.models import ReferencePoint

    ref = None
    if isinstance(v, (int, float)) or (isinstance(v, str) and v.strip().isdigit()):
        try:
            ref = ReferencePoint.objects.filter(pk=int(v)).first()
        except (ValueError, TypeError):
            ref = None
    if ref is None:
        ref = get_active_ref()
    if ref is None:
        ref = (
            ReferencePoint.objects.filter(is_default=True).first()
            or ReferencePoint.objects.first()
        )
    if ref is None:
        return Q(pk__in=[])

    from django.db.models import ExpressionWrapper, F, FloatField
    from .models import Geocache
    from .query import annotate_distance

    annotated = annotate_distance(Geocache.objects.all(), ref)
    matched = annotated.filter(
        al_detail__geofencing_radius__isnull=False,
        al_detail__geofencing_radius__gt=0,
        distance_km__lte=ExpressionWrapper(
            F("al_detail__geofencing_radius") / 1000.0,
            output_field=FloatField(),
        ),
    ).values("pk")
    return Q(pk__in=matched)


_REGISTRY[("alc", "is_adventure")] = _alc_is_adventure
_REGISTRY[("alc", "is_stage")] = _alc_is_stage
_REGISTRY[("alc", "in_progress")] = _alc_in_progress
_REGISTRY[("alc", "geofence_contains_point")] = _alc_geofence_contains_point
_REGISTRY[("alc", "is_final")] = _alc_is_final
_REGISTRY[("alc", "is_not_final")] = _alc_is_not_final
_REGISTRY[("alc", "has_theme_image")] = _alc_has_theme_image
_REGISTRY[("alc", "adventure_owner_in")] = _alc_adventure_owner_in
_REGISTRY[("alc", "adventure_owner_not_in")] = _alc_adventure_owner_not_in
_REGISTRY[("alc", "is_highly_recommended")] = _alc_is_highly_recommended
_REGISTRY[("alc", "loggable_from_ref")] = _alc_loggable_from_ref


# ---------------------------------------------------------------------------
# FTF-possible: active, unfound by anyone, not owned by the current user.
# Mirrors apply_flag_filter("ftf_possible") but as a pure Q subquery.
# ---------------------------------------------------------------------------

def _ftf_possible_is_true(_v):
    from .models import EVENT_CACHE_TYPES, Geocache
    from .query import mine_q
    found_pks = Geocache.objects.filter(
        logs__log_type__in=_FOUND_LOG_TYPES
    ).values("pk")
    return (
        Q(found=False)
        & Q(completed=False)
        & Q(status="Active")
        & ~Q(cache_type__in=EVENT_CACHE_TYPES | {"Adventure Lab"})
        & ~Q(pk__in=found_pks)
        & ~mine_q()
    )


_REGISTRY[("ftf_possible", "is_true")] = _ftf_possible_is_true


# ---------------------------------------------------------------------------
# Logs (reverse FK to Log) — phase 4b
# ---------------------------------------------------------------------------
#
# All operators use the pk-in-subquery pattern so group-level NOT stays safe.
# The Log model orders by ``-logged_at, -logged_date``; we use the same order
# for "most recent" semantics so the result matches what the user sees in
# the log list on a cache detail page.

# Log types that count as a finder's visit (for last_n_are_dnf).  Notes,
# reviewer logs, owner maintenance, etc. are excluded.
_LOG_FINDER_TYPES = (
    "Found it",
    "Didn't find it",
    "Attended",
    "Webcam Photo Taken",
    "Will Attend",
)

_LOG_DNF_TYPE = "Didn't find it"
# Single source of truth: geocaches.models.FOUND_LOG_TYPES (LogType.found_types()).
_LOG_FOUND_TYPES = _FOUND_LOG_TYPES


def _logs_last_log_type_in(v):
    """Last log on a cache has one of the given types.

    "Last" = most recent by the Log model's default order.  Counts every log
    type, not just finder logs — that matches GSAK's behaviour.
    """
    types = _normalise_list(v) if not isinstance(v, list) else \
        [str(t).strip() for t in v if str(t).strip()]
    if not types:
        return Q()
    from django.db.models import OuterRef, Subquery
    from .models import Geocache, Log

    last_type = Subquery(
        Log.objects.filter(geocache=OuterRef("pk"))
        .order_by("-logged_at", "-logged_date")
        .values("log_type")[:1]
    )
    matched = Geocache.objects.annotate(_last_type=last_type).filter(
        _last_type__in=types,
    ).values("pk")
    return Q(pk__in=matched)


def _logs_found_by_user(v):
    """Cache has at least one finder-type log with user_name matching ``v``
    (case-insensitive substring).  Useful for "find caches where Alice has
    logged a find" queries."""
    name = (v or "") if isinstance(v, str) else ""
    name = name.strip()
    if not name:
        return Q()
    from .models import Log

    matched = Log.objects.filter(
        log_type__in=_LOG_FOUND_TYPES,
        user_name__icontains=name,
    ).values("geocache_id").distinct()
    return Q(pk__in=matched)


def _logs_log_count_gte(v):
    """Cache has at least ``v`` total logs."""
    n = _num(v)
    if n is None or n < 0:
        return Q()
    from django.db.models import Count
    from .models import Geocache
    matched = (
        Geocache.objects.annotate(_log_count=Count("logs"))
        .filter(_log_count__gte=int(n))
        .values("pk")
    )
    return Q(pk__in=matched)


def _logs_last_n_are_dnf(v):
    """Cache's most recent ``n`` finder logs are all DNFs.

    Implemented with a single SQLite window-function query for performance
    on large log tables — per-cache iteration in Python would be N+1 over
    every cache.  Returns ``Q(pk__in=[…])`` for NOT-safety.

    Excludes non-finder log types (notes, reviewer actions, owner
    maintenance) from the "last N" window so a recent reviewer note doesn't
    mask the underlying DNF streak.  Matches GSAK semantics.
    """
    try:
        n = int(v)
    except (TypeError, ValueError):
        return Q()
    if n <= 0:
        return Q()

    from django.db import connection
    from .models import Log

    placeholders = ",".join(["%s"] * len(_LOG_FINDER_TYPES))
    sql = f"""
        SELECT cache_id FROM (
            SELECT geocache_id AS cache_id, log_type,
                   ROW_NUMBER() OVER (
                       PARTITION BY geocache_id
                       ORDER BY logged_at DESC,
                                logged_date DESC
                   ) AS rn
            FROM {Log._meta.db_table}
            WHERE log_type IN ({placeholders})
        )
        WHERE rn <= %s
        GROUP BY cache_id
        HAVING COUNT(*) = %s
           AND SUM(CASE WHEN log_type = %s THEN 1 ELSE 0 END) = %s
    """
    params = (*_LOG_FINDER_TYPES, n, n, _LOG_DNF_TYPE, n)
    with connection.cursor() as cursor:
        cursor.execute(sql, params)
        pks = [row[0] for row in cursor.fetchall()]
    return Q(pk__in=pks)


_REGISTRY[("logs", "last_log_type_in")] = _logs_last_log_type_in
_REGISTRY[("logs", "found_by_user")] = _logs_found_by_user
_REGISTRY[("logs", "log_count_gte")] = _logs_log_count_gte
_REGISTRY[("logs", "last_n_are_dnf")] = _logs_last_n_are_dnf


# ---------------------------------------------------------------------------
# Area (geographic regions) — phase 4c
# ---------------------------------------------------------------------------
#
# Mirrors the legacy ``?geo=`` semantics (see ``filters.apply_area_filter``).
# Value is a list of region dicts:
#     [{"type": "rect", "bbox": [s, w, n, e]},
#      {"type": "circle", "center": [lat, lon], "radius_m": r},
#      {"type": "polygon", "coordinates": [[lng,lat], …]},
#      {"type": "corridor", "path": [[lng,lat], …], "width_m": w}]
#
# Rect regions resolve to pure DB filters; circle / polygon / corridor are
# evaluated in Python over a union-bbox-prefiltered candidate set, same as
# the existing legacy filter.  ``Q(pk__in=…)`` keeps NOT-safe.
#
# For day-to-day map-drawer usage the legacy ``?geo=`` path is still faster
# (it can operate on the already-narrowed queryset rather than materialising
# pks against the whole table).  The tree-form exists so that area can sit
# inside an OR / NOT alongside other conditions when phase 4d's dialog
# composes things that way.

def _area_inside(v):
    if not v or not isinstance(v, list):
        return Q()
    from .filters import apply_area_filter
    from .models import Geocache

    # Reuse the existing filter — it knows how to dispatch rect (pure DB)
    # vs the other shapes (in-Python).  Pass the regions verbatim by encoding
    # back into the ``geo=`` string that apply_area_filter parses.
    geo_str = _regions_to_geo_string(v)
    if not geo_str:
        return Q()
    filtered = apply_area_filter(Geocache.objects.all(), {"geo": geo_str})
    return Q(pk__in=filtered.values("pk"))


def _area_outside(v):
    if not v or not isinstance(v, list):
        return Q()
    inside_q = _area_inside(v)
    return ~inside_q if inside_q.children else Q()


def _regions_to_geo_string(regions: list) -> str:
    """Serialise a region list into the pipe-separated ``geo=`` format that
    ``apply_area_filter`` consumes.  Same encoding as the chip code in
    ``query._match_saved_area``."""
    parts = []
    for r in regions:
        if not isinstance(r, dict):
            continue
        kind = r.get("type")
        if kind == "rect" and r.get("bbox"):
            try:
                s, w, n, e = [float(x) for x in r["bbox"]]
                parts.append(f"rect:{s},{w},{n},{e}")
            except (ValueError, TypeError):
                continue
        elif kind == "circle" and r.get("center") and r.get("radius_m") is not None:
            try:
                lat, lon = [float(x) for x in r["center"]]
                radius = float(r["radius_m"])
                parts.append(f"circle:{lat},{lon},{radius}")
            except (ValueError, TypeError):
                continue
        elif kind == "polygon" and r.get("coordinates"):
            try:
                flat = ",".join(f"{float(c[0])},{float(c[1])}" for c in r["coordinates"])
                if flat:
                    parts.append(f"polygon:{flat}")
            except (ValueError, TypeError, IndexError):
                continue
        elif kind == "corridor" and r.get("path") and r.get("width_m") is not None:
            try:
                width = float(r["width_m"])
                flat = ",".join(f"{float(c[0])},{float(c[1])}" for c in r["path"])
                if flat:
                    parts.append(f"corridor:{width}:{flat}")
            except (ValueError, TypeError, IndexError):
                continue
    return "|".join(parts)


_REGISTRY[("area", "inside")] = _area_inside
_REGISTRY[("area", "outside")] = _area_outside


# ---------------------------------------------------------------------------
# Waypoint (reverse FK to ``Waypoint``) — phase 5
# ---------------------------------------------------------------------------
#
# Each cache has zero or more child waypoints (parking, stage, final, …).  All
# compilers use the pk-in-subquery pattern so group-level NOT stays correct
# on the reverse FK.

def _waypoint_has_type(v):
    """Cache has at least one waypoint whose type is in the given list."""
    items = _normalise_list(v) if not isinstance(v, list) else \
        [str(t).strip() for t in v if str(t).strip()]
    if not items:
        return Q()
    from .models import Geocache
    return Q(pk__in=Geocache.objects.filter(waypoints__waypoint_type__in=items).values("pk"))


def _waypoint_not_has_type(v):
    items = _normalise_list(v) if not isinstance(v, list) else \
        [str(t).strip() for t in v if str(t).strip()]
    if not items:
        return Q()
    from .models import Geocache
    return ~Q(pk__in=Geocache.objects.filter(waypoints__waypoint_type__in=items).values("pk"))


def _waypoint_count_gte(v):
    """Cache has at least ``v`` waypoints total."""
    n = _num(v)
    if n is None or n < 0:
        return Q()
    from django.db.models import Count
    from .models import Geocache
    matched = (
        Geocache.objects.annotate(_wp_count=Count("waypoints"))
        .filter(_wp_count__gte=int(n))
        .values("pk")
    )
    return Q(pk__in=matched)


def _waypoint_name_contains(v):
    """Cache has at least one waypoint whose name contains ``v`` (case-insensitive)."""
    text = (v or "") if isinstance(v, str) else ""
    text = text.strip()
    if not text:
        return Q()
    from .models import Geocache
    return Q(pk__in=Geocache.objects.filter(waypoints__name__icontains=text).values("pk"))


def _waypoint_has_completed(_v):
    """Cache has at least one waypoint marked ``is_completed``."""
    from .models import Geocache
    return Q(pk__in=Geocache.objects.filter(waypoints__is_completed=True).values("pk"))


def _waypoint_has_user_created(_v):
    """Cache has at least one user-created waypoint."""
    from .models import Geocache
    return Q(pk__in=Geocache.objects.filter(waypoints__is_user_created=True).values("pk"))


_REGISTRY[("waypoint", "has_type")]         = _waypoint_has_type
_REGISTRY[("waypoint", "not_has_type")]     = _waypoint_not_has_type
_REGISTRY[("waypoint", "count_gte")]        = _waypoint_count_gte
_REGISTRY[("waypoint", "name_contains")]    = _waypoint_name_contains
_REGISTRY[("waypoint", "has_completed")]    = _waypoint_has_completed
_REGISTRY[("waypoint", "has_user_created")] = _waypoint_has_user_created


# ---------------------------------------------------------------------------
# Tree inspection helpers
# ---------------------------------------------------------------------------

def count_conditions(node) -> int:
    """Return the number of ``Condition`` leaves in a tree.

    Used by the chip bar to render a "Custom filter (N conditions)" label
    so users can see at a glance how complex an active ``?fx=`` is.
    """
    if isinstance(node, Condition):
        return 1
    if isinstance(node, Group):
        return sum(count_conditions(c) for c in node.children)
    return 0


# ---------------------------------------------------------------------------
# Human-readable labels for chip rendering (phase 4d-ii-A)
# ---------------------------------------------------------------------------

_TEXT_FIELDS = {
    "name":        "Name",
    "owner":       "Owner",
    "placed_by":   "Placed by",
    "gc_code":     "GC code",
    "oc_code":     "OC code",
    "code":        "Code",
    "description": "Description",
}
_TEXT_OPS = {
    "contains":      "contains",
    "not_contains":  "∌",
    "equals":        "=",
    "not_equals":    "≠",
    "starts_with":   "starts with",
    "in_list":       "in",
    "not_in_list":   "not in",
    "is_empty":      "is empty",
    "is_not_empty":  "is not empty",
}

_BOOL_FIELDS = {
    "found":                      "Found",
    "ftf":                        "FTF",
    "dnf":                        "DNF",
    "user_flag":                  "Flagged",
    "is_premium":                 "Premium",
    "has_trackable":              "Has trackable",
    "needs_maintenance":          "Needs maintenance",
    "watch":                      "Watching",
    "has_corrected_coordinates":  "Corrected coords",
    "import_locked":              "Import locked",
    "completed":                  "Completed",
}

_RANGE_FIELDS = {
    "difficulty":  "D",
    "terrain":     "T",
    "fav_points":  "Favs",
    "elevation":   "Elev",
    "found_count": "Found count",
}

_DATE_FIELDS = {
    "hidden_date":     "Hidden",
    "last_found_date": "Last found",
    "found_date":      "Found",
    "dnf_date":        "DNF",
    "updated_at":      "Updated",
    "last_gpx_date":   "Last GPX",
    "imported_at":     "Imported",
}


def _range_label(value) -> str:
    if isinstance(value, dict):
        lo, hi = value.get("gte"), value.get("lte")
    elif isinstance(value, (list, tuple)) and len(value) == 2:
        lo, hi = value
    else:
        return str(value)
    if lo is not None and hi is not None:
        return f"{lo}–{hi}"
    if lo is not None:
        return f"≥ {lo}"
    if hi is not None:
        return f"≤ {hi}"
    return "?"


def _join_values(value) -> str:
    if isinstance(value, (list, tuple)):
        return ", ".join(str(v) for v in value)
    return str(value)


def condition_to_label(field: str, op: str, value) -> str:
    """Build a short, human-readable chip label for one tree leaf condition.

    Falls back to ``"{field} {op}: {value}"`` for unknown combinations — the
    fallback isn't pretty but it's never silent.
    """
    if field in _TEXT_FIELDS:
        label = _TEXT_FIELDS[field]
        op_lbl = _TEXT_OPS.get(op, op)
        if op in ("is_empty", "is_not_empty"):
            return f"{label} {op_lbl}"
        return f"{label} {op_lbl}: {value}"

    if field in ("cache_type", "status", "size"):
        label = {"cache_type": "Type", "status": "Status", "size": "Size"}[field]
        if op == "in":
            return f"{label}: {_join_values(value)}"
        if op == "not_in":
            return f"{label} ∉: {_join_values(value)}"
        if op == "is_none":
            return f"{label}: (none)"

    if field in _RANGE_FIELDS:
        label = _RANGE_FIELDS[field]
        if op == "between":
            return f"{label}: {_range_label(value)}"
        if op in ("gte", "lte", "eq"):
            sym = {"gte": "≥", "lte": "≤", "eq": "="}[op]
            return f"{label} {sym} {value}"
        if op == "is_null":
            return f"{label}: (null)"

    if field in _BOOL_FIELDS:
        label = _BOOL_FIELDS[field]
        if op == "is_true":
            return f"✓ {label}"
        if op == "is_false":
            return f"✗ {label}"

    if field in _DATE_FIELDS:
        label = _DATE_FIELDS[field]
        if op == "between":
            return f"{label}: {_range_label(value)}"
        if op == "last_n_days":
            return f"{label}: last {value} days"
        if op == "in_past":
            return f"{label}: in past"
        if op == "in_future":
            return f"{label}: in future"
        if op == "this_week":
            return f"{label}: this week"
        if op == "this_month":
            return f"{label}: this month"
        if op == "this_year":
            return f"{label}: this year"
        if op == "relative_days":
            if isinstance(value, dict):
                return f"{label}: {_range_label(value)} days from today"
            return f"{label}: {value:+d} days from today" if isinstance(value, int) else f"{label}: {value}"
        if op == "eq":
            return f"{label} = {value}"
        if op in ("gte", "lte"):
            sym = {"gte": "≥", "lte": "≤"}[op]
            return f"{label} {sym} {value}"
        if op == "is_null":
            return f"{label}: (none)"
        if op == "not_today":
            return f"{label}: not today"

    if field in ("country", "state", "county"):
        label = {"country": "Country", "state": "State", "county": "County"}[field]
        if op == "in":
            return f"{label}: {_join_values(value)}"
        if op == "not_in":
            return f"{label} ∉: {_join_values(value)}"
        if op == "is_none":
            return f"{label}: (none)"

    if field == "tags":
        if op == "in":
            return f"Tags: {_join_values(value)}"
        if op == "not_in":
            return f"Tags ∉: {_join_values(value)}"
        if op == "is_none":
            return "Tags: (untagged)"

    if field == "attributes":
        ids_str = _join_values(value)
        if op == "has_all":
            return f"Attr (all): {ids_str}"
        if op == "has_any":
            return f"Attr (any): {ids_str}"
        if op == "has_none":
            return f"Attr (none): {ids_str}"

    if field == "distance":
        if op == "between":
            return f"Distance: {_range_label(value)} km"
        if op in ("gte", "lte"):
            sym = {"gte": "≥", "lte": "≤"}[op]
            return f"Distance {sym} {value} km"

    if field == "bearing":
        if op == "direction_in":
            return f"Bearing: {_join_values(value)}"
        if op == "degrees_between":
            return f"Bearing: {_range_label(value)}°"

    if field == "area":
        types = []
        if isinstance(value, list):
            types = [r.get("type") for r in value if isinstance(r, dict) and r.get("type")]
        type_lbl = ", ".join(types) if types else "?"
        if op == "inside":
            return f"Inside area ({type_lbl})"
        if op == "outside":
            return f"Outside area ({type_lbl})"

    if field == "logs":
        if op == "last_log_type_in":
            return f"Last log: {_join_values(value)}"
        if op == "last_n_are_dnf":
            return f"Last {value} are DNF"
        if op == "found_by_user":
            return f"Found by: {value}"
        if op == "log_count_gte":
            return f"Log count ≥ {value}"

    if field == "waypoint":
        if op == "has_type":
            return f"Has waypoint: {_join_values(value)}"
        if op == "not_has_type":
            return f"No waypoint: {_join_values(value)}"
        if op == "count_gte":
            return f"Waypoint count ≥ {value}"
        if op == "name_contains":
            return f"Waypoint name contains: {value}"
        if op == "has_completed":
            return "Has completed waypoint"
        if op == "has_user_created":
            return "Has user-created waypoint"

    if field == "ftf_possible" and op == "is_true":
        return "FTF possible"

    if field == "alc":
        # Boolean-ish flags
        bool_labels = {
            "is_adventure":         "Is adventure",
            "is_stage":             "Is stage",
            "in_progress":          "In progress",
            "is_final":             "Is final stage",
            "is_not_final":         "Not final stage",
            "has_theme_image":      "Has theme image",
            "is_highly_recommended": "Highly recommended",
            "loggable_from_ref":    "Loggable from ref",
        }
        if op in bool_labels:
            return bool_labels[op]
        # Range-style with a stem and one of gte/lte/between suffixes
        stems = {
            "stages_total":         "Stages total",
            "stages_completed":     "Stages completed",
            "stages_remaining":     "Stages remaining",
            "geofencing_radius":    "Geofence radius",
        }
        for stem, stem_label in stems.items():
            if op == f"{stem}_between":
                suffix = " m" if "radius" in stem else ""
                return f"{stem_label}: {_range_label(value)}{suffix}"
            if op == f"{stem}_gte":
                return f"{stem_label} ≥ {value}"
            if op == f"{stem}_lte":
                return f"{stem_label} ≤ {value}"
        if op == "geofence_contains_point":
            lat = value.get("lat") if isinstance(value, dict) else None
            lon = value.get("lon") if isinstance(value, dict) else None
            return f"Geofence ⊇ ({lat}, {lon})"
        if op == "adventure_owner_in":
            return f"Adventure owner: {_join_values(value)}"
        if op == "adventure_owner_not_in":
            return f"Adventure owner ∉: {_join_values(value)}"

    # Fallback — never silent, but ugly.
    return f"{field} {op}: {value}"


def group_to_summary_label(group: "Group") -> str:
    """Single-line summary of a non-root sub-group (used when a depth-2 tree
    has a nested ``OR``/``AND`` group as a child of root)."""
    op_lbl = {"and": "AND", "or": "OR", "not": "NOT"}.get(group.op, group.op.upper())
    n = count_conditions(group)
    return f"{op_lbl} ({n} condition{'s' if n != 1 else ''})"


# ---------------------------------------------------------------------------
# Legacy URL params → tree shim
# ---------------------------------------------------------------------------
#
# Maps the well-isolated subset of today's URL params to an equivalent tree
# under a single AND root.  Used by tests to compare the new compiler against
# the legacy apply_* chain.  Not yet a production codepath.
#
# Deliberately skipped here:
#   q, ftext               — multi-field OR (covered by their own compilers
#                            once phase 2 wires them in, not by the shim)
#   geo, radius, ref       — depend on annotations / pre-context
#   alc_*, my_tb_inside    — depend on subqueries / pre-context
#   attrs_yes, attrs_no    — phase 2
#   where                  — escape hatch, applied separately
# ---------------------------------------------------------------------------

def legacy_params_to_tree(params) -> Group:
    """Convert today's QueryDict-like ``params`` into a Group(AND, [...])."""
    children: list = []

    # Single-field text with operator
    for key, fname in (("fname", "name"), ("fowner", "owner"), ("fplacedby", "placed_by")):
        val = (params.get(key) or "").strip()
        op = (params.get(f"{key}_op") or "contains").strip()
        mapped = {
            "contains": "contains",
            "not_contains": "not_contains",
            "starts_with": "starts_with",
            "equals": "equals",
            "not_equals": "not_equals",
            "in_list": "in_list",
            "not_in_list": "not_in_list",
            "empty": "is_empty",
            "not_empty": "is_not_empty",
        }.get(op)
        if mapped is None:
            continue
        if val or mapped in ("is_empty", "is_not_empty"):
            children.append(Condition(fname, mapped, val))

    # Enums — both the single (?type=) and plural (?types=a,b) forms.
    for single_key, plural_key, fname in (
        ("type", "types", "cache_type"),
        ("status", "statuses", "status"),
        ("size", "sizes", "size"),
    ):
        single = (params.get(single_key) or "").strip()
        if single:
            children.append(Condition(fname, "in", [single]))
        plural = (params.get(plural_key) or "").strip()
        if plural:
            items = [x.strip() for x in plural.split(",") if x.strip()]
            if items:
                children.append(Condition(fname, "in", items))

    # found=1/0
    found = (params.get("found") or "").strip()
    if found == "1":
        children.append(Condition("found", "is_true", True))
    elif found == "0":
        children.append(Condition("found", "is_false", True))

    # Numeric ranges
    for fname, lo_key, hi_key in (
        ("difficulty", "diff_min", "diff_max"),
        ("terrain",    "terr_min", "terr_max"),
        ("fav_points", "fav_min",  "fav_max"),
    ):
        lo = _num((params.get(lo_key) or "").strip())
        hi = _num((params.get(hi_key) or "").strip())
        if lo is not None or hi is not None:
            children.append(Condition(fname, "between", {"gte": lo, "lte": hi}))

    # Date ranges
    for fname, lo_key, hi_key in (
        ("hidden_date",     "hidden_from", "hidden_to"),
        ("last_found_date", "lf_from",     "lf_to"),
        ("found_date",      "fd_from",     "fd_to"),
    ):
        lo = (params.get(lo_key) or "").strip()
        hi = (params.get(hi_key) or "").strip()
        if lo or hi:
            children.append(Condition(fname, "between", {"gte": lo or None, "lte": hi or None}))

    # Country / state / county — positive + exclude
    for ckey, fname in (("country", "country"), ("state", "state"), ("county", "county")):
        val = (params.get(ckey) or "").strip()
        if val == "__none__":
            children.append(Condition(fname, "is_none", True))
        elif val:
            children.append(Condition(fname, "in", [val]))
        excl = (params.get(f"{ckey}_exclude") or "").strip()
        if excl:
            items = [x.strip() for x in excl.split(",") if x.strip()]
            if items:
                children.append(Condition(fname, "not_in", items))

    # Tags
    tag = (params.get("tag") or "").strip()
    if tag == "__none__":
        children.append(Condition("tags", "is_none", True))
    elif tag:
        children.append(Condition("tags", "in", [tag]))

    inc = (params.get("tags_include") or "").strip()
    if inc:
        items = [x.strip() for x in inc.split(",") if x.strip()]
        if items:
            children.append(Condition("tags", "in", items))
    exc = (params.get("tags_exclude") or "").strip()
    if exc:
        items = [x.strip() for x in exc.split(",") if x.strip()]
        if items:
            children.append(Condition("tags", "not_in", items))

    # Code (gc/oc/al multi-field) — legacy fcode + fcode_op
    fcode = (params.get("fcode") or "").strip()
    fcode_op = (params.get("fcode_op") or "contains").strip()
    code_op_map = {
        "contains": "contains", "not_contains": "not_contains",
        "equals": "equals", "starts_with": "starts_with",
        "in_list": "in_list", "not_in_list": "not_in_list",
    }
    if fcode and fcode_op in code_op_map:
        children.append(Condition("code", code_op_map[fcode_op], fcode))

    # Full-text description / hint
    ftext = (params.get("ftext") or "").strip()
    if ftext:
        children.append(Condition("description", "contains", ftext))

    # Single-flag legacy dropdown — maps to the underlying boolean field.
    _flag_to_field = {
        "ftf": "ftf", "dnf": "dnf", "user_flag": "user_flag",
        "is_premium": "is_premium", "has_trackable": "has_trackable",
        "import_locked": "import_locked",
        "needs_maintenance": "needs_maintenance", "watch": "watch",
        "corrected_coords": "has_corrected_coordinates",
    }
    flag = (params.get("flag") or "").strip()
    if flag in _flag_to_field:
        children.append(Condition(_flag_to_field[flag], "is_true", True))

    # Multi-flag CSV — each name → its own is_true / is_false condition.
    flags_csv = (params.get("flags") or "").strip()
    for name in (x.strip() for x in flags_csv.split(",") if x.strip()):
        children.append(Condition(_flag_to_field.get(name, name), "is_true", True))
    flags_not_csv = (params.get("flags_not") or "").strip()
    for name in (x.strip() for x in flags_not_csv.split(",") if x.strip()):
        children.append(Condition(_flag_to_field.get(name, name), "is_false", True))

    # Attributes (M2M) — yes/no ID lists.
    def _int_list(raw):
        out: list[int] = []
        for x in (raw or "").split(","):
            x = x.strip()
            if not x:
                continue
            try:
                out.append(int(x))
            except ValueError:
                pass
        return out

    yes_ids = _int_list(params.get("attrs_yes"))
    if yes_ids:
        children.append(Condition("attributes", "has_all", yes_ids))
    no_ids = _int_list(params.get("attrs_no"))
    if no_ids:
        children.append(Condition("attributes", "has_none", no_ids))

    # Bearing (CSV of compass directions: N, NE, E, SE, S, SW, W, NW).
    bearing_csv = (params.get("bearing") or "").strip()
    dirs = [d.strip().upper() for d in bearing_csv.split(",") if d.strip()]
    if dirs:
        children.append(Condition("bearing", "direction_in", dirs))

    return Group(op=OP_AND, children=children)


__all__ = [
    "OP_AND", "OP_OR", "OP_NOT", "MAX_DEPTH",
    "Condition", "Group", "FilterExprError",
    "compile_condition", "compile_tree",
    "to_url_param", "from_url_param",
    "validate_depth",
    "register",
    "legacy_params_to_tree",
    "count_conditions",
    "condition_to_label",
    "group_to_summary_label",
]
