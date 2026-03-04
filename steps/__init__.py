# Lazy imports — avoids loading heavy dependencies (sklearn, selenium)
# at import time.  Each function is imported only when called.


def run_collection(*args, **kwargs):
    from .collect import run_collection as _f
    return _f(*args, **kwargs)


def run_deduplication(*args, **kwargs):
    from .deduplicate import run_deduplication as _f
    return _f(*args, **kwargs)


def run_link_finder(*args, **kwargs):
    from .find_links import run_link_finder as _f
    return _f(*args, **kwargs)


__all__ = [
    "run_collection",
    "run_deduplication",
    "run_link_finder",
]
