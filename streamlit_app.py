
import json
import urllib.request
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, quote

import streamlit as st

# ==========================================
# APP CONFIGURATION
# ==========================================

st.set_page_config(
    page_title="AI Employee",
    page_icon="🤖",
    layout="centered",
)

# PRIVATE SINGLE-USER TEST ONLY
# Do not invite customers until login and
# per-user authorization are implemented.
TEST_USER_ID = "ai-employee-poc-owner"

APP_URL = "https://ai-employee-poc.streamlit.app"
API_BASE = "https://backend.composio.dev/api/v3.1"

# Google Ads has been removed from the UI.
APPS = {
    "Google Analytics": {
        "toolkit": "google_analytics",
        "secret": "GA4_AUTH_CONFIG_ID",
    },
    "HubSpot": {
        "toolkit": "hubspot",
        "secret": "HUBSPOT_AUTH_CONFIG_ID",
    },
}


# ==========================================
# COMPOSIO API CLIENT
# ==========================================

def api_request(path, method="GET", body=None, query=None):
    url = API_BASE + path

    if query:
        url += "?" + urlencode(query, doseq=True)

    headers = {
        "x-api-key": st.secrets["COMPOSIO_API_KEY"],
        "Content-Type": "application/json",
    }

    payload = (
        json.dumps(body).encode("utf-8")
        if body is not None
        else None
    )

    request = urllib.request.Request(
        url,
        data=payload,
        headers=headers,
        method=method,
    )

    try:
        with urllib.request.urlopen(
            request, timeout=45
        ) as response:
            raw = response.read()

            if not raw:
                return {"success": True}

            return json.loads(raw.decode("utf-8"))

    except HTTPError as error:
        try:
            details = json.loads(
                error.read().decode("utf-8")
            )

            info = details.get("error", {})

            if isinstance(info, dict):
                message = (
                    info.get("message")
                    or details.get("message")
                    or "Unknown API error"
                )
            else:
                message = str(info)

        except (ValueError, UnicodeDecodeError):
            message = "No readable error details"

        raise RuntimeError(
            f"Composio HTTP {error.code}: {message}"
        ) from None

    except (URLError, TimeoutError):
        raise RuntimeError(
            "Network error while contacting Composio"
        ) from None


# ==========================================
# CONNECTED ACCOUNT MANAGEMENT
# ==========================================

def get_connections(toolkit):
    connections = []
    cursor = None

    for _ in range(10):
        params = {
            "user_ids": [TEST_USER_ID],
            "toolkit_slugs": [toolkit],
            "statuses": ["ACTIVE"],
            "limit": 100,
        }

        if cursor:
            params["cursor"] = cursor

        result = api_request(
            "/connected_accounts",
            query=params,
        )

        for account in result.get("items", []):
            account_toolkit = (
                account.get("toolkit") or {}
            ).get("slug")

            if (
                account.get("user_id") == TEST_USER_ID
                and account_toolkit == toolkit
                and account.get("status") == "ACTIVE"
            ):
                connections.append(account)

        cursor = result.get("next_cursor")

        if not cursor:
            break

    return connections


def create_connect_link(auth_config_id):
    result = api_request(
        "/connected_accounts/link",
        method="POST",
        body={
            "user_id": TEST_USER_ID,
            "auth_config_id": auth_config_id,
            "callback_url": APP_URL,
        },
    )

    url = result.get("redirect_url")

    if not url or not url.startswith("https://"):
        raise RuntimeError(
            "Composio did not return a valid authorization URL."
        )

    return url


def disconnect_account(connection_id, toolkit):
    # Verify ownership on the server immediately
    # before deleting the connected account.
    account = api_request(
        "/connected_accounts/"
        + quote(connection_id, safe="")
    )

    owner = account.get("user_id")
    account_toolkit = (
        account.get("toolkit") or {}
    ).get("slug")

    if (
        owner != TEST_USER_ID
        or account_toolkit != toolkit
    ):
        raise RuntimeError(
            "Connection does not belong to this test user."
        )

    result = api_request(
        "/connected_accounts/"
        + quote(connection_id, safe=""),
        method="DELETE",
    )

    if result.get("success") is False:
        raise RuntimeError(
            "Composio did not confirm disconnection."
        )

    return True


