def operation_mode(request):
    """{{ operation_mode }} in every template (the staff pages show a banner when it is not normal)."""
    from .mode import current
    return {"operation_mode": current()}
