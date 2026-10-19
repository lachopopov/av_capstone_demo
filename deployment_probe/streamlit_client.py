"""Small, single-request HTTPX client for the existing ShopUNow probe API."""

from __future__ import annotations

import os
import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from dotenv import dotenv_values


# Use the already-deployed API as the nonsecret default for this demo wrapper.
DEFAULT_API_BASE_URL = "https://fast-api-test.fastapicloud.dev"

# Keep the API's waiting deadline plus network overhead in the client timeout.
DEFAULT_TIMEOUT = httpx.Timeout(70.0, connect=5.0, read=70.0, write=10.0, pool=5.0)

# Store the shared demo UI password and the API token beside the parent project.
PARENT_ENV_PATH = Path(__file__).resolve().parents[1] / ".env"


@dataclass(frozen=True)
class RuntimeSettings:
    """Non-displayable runtime configuration loaded from host-controlled sources."""

    api_base_url: str
    demo_password: str
    api_token: str


class APIClientError(Exception):
    """Sanitized API failure that never carries raw provider or response content."""

    def __init__(
        self,
        status_code: int | None,
        code: str,
        message: str,
        request_id: str | None = None,
        *,
        write_outcome_uncertain: bool = False,
    ) -> None:
        # Save only the fields in the backend's documented safe error envelope.
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.request_id = request_id
        self.write_outcome_uncertain = write_outcome_uncertain


def load_runtime_settings(
    hosted_secrets: Mapping[str, Any],
    *,
    environment: Mapping[str, str] | None = None,
    dotenv_path: Path = PARENT_ENV_PATH,
) -> RuntimeSettings:
    """Read hosted secrets or local environment and fail without revealing values."""

    # Parse only the parent's local .env file; ignore file read/encoding failures safely.
    try:
        local_values = dotenv_values(dotenv_path)
    except (OSError, UnicodeError):
        local_values = {}

    # Copy the supplied environment or use the current process environment.
    selected_environment = os.environ if environment is None else environment

    # Hosted Streamlit secrets take priority, with local environment and .env as fallbacks.
    def configured_value(name: str) -> str | None:
        """Return one nonblank setting without printing or mutating it."""

        for source in (hosted_secrets, selected_environment, local_values):
            value = source.get(name)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None

    # The API origin is public configuration and may use the approved deployed default.
    api_base_url = _validated_api_origin(
        configured_value("SHOPUNOW_API_URL") or DEFAULT_API_BASE_URL
    )

    # Both shared credentials must exist before the protected UI becomes available.
    demo_password = configured_value("SHOPUNOW_DEMO_PASSWORD")
    api_token = configured_value("SHOPUNOW_API_TOKEN")
    if not demo_password or not api_token:
        raise ValueError("The demo password or API token is not configured.")

    # Return only the three values needed by the page and the HTTP client.
    return RuntimeSettings(api_base_url, demo_password, api_token)


def _validated_api_origin(value: str) -> str:
    """Validate the configured API origin with HTTPX without echoing its value."""

    try:
        # Use HTTPX's URL parser so malformed ports and authority syntax fail early.
        parsed_url = httpx.URL(value)
    except (httpx.InvalidURL, ValueError):
        raise ValueError("The API URL configuration is invalid.") from None

    # The wrapper accepts a plain HTTP(S) origin, not credentials, a path, or a query.
    valid_origin = (
        parsed_url.scheme in {"http", "https"}
        and bool(parsed_url.host)
        and not parsed_url.username
        and not parsed_url.password
        and parsed_url.path in {"", "/"}
        and not parsed_url.query
        and not parsed_url.fragment
    )
    if not valid_origin:
        raise ValueError("The API URL configuration is invalid.")

    # Strip only the trailing slash after validation for stable HTTPX path joining.
    return str(parsed_url).rstrip("/")