def clear_ga4_state():
    keys = [
        "ga4_properties",
        "ga4_selected",
        "approved_ga4_properties",
        "ga4_property_connection",
    ]

    for key in keys:
        st.session_state.pop(key, None)


# ==========================================
# GOOGLE ANALYTICS PROPERTY SELECTION
# ==========================================

def unwrap_data(value):
    for _ in range(4):
        if not isinstance(value, dict):
            break

        if "accountSummaries" in value:
            break

        nested = next(
            (
                value[k]
                for k in ("data", "result", "response")
                if isinstance(value.get(k), dict)
            ),
            None,
        )

        if nested is None:
            break

        value = nested

    return value


def load_ga4_properties(connection_id):
    session = api_request(
        "/tool_router/session",
        method="POST",
        body={
            "user_id": TEST_USER_ID,
            "toolkits": {
                "enable": ["google_analytics"]
            },
            "connected_accounts": {
                "google_analytics": [connection_id]
            },
            "instant": False,
        },
    )

    session_id = session["session_id"]
    properties = []
    page_token = ""

    for _ in range(10):
        args = {"pageSize": 200}

        if page_token:
            args["pageToken"] = page_token

        result = api_request(
            "/tool_router/session/"
            + quote(session_id, safe="")
            + "/execute",
            method="POST",
            body={
                "tool_slug":
                    "GOOGLE_ANALYTICS_LIST_ACCOUNT_SUMMARIES",
                "arguments": args,
            },
        )

        if result.get("result_type") != "completed":
            raise RuntimeError(
                str(
                    result.get("error")
                    or "Failed to load GA4 properties."
                )
            )

        data = unwrap_data(
            result.get("data", {})
        )

        if not isinstance(data, dict):
            raise RuntimeError(
                "Unexpected Google Analytics response."
            )

        if data.get("successful") is False:
            raise RuntimeError(
                str(
                    data.get("error")
                    or "Google Analytics request failed."
                )
            )

        for account in data.get(
            "accountSummaries", []
        ):
            account_name = account.get(
                "displayName", "Unnamed account"
            )

            for prop in account.get(
                "propertySummaries", []
            ):
                resource = prop.get("property", "")
                property_id = resource.split("/")[-1]

                if property_id:
                    properties.append({
                        "id": property_id,
                        "name": prop.get(
                            "displayName", property_id
                        ),
                        "account": account_name,
                    })

        page_token = data.get("nextPageToken", "")

        if not page_token:
            break

    return properties


# ==========================================
# MAIN INTERFACE
# ==========================================

st.title("🤖 AI Employee")
st.caption("Your personal AI-powered data assistant")

st.warning(
    "Private testing mode. "
    "Do not share this app with other customers yet."
)

if "connect_links" not in st.session_state:
    st.session_state.connect_links = {}

if "notice" in st.session_state:
    st.success(st.session_state.pop("notice"))

tab_integrations, tab_chat = st.tabs([
    "🔌 Integrations",
    "💬 AI Chat",
])


# ==========================================
# INTEGRATIONS PAGE
# ==========================================

