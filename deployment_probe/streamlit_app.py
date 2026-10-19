"""Native Streamlit page for the browser-to-FastAPI FAQ retrieval demo."""

from __future__ import annotations

import hmac
from typing import Any

import streamlit as st
from streamlit.errors import StreamlitSecretNotFoundError

from streamlit_client import (
    APIClientError,
    RuntimeSettings,
    ShopUNowAPIClient,
    load_runtime_settings,
)


# Configure the page once before rendering any UI elements.
st.set_page_config(page_title="ShopUNow FAQ demo", page_icon=":material/help:")

# Start with a plain heading and explain that the returned text is an existing FAQ.
st.title("ShopUNow FAQ lookup")
st.caption(
    "Select the role and department that fit your question, then submit it for FAQ lookup."
)


def read_hosted_secrets() -> dict[str, Any]:
    """Read Streamlit-managed secrets without displaying values or setup details."""

    try:
        # Streamlit Community Cloud injects its configured values through st.secrets.
        return dict(st.secrets)
    except StreamlitSecretNotFoundError:
        # A missing local secrets file is expected when local .env configuration is used.
        return {}


def api_error_presentation(error: APIClientError, *, action: str) -> tuple[str, str]:
    """Map safe API failures to concise operator guidance without raw response text."""

    # Separate an interaction whose write outcome is uncertain from a read failure.
    if error.write_outcome_uncertain:
        message = (
            "The interaction outcome is uncertain. It may still be running or already saved. "
            "Avoid resubmitting; refresh history for this conversation. An early history read "
            "may not include work that is still running."
        )
        level = "warning"
    elif error.code == "client_timeout" and action == "conversation":
        # A lost generated ID can be replaced only after the user explicitly retries.
        message = "Conversation creation timed out. Retry conversation creation when the API is reachable."
        level = "warning"
    elif error.code == "client_timeout":
        # A timed-out history GET has no write outcome, so it can be safely refreshed later.
        message = "The history request timed out. Refresh history again when the API is reachable."
        level = "warning"
    elif error.code == "connection_error":
        # A lost connection can follow a POST, but GET history has no write side effect.
        message = "The API could not be reached. Check the service and retry the conversation action explicitly."
        level = "warning" if action in {"history", "conversation"} else "error"
    elif error.status_code == 401:
        message = "The backend rejected access. The operator should check the API token configuration."
        level = "error"
    elif error.status_code == 409:
        # Preserve the backend's safe distinction between active and inconsistent conversations.
        message = f"Conversation conflict: {error.message}"
        level = "error"
    elif error.status_code in {413, 422}:
        message = f"The request needs correction: {error.message}"
        level = "error"
    elif error.status_code == 429:
        message = f"The shared operation allowance is reached. Wait before trying again. {error.message}"
        level = "warning"
    elif error.status_code == 503:
        message = f"The demo is busy and cannot accept this operation now. {error.message}"
        level = "warning"
    elif error.status_code == 502:
        if action == "history":
            message = "A dependency did not complete the history read. Refresh history later."
        else:
            message = (
                "A dependency did not complete the interaction. Its outcome may be uncertain; "
                "check history before deciding whether to submit again."
            )
        level = "warning"
    elif error.status_code == 504:
        # A 504 history request did not write; a 504 POST is covered by uncertainty above.
        message = "The history request expired. Refresh history before deciding what to do next."
        level = "warning"
    elif error.code == "invalid_response":
        message = "The API returned an unexpected response. Do not assume the operation succeeded."
        level = "error"
    else:
        message = error.message
        level = "error"

    # Attach only the API's safe error code, status, and request ID from the client exception.
    error_code = f" Code: {error.code}." if error.code else ""
    status = f" (HTTP {error.status_code})" if error.status_code is not None else ""
    request_id = f" Request ID: {error.request_id}" if error.request_id else ""
    return level, f"{message}{error_code}{status}{request_id}"


