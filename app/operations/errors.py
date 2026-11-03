"""Safe maintenance errors that never expose paths, credentials, or process output."""


class OperationError(RuntimeError):
    """Expose only a stable reason code to logs and command output."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__("Maintenance operation failed.")