with tab_integrations:
    st.subheader("Connect your data sources")

    ga4_connections = []

    for app_name, config in APPS.items():
        toolkit = config["toolkit"]

        with st.container(border=True):
            st.subheader(app_name)

            try:
                connections = get_connections(toolkit)
            except (RuntimeError, KeyError) as error:
                st.error(str(error))
                continue

            if toolkit == "google_analytics":
                ga4_connections = connections

            # ----------------------------------
            # CONNECTED STATE
            # ----------------------------------

            if connections:
                st.session_state["connect_links"].pop(
                    toolkit, None
                )

                st.success(
                    f"Connected — {len(connections)} account(s)"
                )

                for connection in connections:
                    connection_id = connection["id"]

                    st.caption(
                        f"Connection: {connection_id}"
                    )

                    with st.expander(
                        "Disconnect this account"
                    ):
                        st.warning(
                            "This will remove the connection "
                            "from Composio. You will need to "
                            "authorize again to reconnect."
                        )

                        confirmed = st.checkbox(
                            "I confirm disconnection",
                            key=f"confirm_{connection_id}",
                        )

                        if st.button(
                            f"Disconnect {app_name}",
                            key=f"delete_{connection_id}",
                            disabled=not confirmed,
                        ):
                            try:
                                disconnect_account(
                                    connection_id,
                                    toolkit,
                                )

                                if toolkit == "google_analytics":
                                    clear_ga4_state()

                                st.session_state["notice"] = (
                                    f"{app_name} disconnected."
                                )
                                st.rerun()

                            except RuntimeError as error:
                                st.error(str(error))

                # GA4 properties button
                if toolkit == "google_analytics":
                    active_connection_ids = {
                        c["id"] for c in connections
                    }

                    previous_connection = (
                        st.session_state.get(
                            "ga4_property_connection"
                        )
                    )

                    if (
                        previous_connection is not None
                        and previous_connection
                        not in active_connection_ids
                    ):
                        clear_ga4_state()

                    if st.button(
                        "Load GA4 Properties",
                        key="load_ga4",
                    ):
                        try:
                            connection_id = (
                                connections[0]["id"]
                            )

                            with st.spinner(
                                "Loading GA4 properties..."
                            ):
                                properties = load_ga4_properties(
                                    connection_id
                                )

                            clear_ga4_state()

                            st.session_state[
                                "ga4_properties"
                            ] = properties

                            st.session_state[
                                "ga4_property_connection"
                            ] = connection_id

                        except (RuntimeError, KeyError) as error:
                            st.error(str(error))

            # ----------------------------------
            # NOT CONNECTED STATE
            # ----------------------------------

            else:
                if toolkit == "google_analytics":
                    clear_ga4_state()

                st.info("Not connected")

                if st.button(
                    f"1. Generate {app_name} Connect Link",
                    key=f"connect_{toolkit}",
                ):
                    try:
                        with st.spinner(
                            "Creating authorization link..."
                        ):
                            link = create_connect_link(
                                st.secrets[config["secret"]]
                            )

                        st.session_state[
                            "connect_links"
                        ][toolkit] = link

                        st.success(
                            "Link created! Click the "
                            "Authorize button below."
                        )

                    except (RuntimeError, KeyError) as error:
                        st.error(str(error))

                link = st.session_state[
                    "connect_links"
                ].get(toolkit)

                if link:
                    st.link_button(
                        f"2. Authorize {app_name}",
                        link,
                        type="primary",
                    )

                    st.caption(
                        "Sign in and approve access. "
                        "Return here afterward. "
                        "Links expire after about 10 minutes."
                    )

                    if st.button(
                        "Refresh connection status",
                        key=f"refresh_{toolkit}",
                    ):
                        st.rerun()

    # --------------------------------------
    # GA4 PROPERTY SELECTION
    # --------------------------------------

    if (
        ga4_connections
        and "ga4_properties" in st.session_state
    ):
        st.divider()
        st.subheader("Select GA4 Properties")

        properties = st.session_state[
            "ga4_properties"
        ]

        if not properties:
            st.warning(
                "No properties found for this connection."
            )

        else:
            st.success(
                f"Found {len(properties)} GA4 properties"
            )

            options = {
                item["id"]: (
                    f'{item["account"]} / '
                    f'{item["name"]} '
                    f'({item["id"]})'
                )
                for item in properties
            }

            selected = st.multiselect(
                "Choose properties to analyze",
                options=list(options.keys()),
                format_func=lambda prop_id: options[prop_id],
                key="ga4_selected",
            )

            if st.button(
                "Save selection",
                key="save_ga4",
            ):
                if selected:
                    st.session_state[
                        "approved_ga4_properties"
                    ] = list(selected)

                    st.success(
                        f"Saved {len(selected)} "
                        "properties for this session."
                    )
                else:
                    st.warning(
                        "Select at least one property."
                    )

            st.caption(
                "This selection is temporary. "
                "AI access enforcement will be added later."
            )


# ==========================================
# AI CHAT - PLACEHOLDER
# ==========================================

with tab_chat:
    st.subheader("AI Chat")

    if "messages" not in st.session_state:
        st.session_state.messages = []

    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])

    if prompt := st.chat_input(
        "Ask your AI Employee..."
    ):
        st.session_state.messages.append({
            "role": "user",
            "content": prompt,
        })

        with st.chat_message("user"):
            st.markdown(prompt)

        response = (
            "The chat interface is working. "
            "Gemini and Composio tool execution "
            "will be connected next."
        )

        with st.chat_message("assistant"):
            st.markdown(response)

        st.session_state.messages.append({
            "role": "assistant",
            "content": response,
        })
