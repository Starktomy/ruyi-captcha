"""Direct Python solvers and a remote HTTP client."""
from .sdk import solve, solve_turnstile, solve_turnstile_details
from .client import ApiClient
from .errors import CaptchaError, RuntimeUnavailable, SolveCancelled

__version__ = "0.1.0"
__all__ = ["ApiClient", "CaptchaError", "RuntimeUnavailable", "SolveCancelled",
           "TurnstileError", "TurnstileSolution", "solve", "solve_turnstile",
           "solve_turnstile_details", "Engine", "use_engine", "__version__"]


def __getattr__(name):
    if name in ("Engine", "use_engine"):
        from .runtime import load_bindings
        return getattr(load_bindings(), name)
    if name in ("TurnstileError", "TurnstileSolution"):
        from .providers import turnstile
        return getattr(turnstile, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
