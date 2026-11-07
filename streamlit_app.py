
import json
import urllib.request
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, quote

import streamlit as st


# ==========================================
# SETTINGS
# ==========================================

st.set_page_config(
    page_title="AI Employee",
    page_icon="🤖",
    layout="centered",
)

APP_URL = "https://ai-employee-poc.streamlit.app"
COMPOSIO_BASE = "https://backend.composio.dev/api/v3.1"
GEMINI_MODEL = "gemini-3.5-flash-lite"

# WARNING: Single-user private prototype only.
# Replace with authenticated user IDs for a real SaaS.
TEST_USER_ID = "ai-employee-poc-owner"

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

# Explicit read-only tool allowlist.
ALLOWED_TOOLS = {
    "google_analytics": [
        "GOOGLE_ANALYTICS_RUN_REPORT",
        "GOOGLE_ANALYTICS_GET_METADATA",
    ],
    "hubspot": [
        "HUBSPOT_LIST_CONTACTS",
        "HUBSPOT_LIST_DEALS",
        "HUBSPOT_LIST_COMPANIES",
        "HUBSPOT_SEARCH_DEALS",
        "HUBSPOT_SEARCH_COMPANIES",
        "HUBSPOT_SEARCH_CONTACTS_BY_CRITERIA",
    ],
}


# ==========================================
# HTTP HELPER
# ==========================================

def http_json(url, method="GET", body=None, headers=None,
              timeout=60):
    data = (
        json.dumps(body).encode("utf-8")
        if body is not None
        else None
    )

    request = urllib.request.Request(
        url,
        data=data,
        headers=headers or {},
        method=method,
    )

    try:
        with urllib.request.urlopen(
            request, timeout=timeout
        ) as response:
            raw = response.read()
            return json.loads(raw) if raw else {"success": True}

    except HTTPError as error:
        try:
            details = json.loads(
                error.read().decode("utf-8")
            )
            info = details.get("error", details)

            if isinstance(info, dict):
                message = info.get(
                    "message", str(info)
                )
            else:
                message = str(info)

        except (ValueError, UnicodeDecodeError):
            message = "No readable error details"

        raise RuntimeError(
            f"HTTP {error.code}: {message}"
        ) from None

    except (URLError, TimeoutError):
        raise RuntimeError(
            "Network connection failed."
        ) from None


def composio_request(path, method="GET",
                     body=None, query=None):
    url = COMPOSIO_BASE + path

    if query:
        url += "?" + urlencode(query, doseq=True)

    return http_json(
        url,
        method=method,
        body=body,
        headers={
            "x-api-key": st.secrets["COMPOSIO_API_KEY"],
            "Content-Type": "application/json",
        },
    )


# ==========================================
# CONNECTED ACCOUNTS
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

        result = composio_request(
            "/connected_accounts",
            query=params,
        )

        for item in result.get("items", []):
            item_toolkit = (
                item.get("toolkit") or {}
            ).get("slug")

            if (
                item.get("user_id") == TEST_USER_ID
                and item_toolkit == toolkit
                and item.get("status") == "ACTIVE"
            ):
                connections.append(item)

        cursor = result.get("next_cursor")
        if not cursor:
            break

    return connections


def create_connect_link(auth_config_id):
    result = composio_request(
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
            "No valid authorization URL returned."
        )

    return url


def disconnect_account(connection_id, toolkit):
    # Check ownership before deleting a connection.
    account = composio_request(
        "/connected_accounts/"
        + quote(connection_id, safe="")
    )

    account_toolkit = (
        account.get("toolkit") or {}
    ).get("slug")

    if (
        account.get("user_id") != TEST_USER_ID
        or account_toolkit != toolkit
    ):
        raise RuntimeError(
            "Connection ownership verification failed."
        )

    result = composio_request(
        "/connected_accounts/"
        + quote(connection_id, safe=""),
        method="DELETE",
    )

    if result.get("success") is False:
        raise RuntimeError(
            "Composio did not confirm disconnection."
        )


def clear_ga4_state():
    for key in [
        "ga4_properties",
        "ga4_selected",
        "approved_ga4_properties",
        "ga4_property_connection",
        "active_ga4_property",
    ]:
        st.session_state.pop(key, None)


