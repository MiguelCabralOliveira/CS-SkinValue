import threading

import requests
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from ETL.config import (
    REQUEST_TIMEOUT,
    RETRY_MAX_ATTEMPTS,
    RETRY_WAIT_INITIAL,
    RETRY_WAIT_MAX,
    USER_AGENT,
)


class RetryableHTTPError(Exception):
    pass


_thread_local = threading.local()


def get_session() -> requests.Session:
    session = getattr(_thread_local, "session", None)
    if session is None:
        session = requests.Session()
        session.headers.update({"accept": "application/json"})
        _thread_local.session = session
    return session


_retry = retry(
    retry=retry_if_exception_type((RetryableHTTPError, requests.Timeout, requests.ConnectionError)),
    wait=wait_exponential(multiplier=RETRY_WAIT_INITIAL, max=RETRY_WAIT_MAX),
    stop=stop_after_attempt(RETRY_MAX_ATTEMPTS),
    reraise=True,
)


@_retry
def get_json(url: str, params: dict | None = None) -> dict | list:
    session = get_session()
    response = session.get(url, params=params, timeout=REQUEST_TIMEOUT)
    if response.status_code in (429, 502, 503, 504):
        raise RetryableHTTPError(f"{response.status_code} for {url}")
    response.raise_for_status()
    return response.json()


@_retry
def get_text(url: str, params: dict | None = None, headers: dict | None = None) -> str:
    """Fetch a text/HTML body with the same retry policy as get_json.

    Sets a polite User-Agent on this call (Liquipedia and similar require it).
    """
    session = get_session()
    h = {"User-Agent": USER_AGENT, "accept": "text/html,application/xhtml+xml"}
    if headers:
        h.update(headers)
    response = session.get(url, params=params, headers=h, timeout=REQUEST_TIMEOUT)
    if response.status_code in (429, 502, 503, 504):
        raise RetryableHTTPError(f"{response.status_code} for {url}")
    response.raise_for_status()
    return response.text
