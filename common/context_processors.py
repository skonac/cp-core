def operation_mode(request):
    """{{ operation_mode }} in every template (the staff pages show a banner when it is not normal)."""
    from .mode import current
    return {"operation_mode": current()}


def build_version(request):
    """{{ cp_version }}: "0.9.212 (3b5bc04)" (config/__init__.py); shown in the staff navigation."""
    from config import __version__
    return {"cp_version": __version__}
