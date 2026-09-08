"""Deprecated compatibility shim: import from social_reader.cli or core instead."""
from .cli import *  # noqa: F401, F403
from .core import *  # noqa: F401, F403
from .core import _parse_sources, _fetch_all_posts, _VALID_SOURCES  # noqa: F401