def show_api_error(error: APIClientError, *, action: str) -> None:
    """Render only status-based guidance and safe API request references."""

    # Choose one native alert based on the mapper's fixed display level.
    level, message = api_error_presentation(error, action=action)
    if level == "warning":
        st.warning(message)
    else:
        st.error(message)


def error_is_current(
    error_record: dict[str, Any],
    *,
    user_id: str,
    conversation_id: str,
    role: str,
    department: str,
    question: str,
) -> bool:
    """Check that an error still belongs to the visible conversation and form input."""

    # History is keyed only by the selected conversation identifiers.
    identifiers_match = (
        error_record["user_id"], error_record["conversation_id"]
    ) == (user_id, conversation_id)
    if not identifiers_match:
        return False
    if error_record["action"] == "history":
        return True

    # Interaction errors also depend on the selected role, department, and question.
    return (
        error_record["role"],
        error_record["department"],
        error_record["question"],
    ) == (role, department, question)


def newest_exchanges_first(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build a display-only list with newest question/answer pairs at the top."""

    # The API guarantees complete alternating user/assistant pairs in stored order.
    # Slice each pair into a new list, then reverse only the pair order for display.
    exchanges = [messages[index : index + 2] for index in range(0, len(messages), 2)]
    return [message for exchange in reversed(exchanges) for message in exchange]


def clear_interaction_error() -> None:
    """Drop an interaction error when its submitted controls change."""

    # A history error belongs to identifiers only and remains visible if role changes.
    error_record = st.session_state.get("last_error")
    if error_record and error_record.get("action") == "interaction":
        st.session_state["last_error"] = None


def submit_demo_password(expected_password: str) -> None:
    """Check the password in a form callback, then immediately clear its widget value."""

    # Read the submitted widget value only during this callback and compare in constant time.
    submitted_password = st.session_state.get("demo_password_input", "")
    password_matches = hmac.compare_digest(
        submitted_password.encode("utf-8"),
        expected_password.encode("utf-8"),
    )

    # Store only the result of the check and erase the typed password immediately.
    st.session_state["authenticated"] = password_matches
    st.session_state["password_rejected"] = not password_matches
    st.session_state["demo_password_input"] = ""


def current_settings() -> RuntimeSettings | None:
    """Load configuration from Streamlit secrets and the parent local .env file."""

    try:
        # Secret values stay on the Python server and are never passed into a widget.
        return load_runtime_settings(read_hosted_secrets())
    except ValueError:
        # Give operators a safe configuration message without naming secret values.
        st.error("The demo settings are not configured correctly. Check the server configuration.")
        return None


# Resolve server-side configuration before showing the password form or protected page.
settings = current_settings()
if settings is None:
    st.stop()

# Keep only the successful gate state in this Streamlit user's session.
st.session_state.setdefault("authenticated", False)

# Require a shared demo password before rendering any API action or protected content.
if not st.session_state["authenticated"]:
    with st.form("demo_password_form"):
        # Keep the password only in its native widget until the callback compares it.
        st.text_input("Demo password", type="password", key="demo_password_input")
        st.form_submit_button(
            "Continue",
            type="primary",
            key="demo_password_submit",
            on_click=submit_demo_password,
            args=(settings.demo_password,),
        )

    if not st.session_state["authenticated"]:
        # Show one generic rejection after the callback has cleared the typed value.
        if st.session_state.pop("password_rejected", False):
            st.error("The demo password was not accepted.")

        # Stop before protected UI or API activity until the password is accepted.
        st.stop()

    # Remove the one-run rejection flag after a successful login.
    st.session_state.pop("password_rejected", None)

# Initialize displayed records once so ordinary reruns do not repeat API writes.
st.session_state.setdefault("last_result", None)
st.session_state.setdefault("last_error", None)
st.session_state.setdefault("last_history", None)
st.session_state.setdefault("active_interaction_inputs", None)
st.session_state.setdefault("clear_question_before_render", False)
st.session_state.setdefault("auto_refresh_history", False)
st.session_state.setdefault("active_user_id", None)
st.session_state.setdefault("conversation_id", None)
st.session_state.setdefault("conversation_creation_blocked", False)
st.session_state.setdefault("conversation_creation_error", None)
st.session_state.setdefault("conversation_rotation_error", None)

# Streamlit requires widget state changes before that widget is created in the run.
# A confirmed POST schedules one rerun so the question can be cleared safely.
preserve_success_result = False
if st.session_state.pop("clear_question_before_render", False):
    st.session_state["question"] = ""
    if st.session_state["last_result"]:
        st.session_state["last_result"]["question"] = ""
    preserve_success_result = True

# Let the demo user remain editable; changing it starts a fresh conversation below.
st.subheader("Conversation")
user_id = st.text_input("Synthetic user ID", value="demo_user_001", key="user_id")

# Clear the old user's visible context before requesting an identifier for the new user.
user_changed = st.session_state["active_user_id"] != user_id
if user_changed:
    st.session_state["active_user_id"] = user_id
    st.session_state["conversation_id"] = None
    st.session_state["conversation_creation_blocked"] = False
    st.session_state["conversation_creation_error"] = None
    st.session_state["conversation_rotation_error"] = None
    st.session_state["last_result"] = None
    st.session_state["last_history"] = None
    st.session_state["last_error"] = None
    st.session_state["active_interaction_inputs"] = None
    st.session_state["question"] = ""


def request_conversation_id() -> tuple[str | None, APIClientError | None]:
    """Make one bodyless request and return either its validated ID or safe error."""

    try:
        # Scope and close a single HTTP client around this explicit creation action.
        with ShopUNowAPIClient(settings.api_base_url, settings.api_token) as client:
            return client.create_conversation(), None
    except APIClientError as error:
        # Return only the already-sanitized client error for safe page rendering.
        return None, error


# Create once after authentication or a user switch; an error waits for a button press.
if (
    st.session_state["conversation_id"] is None
    and not st.session_state["conversation_creation_blocked"]
):
    new_id, creation_error = request_conversation_id()
    if new_id is not None:
        st.session_state["conversation_id"] = new_id
    else:
        st.session_state["conversation_creation_blocked"] = True
        st.session_state["conversation_creation_error"] = creation_error

# Show only the opaque identifier; it cannot be edited or used as authorization.
conversation_id = st.session_state["conversation_id"]
st.caption("Conversation ID")
st.code(conversation_id or "Not available", language=None)

# A successful rotation starts a clean thread; a failed rotation preserves the old one.
if conversation_id is not None and st.button("New conversation", key="new_conversation"):
    new_id, rotation_error = request_conversation_id()
    if new_id is not None:
        st.session_state["conversation_id"] = new_id
        st.session_state["last_result"] = None
        st.session_state["last_history"] = None
        st.session_state["last_error"] = None
        st.session_state["active_interaction_inputs"] = None
        st.session_state["conversation_rotation_error"] = None
        st.session_state["question"] = ""
        conversation_id = new_id
        # Rerun so the readonly display above reflects the new session identifier.
        st.rerun()
    else:
        st.session_state["conversation_rotation_error"] = rotation_error

# Let an initial or user-switch creation failure recover only after explicit action.
if st.session_state["conversation_id"] is None:
    if st.session_state["conversation_creation_blocked"] and st.button(
        "Retry conversation creation", key="retry_conversation_creation"
    ):
        new_id, creation_error = request_conversation_id()
        if new_id is not None:
            st.session_state["conversation_id"] = new_id
            st.session_state["conversation_creation_blocked"] = False
            st.session_state["conversation_creation_error"] = None
            st.session_state["last_result"] = None
            st.session_state["last_history"] = None
            st.session_state["last_error"] = None
            st.session_state["question"] = ""
            conversation_id = new_id
            # Rerun so the readonly display above reflects the retried identifier.
            st.rerun()
        else:
            st.session_state["conversation_creation_error"] = creation_error

    # Show both initial and explicitly retried creation failures in the same run.
    if st.session_state["conversation_creation_error"] is not None:
        show_api_error(st.session_state["conversation_creation_error"], action="conversation")

if st.session_state["conversation_rotation_error"] is not None:
    show_api_error(st.session_state["conversation_rotation_error"], action="conversation")

# Use API-supported values directly and let FastAPI enforce its canonical contract.
role = st.selectbox(
    "Role",
    options=("employee", "customer"),
    key="role",
)
department = st.selectbox(
    "Department",
    options=("hr", "it_support", "billing", "shipping"),
    key="department",
)

# Batch the question and its explicit submit action to prevent rerun-triggered writes.
st.subheader("Ask a question")
with st.form("question_form"):
    # Limit the browser input to the API's documented 2,000-character maximum.
    question = st.text_area(
        "Question",
        max_chars=2000,
        placeholder="Enter a question relevant to the selected role and department.",
        key="question",
    )
    interaction_submitted = st.form_submit_button(
        "Submit question",
        type="primary",
        key="question_submit",
        disabled=conversation_id is None,
    )

# Clear saved display state when an operator changes the selected conversation.
current_identifiers = (user_id, conversation_id)

# Do not mislabel a result or error after role, department, or question changes.
current_interaction_inputs = (user_id, conversation_id, role, department, question)
previous_interaction_inputs = st.session_state["active_interaction_inputs"]
if (
    previous_interaction_inputs is not None
    and previous_interaction_inputs != current_interaction_inputs
):
    if not preserve_success_result:
        st.session_state["last_result"] = None
    clear_interaction_error()
st.session_state["active_interaction_inputs"] = current_interaction_inputs

if interaction_submitted:
    # Reject blank input locally so it does not consume a backend operation slot.
    if not question.strip():
        st.session_state["last_error"] = {
            "action": "interaction",
            "user_id": user_id,
            "conversation_id": conversation_id,
            "role": role,
            "department": department,
            "question": question,
            "detail": "Enter a question containing at least one non-space character.",
        }
        st.session_state["last_result"] = None
    else:
        # Bind this result to the exact selectors used for its one requested POST.
        with st.spinner("Requesting one FAQ lookup from the API..."):
            try:
                # Create and close one HTTPX client for this explicit form submission.
                with ShopUNowAPIClient(settings.api_base_url, settings.api_token) as client:
                    api_result = client.create_interaction(
                        user_id=user_id,
                        conversation_id=conversation_id,
                        role=role,
                        department=department,
                        question=question,
                    )

                # Save only the safe response and the identifiers it belongs to.
                st.session_state["last_result"] = {
                    "user_id": user_id,
                    "conversation_id": conversation_id,
                    "role": role,
                    "department": department,
                    "question": question,
                    "body": api_result,
                }
                st.session_state["last_error"] = None
                # Clear only after confirmed success, then make one read-only history request.
                st.session_state["clear_question_before_render"] = True
                st.session_state["auto_refresh_history"] = True
                st.rerun()
            except APIClientError as error:
                # Keep error state scoped to the same identifiers and avoid raw details.
                st.session_state["last_error"] = {
                    "action": "interaction",
                    "user_id": user_id,
                    "conversation_id": conversation_id,
                    "role": role,
                    "department": department,
                    "question": question,
                    "detail": error,
                }
                st.session_state["last_result"] = None

# Render an interaction only while the selected IDs still match its saved result.
saved_result = st.session_state["last_result"]
if saved_result:
    result_scope = (
        saved_result["user_id"],
        saved_result["conversation_id"],
        saved_result["role"],
        saved_result["department"],
        saved_result["question"],
    )
    if result_scope == current_interaction_inputs:
        # Use the existing FAQ response and identifier; this app does not generate answers.
        st.subheader("FAQ result")
        result_body = saved_result["body"]
        st.write(result_body.get("response", "No matching FAQ."))
        selected_faq_id = result_body.get("selected_faq_id")
        if selected_faq_id:
            st.caption(f"Selected FAQ reference: {selected_faq_id}")
    else:
        # Hide old results when a user changes inputs without submitting a new request.
        st.session_state["last_result"] = None

# Read history after a successful POST or an explicit manual refresh. Ordinary reruns
# remain read-only and never repeat an interaction POST.
st.subheader("Saved history")
st.caption(
    "The API retains up to 50 complete message records, usually about 25 complete "
    "interactions; partial writes can reduce that number. History refreshes after a "
    "successful lookup, or when you choose Refresh history."
)
history_error = st.session_state.get("last_error")
if (
    history_error
    and history_error.get("action") == "interaction"
    and isinstance(history_error.get("detail"), APIClientError)
    and history_error["detail"].write_outcome_uncertain
):
    st.caption(
        "The history shown is the last successful snapshot and may not include the "
        "uncertain interaction. Refresh history to check."
    )
manual_history_refresh = st.button(
    "Refresh history",
    key="refresh_history",
    disabled=conversation_id is None,
)
automatic_history_refresh = st.session_state.pop("auto_refresh_history", False)
if conversation_id is not None and (manual_history_refresh or automatic_history_refresh):
    # Read only the currently selected synthetic user/conversation pair once.
    with st.spinner("Reading saved history from the API..."):
        try:
            # Own and close one HTTPX client for this explicit GET action.
            with ShopUNowAPIClient(settings.api_base_url, settings.api_token) as client:
                history_body = client.read_history(
                    user_id=user_id,
                    conversation_id=conversation_id,
                )

            # Retain only the response object and the IDs it represents.
            st.session_state["last_history"] = {
                "user_id": user_id,
                "conversation_id": conversation_id,
                "body": history_body,
            }
            st.session_state["last_error"] = None
        except APIClientError as error:
            # Keep history failures safe and scoped to the selected conversation.
            st.session_state["last_error"] = {
                "action": "history",
                "user_id": user_id,
                "conversation_id": conversation_id,
                "role": role,
                "department": department,
                "question": question,
                "detail": error,
            }
            # Keep the last known transcript visible if this refresh fails.

# Show complete history newest exchange first without changing the saved API response.
saved_history = st.session_state["last_history"]
if saved_history:
    history_scope = (saved_history["user_id"], saved_history["conversation_id"])
    if history_scope == current_identifiers:
        history_body = saved_history["body"]
        messages = history_body.get("messages", [])
        if history_body.get("found") and messages:
            # Keep the source sequence intact; this new list changes only display order.
            display_messages = newest_exchanges_first(messages)
            st.caption("Newest exchanges first")
            for message in display_messages:
                # Keep each exchange's question above its paired answer.
                with st.chat_message(message.get("speaker", "assistant")):
                    st.write(message.get("text", ""))
                    st.caption(f"Sequence {message.get('sequence', 'unknown')}")
        else:
            # Make the API's empty-history result explicit to the operator.
            st.info("No saved messages were found for this conversation.")
    else:
        # Clear stale transcripts instead of showing them below a different selection.
        st.session_state["last_history"] = None

# Render each action's safe error after all controls so failures appear on the same run.
saved_error = st.session_state["last_error"]
if saved_error and error_is_current(
    saved_error,
    user_id=user_id,
    conversation_id=conversation_id,
    role=role,
    department=department,
    question=question,
):
    error_detail = saved_error["detail"]
    if isinstance(error_detail, APIClientError):
        show_api_error(error_detail, action=saved_error["action"])
    else:
        st.warning(error_detail)

# Overall flow: load server secrets, gate the UI, collect stable selectors, send exactly
# one API request per explicit action, and render its bounded FAQ/history result locally.
