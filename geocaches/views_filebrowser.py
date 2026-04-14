import os
from pathlib import Path

from django.http import JsonResponse


def file_browse(request):
    """AJAX endpoint: list directory contents for the server-side file browser."""
    dir_param = request.GET.get("dir", "")
    ext_param = request.GET.get("ext", "")

    # Parse extensions filter
    extensions = set()
    if ext_param:
        for e in ext_param.split(","):
            e = e.strip().lower()
            if e and not e.startswith("."):
                e = "." + e
            if e:
                extensions.add(e)

    # Resolve directory — default to user's home
    if dir_param:
        target = Path(dir_param).resolve()
    else:
        target = Path.home()

    # Validate the path exists and is a directory
    if not target.exists():
        return JsonResponse({"error": f"Path not found: {target}"}, status=400)
    if not target.is_dir():
        return JsonResponse({"error": f"Not a directory: {target}"}, status=400)

    # Build parent path
    parent = str(target.parent) if target.parent != target else None

    entries = []

    # Add parent directory entry
    if parent:
        entries.append({
            "name": "..",
            "path": str(target.parent),
            "is_dir": True,
        })

    try:
        items = sorted(target.iterdir(), key=lambda p: p.name.lower())
    except PermissionError:
        return JsonResponse({"error": f"Permission denied: {target}"}, status=403)

    # Separate dirs and files, dirs first
    dirs = []
    files = []
    for item in items:
        # Skip hidden files/dirs on Windows (system/hidden) and Unix (dot-prefix)
        if item.name.startswith("."):
            continue
        try:
            is_dir = item.is_dir()
        except (PermissionError, OSError):
            continue

        if is_dir:
            dirs.append({
                "name": item.name,
                "path": str(item),
                "is_dir": True,
            })
        else:
            # Filter by extension if specified
            if extensions and item.suffix.lower() not in extensions:
                continue
            try:
                size = item.stat().st_size
            except (PermissionError, OSError):
                size = 0
            files.append({
                "name": item.name,
                "path": str(item),
                "is_dir": False,
                "size": size,
            })

    entries.extend(dirs)
    entries.extend(files)

    return JsonResponse({
        "current": str(target),
        "parent": parent,
        "entries": entries,
    })
