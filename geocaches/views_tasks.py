from django.http import JsonResponse
from django.shortcuts import render

from geocaches.tasks import get_task, list_tasks


def task_status(request):
    """Return HTML fragment showing running/recent task status (for HTMX polling)."""
    tasks = list_tasks()
    running = [t for t in tasks if t["state"] == "running"]
    recent = [
        t for t in tasks
        if t["state"] in ("completed", "failed", "cancelled")
        and t["completed_at"]
    ]
    # Show most recent completed task if nothing running
    recent.sort(key=lambda t: t["completed_at"] or "", reverse=True)
    display_tasks = running + recent[:1]
    return render(request, "geocaches/_task_status.html", {"tasks": display_tasks})


def task_status_json(request, task_id):
    """Return JSON status for a specific task."""
    info = get_task(task_id)
    if not info:
        return JsonResponse({"error": "not found"}, status=404)
    return JsonResponse(info)