class ShopUNowAPIClient:
    """Own one HTTPX client and send one explicit API request at a time."""

    def __init__(
        self,
        base_url: str,
        api_token: str,
        *,
        transport: httpx.BaseTransport | None = None,
        timeout: httpx.Timeout = DEFAULT_TIMEOUT,
    ) -> None:
        # Retain only the origin and token required to build authenticated requests.
        self._base_url = base_url.rstrip("/")
        self._api_token = api_token

        # HTTPX owns and closes this transport; no retry transport is configured.
        self._client = httpx.Client(
            base_url=self._base_url,
            headers={"Accept": "application/json", "X-API-Token": self._api_token},
            timeout=timeout,
            transport=transport,
        )

    def __enter__(self) -> "ShopUNowAPIClient":
        # Support one scoped client lifetime with Python's native context manager.
        return self

    def __exit__(self, *_: object) -> None:
        # Close the HTTPX connection pool after the requested operation finishes.
        self.close()

    def close(self) -> None:
        """Close the owned HTTPX client and release its resources."""

        self._client.close()

    def create_interaction(
        self,
        *,
        user_id: str,
        conversation_id: str,
        role: str,
        department: str,
        question: str,
    ) -> dict[str, Any]:
        """Send exactly one POST with the API's five accepted request fields."""

        # Construct exactly the Pydantic request fields accepted by /interactions.
        payload = {
            "user_id": user_id,
            "conversation_id": conversation_id,
            "role": role,
            "department": department,
            "question": question,
        }

        # Make one POST only; an uncertain write is never automatically retried.
        response = self._send("POST", "/interactions", json=payload)
        return self._read_interaction_response(response)

    def create_conversation(self) -> str:
        """Create one fresh conversation using the bodyless API contract."""

        # Send exactly one authenticated POST with no payload or query parameters.
        try:
            response = self._send("POST", "/conversations")
        except APIClientError as error:
            # Losing a generated identifier has no persisted conversation side effect.
            raise APIClientError(
                error.status_code,
                error.code,
                error.message,
                error.request_id,
            ) from None

        # A successful but unexpected status or body leaves creation unconfirmed.
        if response.status_code != 201:
            raise APIClientError(
                response.status_code,
                "invalid_response",
                "The API returned an unexpected conversation response.",
                write_outcome_uncertain=False,
            )

        body = self._read_json_object(response, operation="conversation")
        conversation_id = body.get("conversation_id")

        # Check lowercase UUID hex first, then require the version and variant of UUID4.
        valid_hex = isinstance(conversation_id, str) and bool(
            re.fullmatch(r"[0-9a-f]{32}", conversation_id)
        )
        if valid_hex:
            parsed_id = uuid.UUID(hex=conversation_id)
            if parsed_id.version == 4 and parsed_id.variant == uuid.RFC_4122:
                return conversation_id
        raise APIClientError(
            response.status_code,
            "invalid_response",
            "The API returned an unexpected conversation response.",
            write_outcome_uncertain=False,
        )

    def read_history(self, *, user_id: str, conversation_id: str) -> dict[str, Any]:
        """Send exactly one authenticated GET for the selected identifier pair."""

        # Use the two API-supported query parameters and no request body.
        response = self._send(
            "GET",
            "/history",
            params={"user_id": user_id, "conversation_id": conversation_id},
        )
        return self._read_history_response(response)

    def _send(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        """Send one request and map transport failures to a safe fixed message."""

        try:
            # Use the standard HTTPX request method once, without retry wrappers.
            response = self._client.request(method, path, **kwargs)
        except httpx.TimeoutException:
            # Keep timeout details private because they can include request metadata.
            raise APIClientError(
                None,
                "client_timeout",
                "The API did not respond before the client wait limit.",
                write_outcome_uncertain=method == "POST",
            ) from None
        except httpx.HTTPError:
            # Do not render provider exception text, which can contain sensitive details.
            raise APIClientError(
                None,
                "connection_error",
                "The API could not be reached.",
                write_outcome_uncertain=method == "POST",
            ) from None

        # Convert a structured backend error or unexpected status into a safe exception.
        if response.is_error:
            raise self._api_error(response, method=method)
        return response

    @staticmethod
    def _read_json_object(
        response: httpx.Response,
        *,
        operation: str,
    ) -> dict[str, Any]:
        """Accept only JSON object responses and hide malformed body contents."""

        try:
            # Parse JSON without ever including response text in a later error.
            body = response.json()
        except ValueError:
            body = None

        # The interaction and history contracts both return top-level JSON objects.
        if isinstance(body, dict):
            return body
        raise APIClientError(
            response.status_code,
            "invalid_response",
            f"The API returned an unexpected {operation} response.",
            write_outcome_uncertain=operation == "interaction",
        )

    @staticmethod
    def _read_interaction_response(response: httpx.Response) -> dict[str, Any]:
        """Check only the FAQ fields the page displays, not a duplicate full schema."""

        # Parse the common JSON envelope and read only the fields rendered by this page.
        body = ShopUNowAPIClient._read_json_object(response, operation="interaction")
        answer_text = body.get("response")
        faq_identifier = body.get("selected_faq_id")
        faq_text = body.get("selected_faq_text")

        # Require a real answer string; an absent FAQ match is represented by null fields.
        valid_answer = isinstance(answer_text, str)
        valid_faq_id = faq_identifier is None or isinstance(faq_identifier, str)
        valid_faq_text = faq_text is None or isinstance(faq_text, str)
        if valid_answer and valid_faq_id and valid_faq_text:
            return body

        # Do not display a malformed success as an empty or fabricated answer.
        raise APIClientError(
            response.status_code,
            "invalid_response",
            "The API returned an unexpected interaction response.",
            write_outcome_uncertain=True,
        )

    @staticmethod
    def _read_history_response(response: httpx.Response) -> dict[str, Any]:
        """Check only the transcript fields the page renders, not a duplicate schema."""

        # Parse the envelope fields that determine whether a history is empty.
        body = ShopUNowAPIClient._read_json_object(response, operation="history")
        found = body.get("found")
        messages = body.get("messages")
        if not isinstance(found, bool) or not isinstance(messages, list):
            raise APIClientError(
                response.status_code,
                "invalid_response",
                "The API returned an unexpected history response.",
            )

        # Validate only transcript fields used by the page and retain API sequence order.
        for message in messages:
            if not isinstance(message, dict):
                break
            valid_identifier = isinstance(message.get("id"), str)
            valid_sequence = (
                isinstance(message.get("sequence"), int)
                and not isinstance(message.get("sequence"), bool)
            )
            speaker = message.get("speaker")
            valid_speaker = isinstance(speaker, str) and speaker in {"user", "assistant"}
            valid_text = isinstance(message.get("text"), str)
            if not all((valid_identifier, valid_sequence, valid_speaker, valid_text)):
                break
        else:
            # Return the original validated response without sorting or rewriting it.
            if found == bool(messages):
                return body

        # Never render a malformed message as if it were a valid transcript row.
        raise APIClientError(
            response.status_code,
            "invalid_response",
            "The API returned an unexpected history response.",
        )

    @staticmethod
    def _api_error(response: httpx.Response, *, method: str) -> APIClientError:
        """Extract a valid safe error shape or use a status-only fallback."""

        try:
            # The API documents a top-level error object with three string fields.
            body = response.json()
        except ValueError:
            body = None

        # Reject scalar/list/null error members before looking up their fields.
        error_body = body.get("error") if isinstance(body, dict) else None
        if not isinstance(error_body, dict):
            error_body = {}

        # Keep only nonempty safe strings from the documented public error fields.
        code = error_body.get("code")
        message = error_body.get("message")
        request_id = error_body.get("request_id")
        valid_error = (
            isinstance(code, str)
            and bool(code.strip())
            and isinstance(message, str)
            and bool(message.strip())
        )
        if not valid_error:
            code = "api_error"
            message = "The API rejected or could not complete the request."
        if not isinstance(request_id, str):
            request_id = None

        # Treat unexpected server failures as uncertain; documented 503 rejects before admission.
        uncertain_write = (
            method == "POST"
            and response.status_code >= 500
            and response.status_code != 503
        )
        return APIClientError(
            response.status_code,
            code,
            message,
            request_id,
            write_outcome_uncertain=uncertain_write,
        )
