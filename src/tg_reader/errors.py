"""Shared exception base class for the CLI exit-code contract."""


class PermanentError(Exception):
    """Base for permanent failures; the CLI maps these to exit code 1.

    "Permanent" means retrying with the same arguments will not help
    (unknown chat, no media, not authorized), as opposed to
    throttle.RetryLaterError which maps to exit code 2.
    """
