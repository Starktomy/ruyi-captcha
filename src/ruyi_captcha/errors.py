class CaptchaError(RuntimeError):
    def __init__(self, message, *, code="solver_error"):
        super().__init__(message)
        self.code = code


class RuntimeUnavailable(CaptchaError):
    def __init__(self, message):
        super().__init__(message, code="runtime_unavailable")


class SolveCancelled(CaptchaError):
    def __init__(self):
        super().__init__("Task cancelled", code="cancelled")