# ==========================================
# COMPOSIO TOOL EXECUTION
# ==========================================

def create_tool_session(toolkit, connection_id):
    result = composio_request(
        "/tool_router/session",
        method="POST",
        body={
            "user_id": TEST_USER_ID,
            "toolkits": {
                "enable": [toolkit]
            },
            "connected_accounts": {
                toolkit: [connection_id]
            },
            "instant": False,
        },
    )

    return result["session_id"]


def execute_tool(tool_slug, arguments,
                 connections, active_property):
    toolkit = None

    for name, allowed in ALLOWED_TOOLS.items():
        if tool_slug in allowed:
            toolkit = name
            break

    if not toolkit:
        raise RuntimeError(
            "This tool is not permitted."
        )

    if toolkit not in connections:
        raise RuntimeError(
            f"{toolkit} is not connected."
        )

    if not isinstance(arguments, dict):
        raise RuntimeError(
            "Invalid tool arguments."
        )

    arguments = arguments.copy()

    # Limit potentially large CRM responses.
    if toolkit == "hubspot":
        for field in ("limit", "count", "pageSize"):
            if field in arguments:
                try:
                    arguments[field] = min(
                        max(int(arguments[field]), 1),
                        100,
                    )
                except (ValueError, TypeError):
                    arguments[field] = 20

    # Enforce the selected GA4 property on the server.
    if toolkit == "google_analytics":
        if not active_property:
            raise RuntimeError(
                "Select and save a GA4 property first."
            )

        property_resource = (
            f"properties/{active_property}"
        )

        arguments["property"] = property_resource

        for field in ("propertyId", "property_id"):
            if field in arguments:
                arguments[field] = active_property

    connection_id = connections[toolkit]["id"]

    # Recheck connection ownership and current status.
    valid_ids = {
        item["id"]
        for item in get_connections(toolkit)
    }

    if connection_id not in valid_ids:
        raise RuntimeError(
            "Connected account is no longer available."
        )

    session_id = create_tool_session(
        toolkit, connection_id
    )

    result = composio_request(
        "/tool_router/session/"
        + quote(session_id, safe="")
        + "/execute",
        method="POST",
        body={
            "tool_slug": tool_slug,
            "arguments": arguments,
        },
    )

    if result.get("error"):
        raise RuntimeError(
            str(result["error"])
        )

    if (
        result.get("result_type")
        and result["result_type"] != "completed"
    ):
        raise RuntimeError(
            f"Tool execution status: "
            f"{result['result_type']}"
        )

    data = result.get("data", result)

    if isinstance(data, dict):
        if data.get("successful") is False:
            raise RuntimeError(
                str(data.get("error", "Tool failed"))
            )

    return data


# ==========================================
# TOOL SCHEMAS
# ==========================================

@st.cache_data(ttl=3600, show_spinner=False)
def get_tool_definition(tool_slug):
    return composio_request(
        "/tools/" + quote(tool_slug, safe=""),
        query={"toolkit_versions": "latest"},
    )


def get_available_tool_definitions(connections,
                                   active_property):
    definitions = {}

    for toolkit in connections:
        if (
            toolkit == "google_analytics"
            and not active_property
        ):
            continue

        for slug in ALLOWED_TOOLS.get(toolkit, []):
            try:
                definitions[slug] = get_tool_definition(
                    slug
                )
            except RuntimeError:
                # Skip unavailable tool versions.
                continue

    return definitions


# ==========================================
# GOOGLE ANALYTICS PROPERTIES
# ==========================================

def unwrap_ga4_data(value):
    for _ in range(5):
        if not isinstance(value, dict):
            break

        if "accountSummaries" in value:
            break

        nested = next(
            (
                value[key]
                for key in ("data", "result", "response")
                if isinstance(value.get(key), dict)
            ),
            None,
        )

        if nested is None:
            break

        value = nested

    return value


