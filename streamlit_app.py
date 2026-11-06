
import json
import urllib.request
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode

import streamlit as st

st.set_page_config(
    page_title="AI Employee",
    page_icon="🤖",
    layout="centered",
)

# PRIVATE OWNER-ONLY POC.
# Do not use this fixed user ID for multiple customers.
TEST_USER_ID = "ai-employee-poc-owner"
APP_URL = "https://ai-employee-poc.streamlit.app"
API_BASE = "https://backend.composio.dev/api/v3.1"

APPS = {
    "Google Analytics": {
        "toolkit": "google_analytics",
        "secret": "GA4_AUTH_CONFIG_ID",
    },
    "Google Ads": {
        "toolkit": "googleads",
        "secret": "GOOGLE_ADS_AUTH_CONFIG_ID",
    },
    "HubSpot": {
        "toolkit": "hubspot",
        "secret": "HUBSPOT_AUTH_CONFIG_ID",
    },
}


# -------------------------------------
# COMPOSIO API
# -------------------------------------

def api_request(path, body=None, query=None):
    url = API_BASE + path

    if query:
        url += "?" + urlencode(query, doseq=True)

    headers = {
        "x-api-key": st.secrets["COMPOSIO_API_KEY"],
        "Content-Type": "application/json",
    }

    request = urllib.request.Request(
        url,
        data=(
            json.dumps(body).encode("utf-8")
            if body is not None else None
        ),
        headers=headers,
        method="POST" if body is not None else "GET",
    )

    try:
        with urllib.request.urlopen(
            request, timeout=45
        ) as response:
            return json.load(response)

    except HTTPError as error:
        try:
            details = json.loads(
                error.read().decode("utf-8")
            )
            info = details.get("error", {})
            message = (
                info.get("message", str(info))
                if isinstance(info, dict)
                else str(info)
            )
        except (ValueError, UnicodeDecodeError):
            message = "No error details returned"

        raise RuntimeError(
            f"Composio HTTP {error.code}: {message}"
        ) from None

    except (URLError, TimeoutError):
        raise RuntimeError(
            "Network error while contacting Composio"
        ) from None


# -------------------------------------
# CONNECTED ACCOUNTS
# -------------------------------------

def get_connections(toolkit):
    result = api_request(
        "/connected_accounts",
        query={
            "user_ids": [TEST_USER_ID],
            "toolkit_slugs": [toolkit],
            "statuses": ["ACTIVE"],
            "limit": 100,
        },
    )

    # Verify ownership even if API filters change.
    return [
        item for item in result.get("items", [])
        if item.get("user_id") == TEST_USER_ID
        and item.get("status") == "ACTIVE"
        and item.get("toolkit", {}).get("slug") == toolkit
    ]


def create_connect_link(auth_config_id):
    result = api_request(
        "/connected_accounts/link",
        body={
            "user_id": TEST_USER_ID,
            "auth_config_id": auth_config_id,
            "callback_url": APP_URL,
        },
    )
    return result["redirect_url"]


# -------------------------------------
# GA4 ACCOUNT & PROPERTY SELECTION
# -------------------------------------

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
    # A session is restricted to the selected
    # user's Google Analytics connection.
    session = api_request(
        "/tool_router/session",
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
            f"/tool_router/session/{session_id}/execute",
            body={
                "tool_slug":
                    "GOOGLE_ANALYTICS_LIST_ACCOUNT_SUMMARIES",
                "arguments": args,
            },
        )

        if result.get("result_type") != "completed":
            raise RuntimeError(
                result.get("error")
                or "Unable to list GA4 properties"
            )

        data = unwrap_data(result.get("data", {}))

        if not isinstance(data, dict):
            raise RuntimeError(
                "Unexpected Google Analytics response"
            )

        if data.get("successful") is False:
            raise RuntimeError(
                str(data.get("error") or "GA4 request failed")
            )

        accounts = data.get("accountSummaries", [])

        for account in accounts:
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


# -------------------------------------
# STREAMLIT INTERFACE
# -------------------------------------

st.title("🤖 AI Employee")
st.caption("Your personal AI-powered assistant")

tab1, tab2 = st.tabs([
    "🔌 Integrations",
    "💬 AI Chat",
])

with tab1:
    st.subheader("Connect your data sources")

    st.info(
        "Private proof of concept: one test user only."
    )

    if "connect_links" not in st.session_state:
        st.session_state.connect_links = {}

    for app_name, config in APPS.items():
        with st.container(border=True):
            st.subheader(app_name)

            toolkit = config["toolkit"]

            try:
                connections = get_connections(toolkit)
            except RuntimeError as error:
                st.error(str(error))
                connections = []

            if connections:
                st.success("Connected")

                if toolkit == "google_analytics":
                    if st.button(
                        "Load GA4 Properties",
                        key="load_ga4",
                    ):
                        try:
                            with st.spinner(
                                "Loading your GA4 properties..."
                            ):
                                properties = load_ga4_properties(
                                    connections[0]["id"]
                                )

                            st.session_state[
                                "ga4_properties"
                            ] = properties

                        except RuntimeError as error:
                            st.error(str(error))
            else:
                if st.button(
                    f"Connect {app_name}",
                    key=f"connect_{toolkit}",
                ):
                    try:
                        url = create_connect_link(
                            st.secrets[config["secret"]]
                        )
                        st.session_state[
                            "connect_links"
                        ][toolkit] = url
                    except RuntimeError as error:
                        st.error(str(error))

                if toolkit in st.session_state["connect_links"]:
                    st.link_button(
                        f"Authorize {app_name}",
                        st.session_state[
                            "connect_links"
                        ][toolkit],
                    )
                    st.caption(
                        "Complete authorization, then "
                        "refresh this page."
                    )

    # GA4 property selection
    if "ga4_properties" in st.session_state:
        st.divider()
        st.subheader("Select GA4 Properties")

        properties = st.session_state["ga4_properties"]

        if not properties:
            st.warning(
                "No GA4 properties found for this connection."
            )
        else:
            st.success(
                f"Found {len(properties)} GA4 properties"
            )

            options = {
                item["id"]: (
                    f'{item["account"]} / '
                    f'{item["name"]} ({item["id"]})'
                )
                for item in properties
            }

            selected = st.multiselect(
                "Choose properties to analyze",
                options=list(options.keys()),
                format_func=lambda prop_id: options[prop_id],
                key="ga4_selected",
            )

            if st.button("Save selection"):
                if selected:
                    st.session_state[
                        "approved_ga4_properties"
                    ] = selected
                    st.success(
                        f"Saved {len(selected)} properties "
                        "for this test session."
                    )
                else:
                    st.warning(
                        "Please select at least one property."
                    )

            st.caption(
                "Selection is temporary for this POC. "
                "AI data access is not configured yet."
            )


# -------------------------------------
# AI CHAT (PLACEHOLDER)
# -------------------------------------

with tab2:
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
            "The chat is ready. Gemini integration "
            "will be added after property selection."
        )

        with st.chat_message("assistant"):
            st.markdown(response)

        st.session_state.messages.append({
            "role": "assistant",
            "content": response,
        })
