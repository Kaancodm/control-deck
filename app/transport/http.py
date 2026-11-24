"""POST a wake the way the routine document describes. No extra vendor headers."""

from __future__ import annotations

import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Literal

WAKE_TIMEOUT_S = 5.0


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A redirect is not a 200 and must not forward the bearer key."""

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: object,
        code: int,
        msg: str,
        headers: object,
        newurl: str,
    ) -> urllib.request.Request | None:
        return None


@dataclass(frozen=True)
class StartResponse:
    kind: Literal["http", "no_response"]
    status_code: int | None = None


def post_wake(
    *,
    url: str,
    authorization: str,
    body: bytes,
    opener: urllib.request.OpenerDirector | None = None,
    timeout: float = WAKE_TIMEOUT_S,
) -> StartResponse:
    """Return the HTTP status. 200 means the run started, nothing more.

    A missing response is not a status and is not an acknowledgement.
    """
    if (
        not authorization.startswith("Bearer ")
        or "\r" in authorization
        or "\n" in authorization
        or authorization.count(" ") != 1
    ):
        raise ValueError("invalid authorization")
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={"Authorization": authorization, "Content-Type": "application/json"},
    )
    client = opener or urllib.request.build_opener(_NoRedirect)
    try:
        with client.open(request, timeout=timeout) as response:
            return StartResponse("http", status_code=response.status)
    except urllib.error.HTTPError as exc:
        return StartResponse("http", status_code=exc.code)
    except (urllib.error.URLError, TimeoutError, OSError):
        return StartResponse("no_response")