def load_ga4_properties(connection_id):
    session_id = create_tool_session(
        "google_analytics",
        connection_id,
    )

    properties = []
    page_token = ""

    for _ in range(10):
        arguments = {"pageSize": 200}

        if page_token:
            arguments["pageToken"] = page_token

        result = composio_request(
            "/tool_router/session/"
            + quote(session_id, safe="")
            + "/execute",
            method="POST",
            body={
                "tool_slug":
                    "GOOGLE_ANALYTICS_LIST_ACCOUNT_SUMMARIES",
                "arguments": arguments,
            },
        )

        if result.get("error"):
            raise RuntimeError(str(result["error"]))

        data = unwrap_ga4_data(
            result.get("data", {})
        )

        if not isinstance(data, dict):
            raise RuntimeError(
                "Unexpected GA4 response."
            )

        if data.get("successful") is False:
            raise RuntimeError(
                str(data.get("error", "GA4 request failed"))
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
                prop_id = resource.split("/")[-1]

                if prop_id:
                    properties.append({
                        "id": prop_id,
                        "name": prop.get(
                            "displayName", prop_id
                        ),
                        "account": account_name,
                    })

        page_token = data.get("nextPageToken", "")

        if not page_token:
            break

    return properties


# ==========================================
# GEMINI API
# ==========================================

def gemini_generate(contents, system_prompt,
                    tool_definitions):
    url = (
        "https://generativelanguage.googleapis.com/"
        f"v1beta/models/{GEMINI_MODEL}:generateContent"
    )

    payload = {
        "contents": contents,
        "systemInstruction": {
            "parts": [{"text": system_prompt}]
        },
        "generationConfig": {
            "temperature": 0.2,
            "maxOutputTokens": 4096,
        },
    }

    if tool_definitions:
        payload["tools"] = [{
            "functionDeclarations": [{
                "name": "execute_read_tool",
                "description": (
                    "Execute one approved read-only "
                    "Composio tool using arguments "
                    "matching its documented schema."
                ),
                "parametersJsonSchema": {
                    "type": "object",
                    "properties": {
                        "tool_slug": {
                            "type": "string",
                            "enum": list(
                                tool_definitions.keys()
                            ),
                        },
                        "arguments_json": {
                            "type": "string",
                            "description": (
                                "A valid JSON object encoded "
                                "as a string, containing the "
                                "chosen tool's input arguments."
                            ),
                        },
                    },
                    "required": [
                        "tool_slug",
                        "arguments_json",
                    ],
                },
            }]
        }]

    return http_json(
        url,
        method="POST",
        body=payload,
        headers={
            "Content-Type": "application/json",
            "x-goog-api-key": st.secrets["GOOGLE_API_KEY"],
        },
        timeout=90,
    )


def ask_ai(messages, connections, active_property):
    definitions = get_available_tool_definitions(
        connections,
        active_property,
    )

    tool_information = {}

    for slug, definition in definitions.items():
        tool_information[slug] = {
            "description": definition.get(
                "description", ""
            ),
            "input_parameters": definition.get(
                "input_parameters", {}
            ),
        }

    system_prompt = f"""
You are AI Employee, a professional business
data analyst.

Current available connected tools and their input
schemas:
{json.dumps(tool_information, ensure_ascii=False)}

Active GA4 property:
{active_property or "None"}

Instructions:
- Respond in the user's language.
- Keep answers short, clear, and professional.
- Use 3-5 sentences by default.
- Provide detailed analysis only when requested.
- Never invent data, metrics, or API results.
- Always use real connected data for analytics.
- If data is missing, clearly say what is missing.
- If a tool fails, report the failure honestly.
- Never assume attribution between data sources.
- Clearly separate facts from interpretations.
- Verify calculations before reporting results.
- Use only authorized accounts and properties.
- Never execute write or delete operations.
- Never claim a task succeeded without confirmation.
- Avoid generic advice and unnecessary explanations.
- Ask questions only when essential.

You may call execute_read_tool multiple times
when comparisons need multiple queries.
"""

    contents = []

    for message in messages[-12:]:
        role = (
            "model"
            if message["role"] == "assistant"
            else "user"
        )

        contents.append({
            "role": role,
            "parts": [{
                "text": message["content"]
            }],
        })

    tool_trace = []
    total_calls = 0

    for _ in range(5):
        result = gemini_generate(
            contents,
            system_prompt,
            definitions,
        )

        candidates = result.get("candidates", [])

        if not candidates:
            raise RuntimeError(
                "Gemini returned no response."
            )

        model_content = candidates[0].get(
            "content", {}
        )

        parts = model_content.get("parts", [])

        calls = [
            part["functionCall"]
            for part in parts
            if "functionCall" in part
        ]

        if not calls:
            text = "\n".join(
                part.get("text", "")
                for part in parts
                if isinstance(part, dict)
            ).strip()

            if text:
                return text, tool_trace

            raise RuntimeError(
                "Gemini returned an empty answer."
            )

        # Preserve all original parts, including
        # Gemini thought signatures.
        contents.append(model_content)

        responses = []

        for call in calls:
            name = call.get("name", "")
            args = call.get("args", {})

            total_calls += 1

            if total_calls > 8:
                output = {
                    "error": "Tool call limit reached."
                }

            elif name != "execute_read_tool":
                output = {
                    "error": "Function not allowed."
                }

            else:
                slug = args.get("tool_slug", "")
                raw_arguments = args.get(
                    "arguments_json", "{}"
                )

                try:
                    if slug not in definitions:
                        raise RuntimeError(
                            "Tool not on the allowlist."
                        )

                    if isinstance(raw_arguments, dict):
                        arguments = raw_arguments
                    else:
                        arguments = json.loads(
                            raw_arguments
                        )

                    data = execute_tool(
                        slug,
                        arguments,
                        connections,
                        active_property,
                    )

                    serialized = json.dumps(
                        data,
                        ensure_ascii=False,
                        default=str,
                    )

                    # Limit data forwarded to the LLM.
                    if len(serialized) > 20000:
                        output = {
                            "data_excerpt": serialized[:20000],
                            "truncated": True,
                        }
                    else:
                        output = {"data": data}

                    tool_trace.append(slug)

                except (
                    RuntimeError,
                    ValueError,
                    TypeError,
                    KeyError,
                ) as error:
                    output = {
                        "error": str(error)
                    }

                    tool_trace.append(
                        f"{slug}: failed"
                    )

            response_part = {
                "name": name,
                "response": output,
            }

            if call.get("id"):
                response_part["id"] = call["id"]

            responses.append({
                "functionResponse": response_part
            })

        contents.append({
            "role": "user",
            "parts": responses,
        })

    return (
        "The tool execution limit was reached. "
        "Try a smaller or more specific request.",
        tool_trace,
    )


# ==========================================
# MAIN APP
# ==========================================

st.title("🤖 AI Employee")
st.caption("Your AI-powered business data analyst")

st.warning(
    "Private prototype: one test user only. "
    "Do not use customer data in this version."
)

if "connect_links" not in st.session_state:
    st.session_state.connect_links = {}

tab_integrations, tab_chat = st.tabs([
    "🔌 Integrations",
    "💬 AI Chat",
])

active_connections = {}


# ==========================================
# INTEGRATIONS PAGE
# ==========================================

with tab_integrations:
    st.subheader("Connect your data sources")

    for app_name, config in APPS.items():
        toolkit = config["toolkit"]

        with st.container(border=True):
            st.subheader(app_name)

            try:
                accounts = get_connections(toolkit)
            except (RuntimeError, KeyError) as error:
                st.error(str(error))
                continue

            if accounts:
                st.success(
                    f"Connected — {len(accounts)} account(s)"
                )

                if len(accounts) > 1:
                    labels = {
                        a["id"]: a["id"]
                        for a in accounts
                    }

                    chosen_id = st.selectbox(
                        f"Choose {app_name} connection",
                        list(labels.keys()),
                        key=f"active_{toolkit}",
                    )

                    account = next(
                        a for a in accounts
                        if a["id"] == chosen_id
                    )
                else:
                    account = accounts[0]

                active_connections[toolkit] = account

                with st.expander("Disconnect account"):
                    confirmed = st.checkbox(
                        "I confirm disconnection",
                        key=f"confirm_{account['id']}",
                    )

                    if st.button(
                        "Disconnect",
                        key=f"disconnect_{account['id']}",
                        disabled=not confirmed,
                    ):
                        try:
                            disconnect_account(
                                account["id"], toolkit
                            )

                            if toolkit == "google_analytics":
                                clear_ga4_state()

                            st.success("Disconnected.")
                            st.rerun()

                        except RuntimeError as error:
                            st.error(str(error))

                if toolkit == "google_analytics":
                    previous_connection = (
                        st.session_state.get(
                            "ga4_property_connection"
                        )
                    )

                    if (
                        previous_connection
                        and previous_connection != account["id"]
                    ):
                        clear_ga4_state()

                    if st.button(
                        "Load GA4 Properties",
                        key="load_ga4",
                    ):
                        try:
                            with st.spinner(
                                "Loading GA4 properties..."
                            ):
                                props = load_ga4_properties(
                                    account["id"]
                                )

                            clear_ga4_state()

                            st.session_state[
                                "ga4_properties"
                            ] = props

                            st.session_state[
                                "ga4_property_connection"
                            ] = account["id"]

                        except (RuntimeError, KeyError) as error:
                            st.error(str(error))

            else:
                st.info("Not connected")

                if toolkit == "google_analytics":
                    clear_ga4_state()

                if st.button(
                    f"Generate {app_name} Connect Link",
                    key=f"connect_{toolkit}",
                ):
                    try:
                        link = create_connect_link(
                            st.secrets[config["secret"]]
                        )

                        st.session_state[
                            "connect_links"
                        ][toolkit] = link

                    except (RuntimeError, KeyError) as error:
                        st.error(str(error))

                link = st.session_state[
                    "connect_links"
                ].get(toolkit)

                if link:
                    st.link_button(
                        f"Authorize {app_name}",
                        link,
                        type="primary",
                    )

                    if st.button(
                        "Refresh connection status",
                        key=f"refresh_{toolkit}",
                    ):
                        st.rerun()

    # GA4 property selection
    if (
        "google_analytics" in active_connections
        and "ga4_properties" in st.session_state
    ):
        st.divider()
        st.subheader("Select GA4 Properties")

        properties = st.session_state[
            "ga4_properties"
        ]

        options = {
            item["id"]: (
                f'{item["account"]} / '
                f'{item["name"]} '
                f'({item["id"]})'
            )
            for item in properties
        }

        st.success(
            f"Found {len(properties)} GA4 properties"
        )

        selected = st.multiselect(
            "Choose properties to analyze",
            options=list(options.keys()),
            format_func=lambda p: options[p],
            key="ga4_selected",
        )

        if st.button("Save selection"):
            if selected:
                st.session_state[
                    "approved_ga4_properties"
                ] = list(selected)

                st.success(
                    f"Saved {len(selected)} properties."
                )
            else:
                st.warning(
                    "Select at least one property."
                )


# ==========================================
# AI CHAT PAGE
# ==========================================

with tab_chat:
    st.subheader("AI Chat")
    st.caption(f"Powered by {GEMINI_MODEL}")

    approved = st.session_state.get(
        "approved_ga4_properties", []
    )

    active_property = None

    if (
        "google_analytics" in active_connections
        and approved
    ):
        active_property = st.selectbox(
            "GA4 property for this conversation",
            options=approved,
            key="active_ga4_property",
        )

    if "hubspot" in active_connections:
        st.success("HubSpot tools available")

    if active_property:
        st.success(
            f"GA4 connected — Property {active_property}"
        )

    if "messages" not in st.session_state:
        st.session_state.messages = []

    if st.button("Clear conversation"):
        st.session_state.messages = []
        st.rerun()

    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])

    if prompt := st.chat_input(
        "Ask your AI Employee..."
    ):
        conversation = (
            st.session_state.messages
            + [{
                "role": "user",
                "content": prompt,
            }]
        )

        with st.chat_message("user"):
            st.markdown(prompt)

        with st.chat_message("assistant"):
            with st.spinner(
                "Analyzing your request..."
            ):
                try:
                    answer, tools_used = ask_ai(
                        conversation,
                        active_connections,
                        active_property,
                    )

                    st.markdown(answer)

                    if tools_used:
                        with st.expander(
                            "Tools used"
                        ):
                            for tool in tools_used:
                                st.code(tool)

                    st.session_state.messages.extend([
                        {
                            "role": "user",
                            "content": prompt,
                        },
                        {
                            "role": "assistant",
                            "content": answer,
                        },
                    ])

                except RuntimeError as error:
                    st.error(str(error))
