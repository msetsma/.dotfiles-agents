"""The gate's exception hierarchy."""

__all__ = ['GateError']


class GateError(Exception):
    """A check could not run; the gate skips it with a warning, never blocks on it."""
