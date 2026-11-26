"""Errors raised by the durable background-job boundary."""


class DuplicateJobError(RuntimeError):
    """Raised when an active job already exists for a uniqueness scope."""


class UnregisteredJobError(RuntimeError):
    """Raised when a worker claims a job type it does not know how to run."""


class JobPayloadError(RuntimeError):
    """Raised when a job payload disagrees with its durable row identity."""
