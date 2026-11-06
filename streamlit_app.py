
import json
import urllib.request
from urllib.error import HTTPError, URLError

import streamlit as st

# ==========================================
# PAGE SETTINGS
# ==========================================

st.set_page_config(
    page_title="AI Employee",
    page_icon="🤖",
    layout="centered"
)

# Single-user testing only.
# A real login system is required before inviting customers.
TEST_USER_ID = "ai-employee-poc-owner"

COMPOSIO_URL = (
    "https://backend.composio.dev"
    "/api/v3.1/connected_accounts/link"
)

APPS = {
    "Google Analytics": "GA4_AUTH_CONFIG_ID",
    "Google Ads": "GOOGLE_ADS_AUTH_CONFIG_ID",
    "HubSpot": "HUBSPOT_AUTH_CONFIG_ID",
}


# ==========================================
# COMPOSIO CONNECTION
# ==========================================

def create_connect_link(auth_config_id):

    payload = json.dumps({
        "user_id": TEST_USER_ID,
        "auth_config_id": auth_config_id,
        "callback_url": "https://ai-employee-poc.streamlit.app"
    }).encode("utf-8")

    request = urllib.request.Request(
        COMPOSIO_URL,
        data=payload,
        headers={
            "x-api-key": st.secrets["COMPOSIO_API_KEY"],
            "Content-Type": "application/json",
        },
        method="POST",
    )

    with urllib.request.urlopen(
        request,
        timeout=30
    ) as response:

        result = json.load(response)
        return result["redirect_url"]


# ==========================================
# ERROR HANDLING
# ==========================================

def get_error_message(error):

    try:
        raw_response = error.read().decode(
            "utf-8", errors="replace"
        )

        details = json.loads(raw_response)

        error_info = details.get("error", details)

        if isinstance(error_info, dict):
            message = (
                error_info.get("message")
                or error_info.get("code")
                or details.get("message")
                or "Unknown API error"
            )
        else:
            message = str(error_info)

        return str(message)

    except (ValueError, TypeError, AttributeError):
        return "No readable error details returned by Composio."


# ==========================================
# MAIN INTERFACE
# ==========================================

st.title("🤖 AI Employee")

st.caption(
    "Your personal AI-powered data assistant"
)

tab1, tab2 = st.tabs([
    "🔌 Integrations",
    "💬 AI Chat"
])


# ==========================================
# INTEGRATIONS
# ==========================================

with tab1:

    st.subheader("Connect your data sources")

    st.write(
        "Choose an app to connect your account."
    )

    if "connect_links" not in st.session_state:
        st.session_state.connect_links = {}

    for app_name, secret_name in APPS.items():

        with st.container(border=True):

            st.subheader(app_name)

            if st.button(
                f"Connect {app_name}",
                key=f"connect_{secret_name}",
            ):

                try:

                    with st.spinner(
                        "Preparing connection..."
                    ):

                        auth_config_id = st.secrets[
                            secret_name
                        ]

                        url = create_connect_link(
                            auth_config_id
                        )

                        st.session_state.connect_links[
                            app_name
                        ] = url

                except HTTPError as error:

                    message = get_error_message(error)

                    st.error(
                        f"Composio HTTP {error.code}: {message}"
                    )

                except (URLError, TimeoutError):

                    st.error(
                        "Network error while contacting Composio."
                    )

                except KeyError:

                    st.error(
                        "Missing configuration or invalid API response."
                    )

                except Exception:

                    st.error(
                        "Unexpected connection error. "
                        "Check your Streamlit app logs."
                    )

            if app_name in st.session_state.connect_links:

                st.link_button(
                    f"Authorize {app_name}",
                    st.session_state.connect_links[
                        app_name
                    ],
                )

                st.caption(
                    "Complete authorization in the new tab. "
                    "If the link expires, click Connect again."
                )


# ==========================================
# AI CHAT
# ==========================================

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
            "The chat interface is ready. "
            "AI integration will be added next."
        )

        with st.chat_message("assistant"):
            st.markdown(response)

        st.session_state.messages.append({
            "role": "assistant",
            "content": response,
        })
