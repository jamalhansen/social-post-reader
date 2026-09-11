"""Deprecated compatibility shim: import from social_reader.cli or core instead."""
from .cli import *
from .core import *
from .core import _VALID_SOURCES, _fetch_all_posts, _parse_sources  # noqa: F401
