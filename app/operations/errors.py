"""Safe maintenance errors that never expose paths, credentials, or process output."""


class OperationError(RuntimeError):
    """Expose only a stable reason code to logs and command output."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__("Maintenance operation failed.")


class ProcessExitError(OperationError):
    """A child exited non-zero; a negative return code names the signal that killed it."""

    def __init__(self, returncode: int) -> None:
        super().__init__("process_failed")
        self.returncode = returncode
