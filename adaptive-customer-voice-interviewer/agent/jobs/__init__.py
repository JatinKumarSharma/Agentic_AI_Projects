"""Durable background-job boundary for CVI.

This package contains only queue semantics and error types. The actual storage
lives in the configured persistence adapter, and handlers are registered by the
worker process.
"""

from .errors import DuplicateJobError, UnregisteredJobError

__all__ = ["DuplicateJobError", "UnregisteredJobError"]
