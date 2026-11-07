# RELEASE_BUILD: AI_EMPLOYEE_UI_V4_USAGE_2026_11_07_VERIFIED
# Gemini usage ledger, business overview, client usage charts, inline integration management.
import json
import time
import html
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from zoneinfo import ZoneInfo
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
    layout="wide",
    initial_sidebar_state="expanded",
)

APP_URL = "https://ai-employee-poc.streamlit.app"
COMPOSIO_BASE = "https://backend.composio.dev/api/v3.1"
GEMINI_MODEL = "gemini-3.5-flash-lite"

SUPABASE_URL = st.secrets["SUPABASE_URL"].rstrip("/")
SUPABASE_PUBLISHABLE_KEY = st.secrets["SUPABASE_PUBLISHABLE_KEY"]
SUPABASE_SECRET_KEY = st.secrets["SUPABASE_SECRET_KEY"]

# Standard paid-tier token prices (USD per million tokens; reviewed 2026-11-07).
# The dashboard shows a PAID-TIER EQUIVALENT, not a billing statement.
GEMINI_PRICES = {"input": Decimal("0.30"), "output": Decimal("2.50"), "cache": Decimal("0.03")}
PRICE_VERSION = "gemini-3.5-flash-lite-standard-2026-11-07"
CAIRO = ZoneInfo("Africa/Cairo")

# Separate, verified Composio identity for each Supabase user.
# Do not reuse the old shared test identity.

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
# SUPABASE AUTH - EMAIL / PASSWORD
# ==========================================


def auth_request(path, method="POST", body=None,
                 access_token=None, query=None):
    url = SUPABASE_URL + "/auth/v1" + path
    if query:
        url += "?" + urlencode(query)

    headers = {
        "apikey": SUPABASE_PUBLISHABLE_KEY,
        "Content-Type": "application/json",
    }
    if access_token:
        headers["Authorization"] = "Bearer " + access_token

    return http_json(
        url, method=method, body=body, headers=headers
    )


def clear_user_workspace():
    # Clear personal and widget state when the auth identity changes.
    keys = (
        "connect_links", "ga4_properties", "ga4_selected",
        "approved_ga4_properties", "ga4_property_connection",
        "active_ga4_property", "messages", "authenticated_user",
        "verified_user_id", "active_google_analytics",
        "active_hubspot", "load_ga4", "auth_notice",
        "nav_page", "manage_toolkit", "pending_connection_since", "pending_connection_since",
    )
    for key in keys:
        st.session_state.pop(key, None)

    # Remove account-specific controls from earlier sessions.
    for key in list(st.session_state.keys()):
        if key.startswith(("confirm_", "connect_", "disconnect_", "refresh_")):
            st.session_state.pop(key, None)


def clear_auth():
    st.session_state.pop("auth_tokens", None)
    clear_user_workspace()


def save_auth_tokens(data):
    if not isinstance(data, dict):
        raise RuntimeError("Invalid authentication response.")

    access = data.get("access_token")
    refresh = data.get("refresh_token")
    if not access or not refresh:
        raise RuntimeError("No login session was returned.")

    user = auth_request("/user", method="GET", access_token=access)
    user_id = user.get("id")
    if not user_id:
        raise RuntimeError("Could not verify the authenticated user.")

    previous = st.session_state.get("verified_user_id")
    if previous != user_id:
        clear_user_workspace()

    st.session_state["auth_tokens"] = {
        "access_token": access,
        "refresh_token": refresh,
        "expires_at": time.time() + int(data.get("expires_in", 3600)),
    }
    st.session_state["authenticated_user"] = user
    st.session_state["verified_user_id"] = user_id


def refresh_auth():
    tokens = st.session_state.get("auth_tokens") or {}
    refresh = tokens.get("refresh_token")
    if not refresh:
        raise RuntimeError("No refresh token is available.")

    data = auth_request(
        "/token", query={"grant_type": "refresh_token"},
        body={"refresh_token": refresh},
    )
    save_auth_tokens(data)


def get_signed_in_user():
    tokens = st.session_state.get("auth_tokens")
    if not tokens:
        return None

    try:
        # Refresh shortly before expiry and verify on every rerun.
        if time.time() >= tokens.get("expires_at", 0) - 60:
            refresh_auth()

        tokens = st.session_state.get("auth_tokens") or {}
        try:
            user = auth_request(
                "/user", method="GET",
                access_token=tokens["access_token"],
            )
        except RuntimeError as error:
            if "HTTP 401" not in str(error):
                raise
            refresh_auth()
            tokens = st.session_state["auth_tokens"]
            user = auth_request(
                "/user", method="GET",
                access_token=tokens["access_token"],
            )

        user_id = user.get("id")
        if not user_id:
            raise RuntimeError("Invalid account identity.")
        if st.session_state.get("verified_user_id") != user_id:
            clear_user_workspace()

        st.session_state["authenticated_user"] = user
        st.session_state["verified_user_id"] = user_id
        return user
    except (RuntimeError, KeyError, TypeError, ValueError):
        clear_auth()
        return None


def composio_user_id():
    user_id = st.session_state.get("verified_user_id")
    if not user_id:
        raise RuntimeError("Sign in before using integrations.")
    return "supabase_" + user_id


def render_login():
    # Login screen uses the same visual language as the app.
    st.sidebar.markdown(
        '<div class="brand-lockup"><span class="brand-symbol">✦</span>'
        '<span><strong>AI Employee</strong><small>Early access</small></span></div>',
        unsafe_allow_html=True,
    )
    st.sidebar.markdown(
        '<div class="sidebar-note">Your data. Your AI analyst.<br>'
        'A dedicated workspace for each account.</div>',
        unsafe_allow_html=True,
    )

    left, center, right = st.columns([1, 1.25, 1])
    with center:
        st.markdown('<div class="login-spacer"></div>', unsafe_allow_html=True)
        st.markdown(
            '<div class="eyebrow">YOUR AI WORKSPACE</div>'
            '<div class="page-title">Welcome to AI Employee</div>'
            '<div class="page-description">Access your integrations, analytics '
            'and AI assistant in one secure workspace.</div>',
            unsafe_allow_html=True,
        )
        with st.container(border=True):
            tab_signin, tab_signup = st.tabs(["Sign in", "Create account"])

            with tab_signin:
                with st.form("signin_form", clear_on_submit=False):
                    email = st.text_input("Email address", key="signin_email").strip().lower()
                    password = st.text_input(
                        "Password", type="password", key="signin_password"
                    )
                    submit = st.form_submit_button(
                        "Sign in to workspace", type="primary", use_container_width=True
                    )
                if submit:
                    if not email or not password:
                        st.error("Enter your email and password.")
                    else:
                        try:
                            data = auth_request(
                                "/token", query={"grant_type": "password"},
                                body={"email": email, "password": password},
                            )
                            save_auth_tokens(data)
                            st.rerun()
                        except RuntimeError as error:
                            st.error(str(error))

            with tab_signup:
                with st.form("signup_form", clear_on_submit=False):
                    new_email = st.text_input(
                        "Email address", key="signup_email"
                    ).strip().lower()
                    new_password = st.text_input(
                        "Password (minimum 8 characters)",
                        type="password", key="signup_password",
                    )
                    confirm_password = st.text_input(
                        "Confirm password", type="password",
                        key="signup_confirm_password",
                    )
                    signup = st.form_submit_button(
                        "Create account", type="primary", use_container_width=True
                    )
                if signup:
                    if not new_email or "@" not in new_email:
                        st.error("Enter a valid email address.")
                    elif len(new_password) < 8:
                        st.error("Use a password with at least 8 characters.")
                    elif new_password != confirm_password:
                        st.error("Passwords do not match.")
                    else:
                        try:
                            result = auth_request(
                                "/signup",
                                body={
                                    "email": new_email,
                                    "password": new_password,
                                },
                            )
                            if result.get("access_token"):
                                save_auth_tokens(result)
                                st.rerun()
                            else:
                                st.success(
                                    "Check your inbox for a confirmation link, "
                                    "then return to sign in."
                                )
                        except RuntimeError as error:
                            st.error(str(error))
        st.caption(
            "Early access · Email confirmation may be required. "
            "Sign in manually after confirming your email."
        )


# ==========================================
# SUPABASE USAGE LEDGER (SERVER SIDE ONLY)
# ==========================================


def usage_db_request(method="GET", body=None, query=None):
    """Server-side only. Never expose or proxy the secret key to the browser.

    Callers use the Supabase Auth verified identity, not a user-supplied ID.
    Secret keys bypass RLS, so all SELECTs are explicitly user-filtered.
    """
    url = SUPABASE_URL + "/rest/v1/ai_usage"
    if query:
        url += "?" + urlencode(query)
    headers = {
        "apikey": SUPABASE_SECRET_KEY,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    if method != "GET":
        headers["Prefer"] = "return=minimal"
    return http_json(url, method=method, body=body, headers=headers, timeout=25)


def usage_identity():
    user_id = st.session_state.get("verified_user_id")
    if not user_id:
        raise RuntimeError("Sign in to use your AI Employee.")
    try:
        return str(uuid.UUID(user_id))
    except (TypeError, ValueError):
        raise RuntimeError("Account identity could not be verified.") from None


def normalize_gemini_usage(raw):
    """Respect the API's individual input / candidate / thought token counts."""
    if not isinstance(raw, dict) or not any(
        key in raw for key in ("promptTokenCount", "candidatesTokenCount", "thoughtsTokenCount", "toolUsePromptTokenCount")
    ):
        return None
    def count(key):
        try:
            return max(0, int(raw.get(key, 0) or 0))
        except (ValueError, TypeError):
            return 0
    # Tool-use prompt tokens may be reported separately from the normal prompt.
    # They are part of the request's total input-token usage.
    prompt = count("promptTokenCount") + count("toolUsePromptTokenCount")
    response = count("candidatesTokenCount")
    thinking = count("thoughtsTokenCount")
    cached = min(prompt, count("cachedContentTokenCount"))
    total = count("totalTokenCount") or prompt + response + thinking
    return {
        "input_tokens": prompt,
        "output_tokens": response,
        "thinking_tokens": thinking,
        "cached_input_tokens": cached,
        "total_tokens": total,
    }


def estimated_paid_cost(counts):
    # The output tariff includes thinking tokens. Cached prompt tokens
    # have their own rate when reported by the API.
    tokens = Decimal(1_000_000)
    uncached = max(0, counts["input_tokens"] - counts["cached_input_tokens"])
    value = (
        Decimal(uncached) * GEMINI_PRICES["input"]
        + Decimal(counts["cached_input_tokens"]) * GEMINI_PRICES["cache"]
        + Decimal(counts["output_tokens"] + counts["thinking_tokens"]) * GEMINI_PRICES["output"]
    ) / tokens
    return str(value.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP))


def record_model_usage(gemini_response, request_id):
    """Persist one row per Gemini API response, even intermediate tool turns."""
    account_id = usage_identity()
    raw = gemini_response.get("usageMetadata")
    counts = normalize_gemini_usage(raw)
    parts = ((gemini_response.get("candidates") or [{}])[0].get("content") or {}).get("parts") or []
    requested_tools = sum(bool(isinstance(part, dict) and "functionCall" in part) for part in parts)
    row = {
        "user_id": account_id,
        "request_id": request_id,
        "usage_type": "chat",
        "provider": "gemini",
        "model": GEMINI_MODEL,
        "tool_calls": requested_tools,  # Model-requested tools, NOT billed Composio calls.
        "usage_metadata": raw if isinstance(raw, dict) else {},
        "pricing_version": PRICE_VERSION,
        "cost_status": "estimated_paid_tier" if counts is not None else "metadata_missing",
        "estimated_cost_usd": estimated_paid_cost(counts) if counts else "0",
        **(counts or {
            "input_tokens": 0,
            "output_tokens": 0,
            "thinking_tokens": 0,
            "cached_input_tokens": 0,
            "total_tokens": 0,
        }),
    }
    # Fail closed: no additional AI turns can execute if usage cannot be logged.
    try:
        usage_db_request("POST", body=row)
    except RuntimeError:
        raise RuntimeError("Usage recording is unavailable. Your request was stopped to prevent untracked usage.") from None


def fetch_usage_rows(lookback_days=93):
    """Paginate explicit owner-filtered rows; never silently truncate totals."""
    user_id = usage_identity()
    since = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).isoformat()
    records = []
    for offset in range(0, 10000, 500):
        query = {
            "select": "id,request_id,model,input_tokens,output_tokens,thinking_tokens,cached_input_tokens,total_tokens,tool_calls,estimated_cost_usd,cost_status,created_at",
            "user_id": "eq." + user_id,
            "created_at": "gte." + since,
            "order": "created_at.desc",
            "limit": "500",
            "offset": str(offset),
        }
        result = usage_db_request("GET", query=query)
        if not isinstance(result, list):
            raise RuntimeError("Unable to retrieve usage history.")
        records.extend(result)
        if len(result) < 500:
            return records
    raise RuntimeError("Usage history exceeds the current dashboard limit; totals were not displayed.")


def amount(value):
    try:
        return Decimal(str(value or 0))
    except Exception:
        return Decimal(0)


def usage_summary(rows, since):
    filtered = []
    for row in rows:
        try:
            day = datetime.fromisoformat(row["created_at"].replace("Z", "+00:00")).astimezone(CAIRO)
        except (ValueError, KeyError, TypeError, AttributeError):
            continue
        if day >= since:
            filtered.append((row, day))
    def val(row, name):
        try:
            return max(0, int(row.get(name) or 0))
        except (TypeError, ValueError):
            return 0
    requests = set()
    for row, _ in filtered:
        requests.add(row.get("request_id") or row.get("id"))
    return {
        "requests": len(requests),
        "api_calls": len(filtered),
        "input": sum(val(row,"input_tokens") for row,_ in filtered),
        "output": sum(val(row,"output_tokens") for row,_ in filtered),
        "thinking": sum(val(row,"thinking_tokens") for row,_ in filtered),
        "tokens": sum(val(row,"total_tokens") for row,_ in filtered),
        "tools": sum(val(row,"tool_calls") for row,_ in filtered),
        "cost": sum((amount(row.get("estimated_cost_usd")) for row,_ in filtered), Decimal(0)),
        "unpriced": sum(row.get("cost_status") == "metadata_missing" for row,_ in filtered),
        "rows": filtered,
    }


def cairo_periods():
    now = datetime.now(CAIRO)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    last_30 = (now - timedelta(days=29)).replace(hour=0, minute=0, second=0, microsecond=0)
    return now, month_start, last_30


def format_tokens(value):
    return f"{int(value):,}"


def format_cost(value):
    return f"${amount(value):,.4f}"


def usage_donut(summary):
    # Donut shows token composition, NOT credits or remaining balance.
    a,b,c = summary["input"], summary["output"], summary["thinking"]
    total = a+b+c
    if not total:
        background = "conic-gradient(#e8ebf3 0% 100%)"
    else:
        p = a/total*100
        q = b/total*100
        background = (f"conic-gradient(#5852d6 0% {p:.5f}%, "
                      f"#38bdf8 {p:.5f}% {p+q:.5f}%, "
                      f"#f59e0b {p+q:.5f}% 100%)")
    st.markdown(
        '<div class="usage-visual-row">'
        f'<div class="usage-donut" style="background:{background}">'
        f'<div class="usage-donut-hole"><strong>{safe_html(format_tokens(total))}</strong><small>tokens</small></div></div>'
        '<div class="usage-legend">'
        f'<div><span class="legend-dot" style="background:#5852d6"></span>Input <strong>{format_tokens(a)}</strong></div>'
        f'<div><span class="legend-dot" style="background:#38bdf8"></span>Output <strong>{format_tokens(b)}</strong></div>'
        f'<div><span class="legend-dot" style="background:#f59e0b"></span>Thinking <strong>{format_tokens(c)}</strong></div>'
        '</div></div>', unsafe_allow_html=True,
    )
    st.caption("Breakdown of measured Gemini tokens — not a plan-credit balance.")


def usage_daily_chart(summary, days=30):
    now, _, _ = cairo_periods()
    base = now.date() - timedelta(days=days-1)
    daily = {base + timedelta(days=i): 0 for i in range(days)}
    for row, when in summary["rows"]:
        d = when.date()
        if d in daily:
            daily[d] += int(row.get("total_tokens") or 0)
    # Streamlit's native chart uses its bundled plotting stack; no extra pip package.
    st.bar_chart({"Day": [d.strftime("%b %d") for d in daily],
                  "Tokens": list(daily.values())}, x="Day", y="Tokens", height=225)


# ==========================================
# CONNECTED ACCOUNTS
# ==========================================

def get_connections(toolkit):
    connections = []
    cursor = None

    for _ in range(10):
        params = {
            "user_ids": [composio_user_id()],
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
                item.get("user_id") == composio_user_id()
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
            "user_id": composio_user_id(),
            "auth_config_id": auth_config_id,
            "callback_url": APP_URL + "/?oauth_return=1",
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
        account.get("user_id") != composio_user_id()
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
            "user_id": composio_user_id(),
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
                    tool_definitions, request_id):
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

    response = http_json(
        url,
        method="POST",
        body=payload,
        headers={
            "Content-Type": "application/json",
            "x-goog-api-key": st.secrets["GOOGLE_API_KEY"],
        },
        timeout=90,
    )
    record_model_usage(response, request_id)
    return response


def ask_ai(messages, connections, active_property):
    request_id = str(uuid.uuid4())  # Shared by all Gemini calls for one chat request.
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
            request_id,
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
# PRESENTATION / THEME
# ==========================================

# No extra Python packages are needed: styling is plain CSS,
# while every real action remains a native Streamlit widget.
STYLE = r"""
<style>
@import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700;800&display=swap');
:root {--navy:#152238;--indigo:#4f46e5;--body:#202d43;--muted:#708099;--outline:#e6eaf1;--surface:#f7f9fc;}
html, body, [data-testid="stApp"], [data-testid="stAppViewContainer"] {
  background:#f7f9fc; font-family:'DM Sans', 'Segoe UI', sans-serif;
}
[data-testid="stHeader"] {background:rgba(247,249,252,.94);border-bottom:1px solid #edf0f5;}
[data-testid="stToolbar"] {color:#6d7b90;}
.block-container {max-width:1480px;padding-top:2.3rem;padding-bottom:3.5rem;padding-left:2.7rem;padding-right:2.7rem;}
h1,h2,h3,h4,p,div,label,button,input {font-family:'DM Sans', 'Segoe UI', sans-serif;}
h1,h2,h3 {letter-spacing:-.025em;color:#1c2940;}
p {color:#53637a;}
[data-testid="stSidebar"] {background:linear-gradient(180deg,#142138 0%,#1a2943 100%);border-right:1px solid #253654;}
[data-testid="stSidebar"] [data-testid="stSidebarContent"] {padding:1.1rem .8rem;}
[data-testid="stSidebar"] * {color:#d9e3f3;}
[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] p {color:#d9e3f3;}
[data-testid="stSidebar"] [data-testid="stWidgetLabel"] {color:#9aacc6;}
[data-testid="stSidebar"] [role="radiogroup"] {gap:.28rem;}
[data-testid="stSidebar"] [role="radiogroup"] label {
  padding:10px 12px;min-height:41px;border-radius:10px;
  transition:background .15s ease;border:1px solid transparent;
}
[data-testid="stSidebar"] [role="radiogroup"] label:hover {background:rgba(255,255,255,.07);}
[data-testid="stSidebar"] [role="radiogroup"] label:has(input:checked) {background:#344667;border-color:#465d82;}
[data-testid="stSidebar"] [role="radiogroup"] label div[role="radiogroup"] {display:none;}
[data-testid="stSidebar"] button {border-radius:9px;border:1px solid #455675;background:#263955;color:#f3f7ff;}
[data-testid="stSidebar"] button:hover {border-color:#8395ff;background:#334a6e;color:white;}
[data-testid="stSidebar"] hr {border-color:#34445c;}
.brand-lockup {display:flex;gap:11px;align-items:center;margin:16px 0 30px 3px;}
.brand-lockup strong {display:block;color:#fff;font-size:17px;letter-spacing:-.5px;line-height:1.3;}
.brand-lockup small {display:block;color:#aab8ce;font-size:11px;margin-top:2px;letter-spacing:.1em;text-transform:uppercase;}
.brand-symbol {width:38px;height:38px;display:flex;align-items:center;justify-content:center;background:linear-gradient(135deg,#596bff,#8075ff);color:#fff;font-size:25px;font-weight:700;border-radius:12px;box-shadow:0 7px 22px #0002;}
.sidebar-label {font-size:10px;font-weight:800;letter-spacing:.15em;color:#8399b7!important;margin:10px 0 12px 7px;}
.sidebar-note {border:1px solid #35455f;background:#22334f;color:#b7c7df;font-size:12px;line-height:1.75;border-radius:12px;padding:13px 14px;margin:1rem 0;}
.sidebar-profile {border-top:1px solid #33445f;padding:15px 2px 5px;font-size:12px;line-height:1.65;color:#cdd8ea;}
.sidebar-profile .avatar {display:inline-grid;place-items:center;width:33px;height:33px;margin-right:7px;border-radius:10px;background:#5265e8;color:white;font-weight:800;}
.sidebar-profile strong {color:white;}
.sidebar-profile .email {display:block;color:#95a9c6;overflow-wrap:anywhere;margin:5px 0;}
.login-spacer {height:9vh;}
.eyebrow {color:#605ff4;font-size:11px;font-weight:800;letter-spacing:.15em;margin:0 0 8px;}
.page-title {font-size:32px;line-height:1.18;font-weight:800;color:#1a2840;letter-spacing:-.05em;margin:3px 0 7px;}
.page-description {font-size:14px;color:#738198;line-height:1.7;margin:0 0 22px;max-width:850px;}
.page-top {display:flex;align-items:center;justify-content:space-between;gap:16px;flex-wrap:wrap;margin:4px 0 23px;}
.header-pill {font-size:11px;font-weight:750;background:#e8edff;color:#4f46e5;border:1px solid #dce4ff;border-radius:30px;padding:7px 13px;}
.hero-card {border-radius:18px;background:linear-gradient(112deg,#172844 0%,#2e3c77 75%,#5455bf 100%);color:#fff;padding:30px 34px;margin:0 0 22px;box-shadow:0 16px 40px rgba(35,50,110,.1);}
.hero-card h2{margin:5px 0 11px;color:#fff;font-weight:800;letter-spacing:-.035em;font-size:25px;}
.hero-card p{margin:0;color:#ced9f1;max-width:740px;font-size:13px;line-height:1.85;}
.hero-card .hero-kicker{color:#aabdfd;font-size:11px;letter-spacing:.13em;font-weight:800;text-transform:uppercase;}
.metric-card {background:white;border:1px solid var(--outline);border-radius:15px;padding:21px 21px 19px;min-height:135px;box-shadow:0 2px 13px rgba(35,51,86,.025);}
.metric-label {font-size:12px;color:#77849b;font-weight:600;letter-spacing:.01em;}
.metric-value {color:#1b2940;font-size:31px;font-weight:800;letter-spacing:-.055em;line-height:1.25;margin:12px 0 5px;}
.metric-foot {font-size:11px;color:#8c98a9;}
.section-title {color:#26334a;font-size:16px;font-weight:800;letter-spacing:-.02em;margin:25px 0 9px;}
.section-muted {color:#8795aa;font-size:12px;margin-bottom:17px;}
.status-pill {display:inline-flex;align-items:center;gap:6px;font-size:11px;font-weight:750;line-height:1.2;border-radius:25px;padding:6px 10px;}
.status-green{background:#eaf9ef;color:#208553;}.status-gray{background:#f0f3f7;color:#708099;}.status-violet{background:#f0edff;color:#6553c3;}
.status-dot{width:6px;height:6px;background:currentColor;border-radius:50%;display:inline-block;}
.integration-head {display:flex;align-items:center;gap:12px;margin:0 0 14px;min-height:52px;}
.integration-logo {display:flex;align-items:center;justify-content:center;width:51px;height:51px;background:#f6f8fc;border:1px solid #eaf0f8;border-radius:13px;flex-shrink:0;}
.integration-logo img{width:30px;height:30px;object-fit:contain;}
.integration-name{font-weight:800;font-size:15px;color:#213048;letter-spacing:-.02em;}
.integration-desc{font-size:11px;color:#8390a4;margin-top:3px;line-height:1.4;}
.integration-foot{font-size:11px;color:#8a98aa;margin-top:11px;min-height:20px;}
.soon-card{border-radius:14px;border:1px solid #e7eaf2;background:white;min-height:140px;padding:20px;margin-bottom:13px;box-shadow:0 2px 8px #1d2d4504;}
.soon-top {display:flex;align-items:center;gap:12px;margin-bottom:16px;}
.soon-logo {width:43px;height:43px;background:#f8f9fd;border:1px solid #edf0f5;border-radius:12px;display:flex;align-items:center;justify-content:center;}
.soon-logo img{height:24px;width:24px;object-fit:contain;opacity:.82;}
.soon-name{color:#29374d;font-size:13px;font-weight:750;}
.placeholder-box{background:#fff;border:1px dashed #ccd5e6;border-radius:16px;padding:25px 29px;color:#66758d;margin:6px 0 15px;line-height:1.9;}
.placeholder-box strong{color:#2b3952;display:block;margin-bottom:4px;}
.placeholder-icon{display:grid;place-items:center;width:47px;height:47px;background:#eff2ff;color:#5858d6;border-radius:14px;font-size:24px;margin-bottom:14px;}
.info-row{background:white;border:1px solid #e5eaf2;border-radius:14px;padding:17px 20px;margin:12px 0;}
.info-row .info-label{font-size:11px;color:#8b98a9;}
.info-row .info-val{font-size:14px;font-weight:700;color:#273751;margin-top:6px;overflow-wrap:anywhere;}
.small-help{font-size:12px;line-height:1.8;color:#7d8a9d;}
[data-testid="stVerticalBlockBorderWrapper"] {border-color:#e2e8f2!important;border-radius:15px!important;background:white;}
[data-testid="stButton"] button[kind="primary"], [data-testid="stFormSubmitButton"] button[kind="primary"] {background:#4d55df;color:white;border:1px solid #4d55df;box-shadow:none;}
[data-testid="stButton"] button[kind="primary"]:hover, [data-testid="stFormSubmitButton"] button[kind="primary"]:hover {background:#434cc7;border-color:#434cc7;color:#fff;}
[data-testid="stButton"] button, [data-testid="stLinkButton"] a, [data-testid="stFormSubmitButton"] button {border-radius:9px;font-weight:650;font-size:12px;min-height:40px;}
[data-testid="stButton"] button:hover {border-color:#7370ed;color:#4e52b7;}
[data-testid="stAlert"]{border-radius:12px;}
[data-testid="stChatMessage"]{border:1px solid #e8ecf3;border-radius:13px;background:white;margin-bottom:9px;padding:8px 10px;}
[data-testid="stChatInput"]{border-radius:12px;}
[data-testid="stMetric"]{background:white;border:1px solid #e7ebf2;border-radius:13px;padding:16px;}
/* CTA styling: the text elements in Streamlit buttons require explicit color. */
[data-testid="stButton"] button[kind="primary"],
[data-testid="stFormSubmitButton"] button[kind="primary"],
[data-testid="stButton"] button[data-testid="baseButton-primary"],
[data-testid="stFormSubmitButton"] button[data-testid="baseButton-primary"] {
  color:#fff!important;-webkit-text-fill-color:#fff!important;
  background:#4f46e5!important;border-color:#4f46e5!important;
}
[data-testid="stButton"] button[kind="primary"] *,
[data-testid="stFormSubmitButton"] button[kind="primary"] *,
[data-testid="stButton"] button[data-testid="baseButton-primary"] *,
[data-testid="stFormSubmitButton"] button[data-testid="baseButton-primary"] * {
  color:#fff!important;-webkit-text-fill-color:#fff!important;
}
[data-testid="stButton"] button[kind="primary"]:hover,
[data-testid="stFormSubmitButton"] button[kind="primary"]:hover,
[data-testid="stButton"] button[data-testid="baseButton-primary"]:hover,
[data-testid="stFormSubmitButton"] button[data-testid="baseButton-primary"]:hover {
  background:#4338ca!important;border-color:#4338ca!important;
}
/* Native Streamlit quick actions are real buttons, not HTML mock controls. */
.quick-actions-title{font-size:12px;color:#7c8ba2;font-weight:750;letter-spacing:.02em;margin:12px 0 9px;}
.onboarding-shell{border:1px solid #e3e9f6;background:#fff;border-radius:17px;padding:22px 23px;margin:14px 0 8px;}
.onboarding-heading{font-size:16px;color:#1e2d46;font-weight:800;margin-bottom:7px;}
.onboarding-intro{font-size:12px;color:#7f8da4;line-height:1.7;}
.journey-step{border:1px solid #e6ebf3;background:#fff;border-radius:13px;padding:15px 16px;min-height:117px;margin-bottom:7px;}
.journey-step.done{background:#f4faf7;border-color:#d8f1e2;}
.journey-step.current{background:#f4f5ff;border-color:#dcdcff;}
.journey-number{font-size:11px;font-weight:800;color:#6a62dc;margin-bottom:9px;}
.journey-title{font-size:13px;font-weight:750;color:#273650;margin-bottom:7px;}
.journey-desc{font-size:11px;line-height:1.6;color:#7a899e;}
.dashboard-panel{border:1px solid #e6eaf2;background:#fff;border-radius:15px;padding:20px;margin-bottom:14px;min-height:195px;}
.dashboard-panel-head{font-weight:800;color:#25334b;font-size:14px;margin:0 0 9px;}
.dashboard-panel-muted{font-size:12px;color:#8593a8;line-height:1.8;}
.activity-item{display:flex;align-items:center;gap:10px;border-top:1px solid #eef1f6;padding:12px 0;}
.activity-mark{background:#eeeefe;color:#615cde;border-radius:10px;width:29px;height:29px;display:grid;place-items:center;font-size:13px;flex-shrink:0;}
.activity-copy{font-size:12px;font-weight:650;color:#334359;line-height:1.6;}
.activity-sub{font-size:11px;color:#929eb0;line-height:1.6;}
.svg-logo{width:27px;height:27px;display:block;}
.svg-logo svg{width:100%;height:100%;display:block;}
.oauth-launch-link{display:block;text-align:center;padding:12px 14px;border-radius:9px;background:#4f46e5;color:white!important;text-decoration:none!important;font-weight:750;font-family:'DM Sans',sans-serif;font-size:13px;min-height:42px;}
.oauth-launch-link:hover{background:#4338ca;color:#fff!important;}

/* Customer-facing usage visuals */
.usage-visual-row{display:flex;align-items:center;gap:30px;flex-wrap:wrap;padding:15px 0;}
.usage-donut{height:166px;width:166px;display:flex;align-items:center;justify-content:center;border-radius:50%;flex-shrink:0;}
.usage-donut-hole{background:#fff;height:120px;width:120px;border-radius:50%;display:flex;flex-direction:column;justify-content:center;align-items:center;}
.usage-donut-hole strong{color:#283754;font-size:22px;font-weight:800;line-height:1.3;}
.usage-donut-hole small{color:#8b97aa;font-size:11px;}
.usage-legend{display:grid;gap:14px;min-width:195px;}
.usage-legend div{display:flex;align-items:center;gap:9px;color:#7a879e;font-size:12px;}
.usage-legend strong{margin-left:auto;color:#22334e;font-size:12px;}
.legend-dot{display:inline-block;width:10px;height:10px;border-radius:3px;flex-shrink:0;}
@media (max-width:900px){.block-container{padding-left:1rem;padding-right:1rem;padding-top:1.3rem;}.page-title{font-size:26px;}.hero-card{padding:22px;}}
</style>
"""


# Catalog placeholders are VISUAL ONLY. They never create links or execute tools.
# Add a real integration to APPS and ALLOWED_TOOLS only after configuring,
# testing and approving it in Composio. Do not treat these as active connectors.
UPCOMING_APPS = [
    ("Google Ads", "googleads"),
    ("Meta Ads", "meta"),
    ("Microsoft Ads", "microsoft"),
    ("LinkedIn Ads", "linkedin"),
    ("Google Search Console", "googlesearchconsole"),
    ("Shopify", "shopify"),
    ("Klaviyo", "klaviyo"),
    ("Salesforce", "salesforce"),
    ("Slack", "slack"),
    ("Gmail", "gmail"),
    ("Google Sheets", "googlesheets"),
    ("TikTok", "tiktok"),
    ("Mailchimp", "mailchimp"),
    ("Stripe", "stripe"),
    ("BigQuery", "googlebigquery"),
]

LOGOS = {
    "Google Analytics": "googleanalytics",
    "HubSpot": "hubspot",
}


# Locally rendered vector marks avoid the broken CDN URLs for these brands.
# They are recognizable brand-style icons, not official brand artwork assets.
INLINE_LOGOS = {
    "microsoft": (
        '<svg viewBox="0 0 32 32" xmlns="http://www.w3.org/2000/svg">'
        '<rect x="1" y="1" width="14" height="14" fill="#f25022"/>'
        '<rect x="17" y="1" width="14" height="14" fill="#7fba00"/>'
        '<rect x="1" y="17" width="14" height="14" fill="#00a4ef"/>'
        '<rect x="17" y="17" width="14" height="14" fill="#ffb900"/>'
        '</svg>'
    ),
    "linkedin": (
        '<svg viewBox="0 0 32 32" xmlns="http://www.w3.org/2000/svg">'
        '<rect width="32" height="32" rx="5" fill="#0A66C2"/>'
        '<circle cx="7.9" cy="9" r="2.2" fill="white"/>'
        '<rect x="5.8" y="13" width="4.3" height="13" fill="white"/>'
        '<path d="M13 13h4.1v1.8c.9-1.4 2.2-2.1 4.1-2.1 4 0 5.5 2.6 5.5 6.4V26h-4.4v-6.2c0-1.7-.4-2.9-2.2-2.9-1.7 0-2.6 1.2-2.6 2.9V26H13z" fill="white"/>'
        '</svg>'
    ),
    "klaviyo": (
        '<svg viewBox="0 0 32 32" xmlns="http://www.w3.org/2000/svg">'
        '<rect width="32" height="32" rx="6" fill="#222222"/>'
        '<path d="M8 6h4v8l8-8h5l-9.5 9.3L26 26h-5.5L12 17.5V26H8z" fill="#ffffff"/>'
        '</svg>'
    ),
    "slack": (
        '<svg viewBox="0 0 32 32" xmlns="http://www.w3.org/2000/svg">'
        '<rect x="4" y="12" width="5" height="11" rx="2.5" fill="#36C5F0"/>'
        '<rect x="4" y="4" width="5" height="5" rx="2.5" fill="#36C5F0"/>'
        '<rect x="11" y="4" width="5" height="12" rx="2.5" fill="#2EB67D"/>'
        '<rect x="19" y="4" width="5" height="12" rx="2.5" fill="#ECB22E" transform="rotate(90 21.5 10)"/>'
        '<rect x="17" y="12" width="11" height="5" rx="2.5" fill="#E01E5A"/>'
        '<rect x="23" y="18" width="5" height="10" rx="2.5" fill="#E01E5A"/>'
        '<rect x="16" y="18" width="5" height="10" rx="2.5" fill="#2EB67D"/>'
        '<rect x="4" y="23" width="13" height="5" rx="2.5" fill="#ECB22E"/>'
        '</svg>'
    ),
}


def logo_url(slug):
    return f"https://cdn.simpleicons.org/{quote(slug, safe='')}"


def logo_markup(slug, name):
    if slug in INLINE_LOGOS:
        return '<span class="svg-logo" role="img" aria-label="' + safe_html(name) + '">' + INLINE_LOGOS[slug] + '</span>'
    # Remaining connectors keep their working CDN source.
    return f'<img src="{safe_html(logo_url(slug))}" alt="{safe_html(name)} logo" loading="lazy">'


def safe_html(value):
    return html.escape(str(value), quote=True)


def inject_theme():
    st.markdown(STYLE, unsafe_allow_html=True)


def section(title, subtitle=None):
    st.markdown(f'<div class="section-title">{safe_html(title)}</div>', unsafe_allow_html=True)
    if subtitle:
        st.markdown(
            f'<div class="section-muted">{safe_html(subtitle)}</div>',
            unsafe_allow_html=True,
        )


def page_header(title, description, tag="YOUR WORKSPACE"):
    st.markdown(
        '<div class="page-top"><div>'
        '<div class="eyebrow">AI EMPLOYEE WORKSPACE</div>'
        f'<div class="page-title">{safe_html(title)}</div>'
        f'<div class="page-description">{safe_html(description)}</div>'
        '</div>'
        f'<span class="header-pill">{safe_html(tag)}</span></div>',
        unsafe_allow_html=True,
    )


def metric_card(label, value, foot):
    st.markdown(
        '<div class="metric-card">'
        f'<div class="metric-label">{safe_html(label)}</div>'
        f'<div class="metric-value">{safe_html(value)}</div>'
        f'<div class="metric-foot">{safe_html(foot)}</div>'
        '</div>', unsafe_allow_html=True,
    )


def empty_feature(icon, title, detail):
    st.markdown(
        '<div class="placeholder-box">'
        f'<div class="placeholder-icon">{safe_html(icon)}</div>'
        f'<strong>{safe_html(title)}</strong>'
        f'{safe_html(detail)}'
        '</div>', unsafe_allow_html=True,
    )


def status_badge(connected, small=False):
    label = "Connected" if connected else "Not connected"
    style = "status-green" if connected else "status-gray"
    return (
        f'<span class="status-pill {style}">'
        f'<span class="status-dot"></span>{label}</span>'
    )


def integration_intro(name, logo, description, connected):
    st.markdown(
        '<div class="integration-head">'
        '<div class="integration-logo">'
        + logo_markup(logo, name) +
        '</div><div>'
        f'<div class="integration-name">{safe_html(name)}</div>'
        f'<div class="integration-desc">{safe_html(description)}</div>'
        '</div></div>'
        f'{status_badge(connected)}', unsafe_allow_html=True,
    )


def load_connection_state():
    active = {}
    accounts_by_toolkit = {}
    errors = {}
    for config in APPS.values():
        toolkit = config["toolkit"]
        try:
            accounts = get_connections(toolkit)
        except (RuntimeError, KeyError, TypeError) as error:
            errors[toolkit] = str(error)
            accounts = []
        accounts_by_toolkit[toolkit] = accounts
        if accounts:
            selected_id = st.session_state.get("active_" + toolkit)
            selected = next((a for a in accounts if a["id"] == selected_id), None)
            active[toolkit] = selected or accounts[0]
    # Clear GA4 options only if the connected account truly changed or was removed;
    # do NOT clear settings when Composio temporarily errors.
    ga4 = active.get("google_analytics")
    if "google_analytics" not in errors:
        previous = st.session_state.get("ga4_property_connection")
        if ga4 and previous and previous != ga4["id"]:
            clear_ga4_state()
        if not ga4:
            clear_ga4_state()
    return active, accounts_by_toolkit, errors


def render_sidebar(user, current_connections):
    with st.sidebar:
        st.markdown(
            '<div class="brand-lockup"><span class="brand-symbol">✦</span>'
            '<span><strong>AI Employee</strong><small>Analytics workspace</small></span></div>',
            unsafe_allow_html=True,
        )
        st.markdown('<div class="sidebar-label">WORKSPACE</div>', unsafe_allow_html=True)
        page = st.radio(
            "Navigation",
            ["Overview", "AI Chat", "My Tasks", "Integrations", "Usage", "Settings"],
            format_func=lambda x: {
                "Overview": "▦   Overview",
                "AI Chat": "✧   AI Chat",
                "My Tasks": "☑   My Tasks",
                "Integrations": "⛓   Integrations",
                "Usage": "◫   Usage",
                "Settings": "⚙   Settings",
            }[x],
            label_visibility="collapsed",
            key="nav_page",
        )
        st.divider()
        st.markdown(
            '<div class="sidebar-note">'
            '<strong style="color:white">Early access</strong><br>'
            'Your workspace for connected analytics, reports and upcoming automations.</div>',
            unsafe_allow_html=True,
        )
        st.markdown(
            '<div class="sidebar-profile">'
            '<span class="avatar">' + safe_html((user.get("email") or "U")[0].upper()) + '</span>'
            '<strong>My Workspace</strong>'
            '<span class="email">' + safe_html(user.get("email") or "Signed in") + '</span>'
            '</div>',
            unsafe_allow_html=True,
        )
        if st.button("↪  Sign out", use_container_width=True, key="signout_nav"):
            tokens = st.session_state.get("auth_tokens") or {}
            try:
                if tokens.get("access_token"):
                    auth_request(
                        "/logout", method="POST", body={},
                        access_token=tokens["access_token"],
                    )
            except RuntimeError:
                pass
            clear_auth()
            st.rerun()
    return page


def render_overview(user, active_connections, connection_errors, rows):
    """Executive overview: real usage metrics without duplicate navigation."""
    now, month_start, last_30 = cairo_periods()
    usage = usage_summary(rows, month_start)
    usage_30 = usage_summary(rows, last_30)
    first = (user.get("email") or "there").split("@")[0].replace("."," ").title()
    page_header("Overview", "A clear view of your AI Employee's activity.", tag="WORKSPACE")
    st.markdown(
        '<div class="hero-card"><div class="hero-kicker">YOUR AI EMPLOYEE · WORKSPACE</div>'
        f'<h2>Welcome back, {safe_html(first)}.</h2>'
        '<p>Stay on top of your connected sources and AI activity. '
        'Your latest monthly figures are updated from your workspace.</p></div>',
        unsafe_allow_html=True,
    )
    k1,k2,k3,k4 = st.columns(4, gap="medium")
    with k1: metric_card("Connected sources", len(active_connections), "Available to your workspace")
    with k2: metric_card("AI requests", format_tokens(usage["requests"]), "This month")
    with k3: metric_card("AI tokens", format_tokens(usage["tokens"]), "This month")
    with k4: metric_card("Est. AI cost", format_cost(usage["cost"]), "Paid-tier equivalent · this month")
    section("AI activity", "A quick look at how your assistant is being used")
    l,r = st.columns([1,1.18],gap="large")
    with l:
        with st.container(border=True):
            st.markdown("**Monthly token breakdown**")
            usage_donut(usage)
    with r:
        with st.container(border=True):
            st.markdown("**Last 30 days**")
            usage_daily_chart(usage_30)
    section("What's next", "Upcoming work and recent activity")
    l,r = st.columns(2, gap="large")
    with l:
        with st.container(border=True):
            st.markdown("**Upcoming tasks**")
            st.caption("No scheduled tasks yet. Task scheduling is being prepared.")
            st.info("Scheduled reports and next delivery times will appear here.")
    with r:
        with st.container(border=True):
            st.markdown("**Recent AI activity**")
            if usage_30["rows"]:
                # Show times and counts only: no prompt / business data exposed.
                seen = set()
                for row, timestamp in usage_30["rows"]:
                    key = row.get("request_id") or row.get("id")
                    if key in seen:
                        continue
                    seen.add(key)
                    st.markdown(f"✦  AI request · {timestamp.strftime('%b %d, %I:%M %p')}")
                    if len(seen) >= 4:
                        break
            else:
                st.caption("Your first AI request will appear here.")
    if connection_errors:
        st.info("Some connections couldn't be checked just now.")
    st.caption("Costs are indicative paid-tier Gemini token estimates, not actual charges. "
               "Credits and full integration costs will be added when billing limits are ready.")


def navigate_to(page):
    """Only real native Streamlit buttons may change this navigation state."""
    if page in ("Overview", "AI Chat", "My Tasks", "Integrations", "Usage", "Settings"):
        st.session_state["nav_page"] = page


def render_oauth_popup_link(url, toolkit):
    """Open the provider in a small popup without destroying the main session.

    In browsers that block popups, the fallback is a normal new tab. Do NOT
    put Supabase JWT or refresh tokens in a URL to survive a same-tab redirect.
    """
    element_id = "oauth_start_" + toolkit
    markup = f"""
    <a class="oauth-launch-link" id="{safe_html(element_id)}"
       href="{safe_html(url)}" target="ai_employee_authorization"
       style="display:block;background:#4f46e5;color:#fff;text-align:center;"
    >Continue to authorization ↗</a>
    <script>
      (function() {{
        var link = document.getElementById({json.dumps(element_id)});
        if (!link) return;
        link.addEventListener('click', function(event) {{
          var popup = window.open(link.href, 'ai_employee_authorization',
            'popup=yes,width=600,height=750,left=180,top=70,scrollbars=yes,resizable=yes');
          if (popup) {{ event.preventDefault(); popup.focus(); }}
        }});
      }})();
    </script>
    """
    try:
        # In recent Streamlit releases, scripts can run only if explicitly opted in.
        st.html(markup, unsafe_allow_javascript=True)
    except (TypeError, AttributeError):
        # Older Streamlit deployments don't support JS in st.html.
        st.link_button("Continue to authorization ↗", url, use_container_width=True)


@st.fragment(run_every="5s")
def monitor_oauth_connection(toolkit):
    """Refresh active connection state while OAuth popup is in progress."""
    if toolkit not in st.session_state.get("connect_links", {}):
        return
    started = st.session_state.get("pending_connection_since", {}).get(toolkit, 0)
    if started and time.time() - started > 180:
        st.caption("If authorization is complete, use Refresh below. A new link may be needed if it expired.")
        return
    try:
        accounts = get_connections(toolkit)
        if accounts:
            st.session_state["connect_links"].pop(toolkit, None)
            st.session_state.get("pending_connection_since", {}).pop(toolkit, None)
            st.session_state["manage_toolkit"] = toolkit
            st.rerun()
        st.caption("Waiting for authorization… This card will update automatically.")
    except RuntimeError:
        st.caption("Can't check the connection right now. Try Refresh status.")


def render_oauth_return():
    """Complete the popup UI before auth gate; no tokens are stored here.

    The *original* app session is still live in its browser tab; only it
    checks Composio ownership. Query params alone never prove a connection.
    """
    status = str(st.query_params.get("status", ""))
    if status == "failed":
        st.error("Authorization wasn't completed. Close this window and try again.")
        st.stop()
    st.markdown("## Connection step finished")
    st.caption("Returning to your AI Employee workspace. This window should close automatically.")
    try:
        st.html("""
        <script>
        if (window.opener && window.opener !== window) {
          window.opener.postMessage({type:'ai_employee_authorization_returned'}, window.location.origin);
          window.close();
        }
        </script>
        """, unsafe_allow_javascript=True)
    except (TypeError, AttributeError):
        pass
    st.info("If this window stays open, close it and return to the original AI Employee tab.")
    st.stop()


def render_live_integration(name, config, accounts, error, active_connections):
    toolkit = config["toolkit"]
    with st.container(border=True):
        desc = {
            "google_analytics": "Web analytics and user journeys",
            "hubspot": "CRM contacts, companies and deals",
        }.get(toolkit, "Business data connector")
        integration_intro(name, LOGOS.get(name, toolkit), desc, bool(accounts))
        st.markdown('<div style="height:5px"></div>', unsafe_allow_html=True)
        if error:
            st.warning("Unable to check connection.")
        elif accounts:
            # Popover is anchored to the card; no long settings form below the grid.
            with st.popover("Manage connection", use_container_width=True):
                render_connection_manager(toolkit, accounts, active_connections)
        else:
            if st.button("Connect", key=f"connect_{toolkit}",
                         type="primary", use_container_width=True):
                try:
                    link = create_connect_link(st.secrets[config["secret"]])
                    st.session_state.setdefault("connect_links", {})[toolkit] = link
                    st.session_state.setdefault("pending_connection_since", {})[toolkit] = time.time()
                except (RuntimeError, KeyError) as err:
                    st.error(str(err))
            link = st.session_state.get("connect_links", {}).get(toolkit)
            if link:
                render_oauth_popup_link(link, toolkit)
                monitor_oauth_connection(toolkit)
                if st.button("Refresh connection", key=f"refresh_{toolkit}", use_container_width=True):
                    st.rerun()


def render_connection_manager(toolkit, accounts, active_connections):
    """Contents of a native Streamlit popover inside the connected integration card."""
    if not accounts:
        st.caption("This source is not connected.")
        return
    if len(accounts) > 1:
        ids = [a["id"] for a in accounts]
        chosen = st.selectbox("Connected account", ids,
                              format_func=lambda x: "Account " + str(ids.index(x)+1),
                              key="active_" + toolkit)
        account = next(a for a in accounts if a["id"] == chosen)
    else:
        account = accounts[0]
        st.caption("One connected account")
    active_connections[toolkit] = account
    if toolkit == "google_analytics":
        previous = st.session_state.get("ga4_property_connection")
        if previous and previous != account["id"]:
            clear_ga4_state()
        if st.button("Load properties", key="load_ga4", use_container_width=True):
            try:
                with st.spinner("Loading properties..."):
                    props = load_ga4_properties(account["id"])
                clear_ga4_state()
                st.session_state["ga4_properties"] = props
                st.session_state["ga4_property_connection"] = account["id"]
            except (RuntimeError, KeyError) as err:
                st.error(str(err))
        properties = st.session_state.get("ga4_properties", [])
        if properties and st.session_state.get("ga4_property_connection") == account["id"]:
            options = {p["id"]: f'{p["account"]} / {p["name"]}' for p in properties}
            st.multiselect("GA4 properties", list(options),
                           format_func=lambda x: options[x], key="ga4_selected")
            if st.button("Save selection", key="save_ga4", type="primary", use_container_width=True):
                selected = st.session_state.get("ga4_selected", [])
                if selected:
                    st.session_state["approved_ga4_properties"] = list(selected)
                    st.success(f"{len(selected)} properties saved")
                else:
                    st.warning("Select at least one property")
            approved = st.session_state.get("approved_ga4_properties", [])
            if approved:
                st.caption(f"{len(approved)} properties available in AI Chat")
    with st.expander("Disconnect"):
        confirmed = st.checkbox("I want to disconnect", key=f"confirm_{account['id']}")
        if st.button("Disconnect account", key=f"disconnect_{account['id']}",
                     use_container_width=True, disabled=not confirmed):
            try:
                disconnect_account(account["id"], toolkit)
                if toolkit == "google_analytics":
                    clear_ga4_state()
                st.session_state.get("connect_links", {}).pop(toolkit, None)
                st.rerun()
            except RuntimeError as err:
                st.error(str(err))


def render_integrations(accounts_by_toolkit, active_connections, errors):
    page_header(
        "Integrations",
        "Connect the platforms your AI Employee can securely read and analyze.",
        tag="DATA SOURCES",
    )
    count = sum(bool(a) for a in accounts_by_toolkit.values())
    a, b, c = st.columns(3, gap="medium")
    with a:
        metric_card("Connected", count, "Connected data source types")
    with b:
        metric_card("Available now", len(APPS), "Ready to connect")
    with c:
        metric_card("On the roadmap", len(UPCOMING_APPS), "Not enabled yet")

    section("Available integrations", "Live connections supported by the current prototype")
    columns = st.columns(3, gap="medium")
    for i, (name, config) in enumerate(APPS.items()):
        with columns[i % 3]:
            render_live_integration(
                name, config,
                accounts_by_toolkit.get(config["toolkit"], []),
                errors.get(config["toolkit"]), active_connections,
            )
    with columns[len(APPS) % 3]:
        st.markdown(
            '<div class="soon-card" style="min-height:230px;border-style:dashed;'
            'background:#f3f5fc;display:flex;flex-direction:column;justify-content:center">'
            '<div style="font-size:26px;color:#6765e6;margin-bottom:9px">＋</div>'
            '<div class="integration-name">More integrations</div>'
            '<div class="integration-desc" style="margin-top:10px">'
            'New connectors will appear here after testing and approval.</div>'
            '</div>', unsafe_allow_html=True,
        )

    section("Coming soon", "Reserved slots for future platforms — not yet connected")
    cols = st.columns(3, gap="medium")
    for i, (name, slug) in enumerate(UPCOMING_APPS):
        with cols[i % 3]:
            st.markdown(
                '<div class="soon-card"><div class="soon-top">'
                '<div class="soon-logo">'
                + logo_markup(slug, name) +
                '</div>'
                f'<div class="soon-name">{safe_html(name)}</div>'
                '</div><span class="status-pill status-violet">Planned</span></div>',
                unsafe_allow_html=True,
            )


def render_ai_chat(active_connections):
    page_header(
        "AI Chat", "Ask questions and get answers from your connected business data.",
        tag="LIVE AI ASSISTANT",
    )
    approved = st.session_state.get("approved_ga4_properties", [])
    active_property = None
    if "google_analytics" in active_connections and approved:
        active_property = st.selectbox(
            "GA4 property for this conversation", approved,
            key="active_ga4_property",
        )
    left, right = st.columns([2.35, 1], gap="large")
    with left:
        with st.container(border=True):
            st.markdown("**✧ Your AI Analyst**")
            st.caption("Real answers from the data sources you choose")
            if not active_connections:
                st.info("No data sources connected. Go to Integrations to connect GA4 or HubSpot.")
            if "messages" not in st.session_state:
                st.session_state.messages = []
            if st.button("Clear conversation", key="clear_conversation"):
                st.session_state.messages = []
                st.rerun()
            st.divider()
            for msg in st.session_state.messages:
                with st.chat_message(msg["role"]):
                    st.markdown(msg["content"])
            if prompt := st.chat_input("Ask your AI Employee..."):
                conversation = st.session_state.messages + [{"role": "user", "content": prompt}]
                with st.chat_message("user"):
                    st.markdown(prompt)
                with st.chat_message("assistant"):
                    with st.spinner("Analyzing your request..."):
                        try:
                            answer, tools_used = ask_ai(
                                conversation, active_connections, active_property,
                            )
                            st.markdown(answer)
                            # Tool diagnostics are intentionally omitted from the customer UI.
                            # Read-only tools are still enforced server-side.
                            st.session_state.messages.extend([
                                {"role": "user", "content": prompt},
                                {"role": "assistant", "content": answer},
                            ])
                        except RuntimeError as err:
                            st.error(str(err))
    with right:
        section("Context", "Data sources this session")
        with st.container(border=True):
            if not active_connections:
                st.caption("No sources connected yet")
            else:
                for name, config in APPS.items():
                    if config["toolkit"] in active_connections:
                        st.markdown(f"**✓ {name}**")
            if active_property:
                st.caption("GA4 property: " + str(active_property))
            elif "google_analytics" in active_connections:
                st.warning("Select a GA4 property in Integrations first")
            st.divider()
            st.markdown("**Your data stays in your control**")
            st.caption("Your AI Employee currently uses read-only access to connected sources.")
        section("Try asking")
        st.markdown(
            "- How many sessions did we get last week?\n"
            "- Which channel drove the most traffic?\n"
            "- Summarize our performance this month."
        )


def render_tasks():
    page_header("My Tasks", "Plan, manage and monitor your AI Employee's work.", tag="IN DEVELOPMENT")
    a, b, c = st.columns(3, gap="medium")
    with a: metric_card("All tasks", "—", "Awaiting Supabase integration")
    with b: metric_card("Scheduled", "—", "Scheduler not enabled")
    with c: metric_card("Completed runs", "—", "No execution service yet")
    section("Task workspace")
    empty_feature(
        "☑", "Your task manager is ready for the next development phase.",
        "We'll connect the existing Supabase tasks and task_runs tables here, "
        "then enable approvals, schedules and execution history. "
        "This page does not create or run tasks yet.",
    )


def render_usage(rows):
    page_header("Usage", "Understand how your AI Employee is being used.", tag="YOUR ACTIVITY")
    now, month_start, last_30 = cairo_periods()
    summary = usage_summary(rows, month_start)
    thirty = usage_summary(rows, last_30)
    k1,k2,k3,k4 = st.columns(4, gap="medium")
    with k1: metric_card("AI requests", format_tokens(summary["requests"]), "This month")
    with k2: metric_card("Model calls", format_tokens(summary["api_calls"]), "All calls, including tool turns")
    with k3: metric_card("Total tokens", format_tokens(summary["tokens"]), "This month")
    with k4: metric_card("Est. AI cost", format_cost(summary["cost"]), "Paid-tier equivalent")
    section("Usage trends", "Based on your own account activity")
    left, right = st.columns([1,1.2], gap="large")
    with left:
        with st.container(border=True):
            st.markdown("**Monthly token breakdown**")
            usage_donut(summary)
    with right:
        with st.container(border=True):
            st.markdown("**Last 30 days**")
            usage_daily_chart(thirty)
    section("Your usage details")
    with st.container(border=True):
        st.markdown(f"**Input tokens:** {format_tokens(summary['input'])}")
        st.markdown(f"**Output tokens:** {format_tokens(summary['output'])}")
        st.markdown(f"**Thinking tokens:** {format_tokens(summary['thinking'])}")
        st.markdown(f"**Tool requests:** {format_tokens(summary['tools'])}")
        st.caption("Tool requests count actions the model asked for. They are not Composio billable units.")
        if summary["unpriced"]:
            st.warning(f"{summary['unpriced']} model calls were missing usage metadata and are not priced.")
    st.info("Cost figures use published paid-tier Gemini token rates. "
            "They don't include Composio, hosting, taxes, or any free-tier allowances. "
            "Credits and hard spending limits are not enabled yet.")


def render_settings(user):
    page_header("Settings", "Manage your workspace preferences and account.", tag="YOUR WORKSPACE")
    left,right = st.columns([1.4,1],gap="large")
    with left:
        section("Account details")
        with st.container(border=True):
            st.markdown("**Signed-in email**")
            st.code(user.get("email") or "Unavailable")
            st.markdown("**Account security**")
            st.success("Your session is protected")
            st.markdown("**Default timezone**")
            st.caption("Africa/Cairo · Scheduled-task timezone will be configurable later")
    with right:
        section("AI configuration")
        with st.container(border=True):
            st.markdown("**AI Employee**")
            st.success("Ready for data analysis")
            st.markdown("**Source permissions**")
            st.caption("Read-only access to connected data")
            st.caption("Custom AI instructions and task-delivery preferences are planned.")


# ==========================================
# MAIN APPLICATION
# ==========================================

inject_theme()
if st.query_params.get("oauth_return") == "1":
    render_oauth_return()
user = get_signed_in_user()
if not user:
    render_login()
    st.stop()

if "connect_links" not in st.session_state:
    st.session_state["connect_links"] = {}

active_connections, accounts_by_toolkit, connection_errors = load_connection_state()
page = render_sidebar(user, active_connections)

if page in ("Overview", "Usage"):
    try:
        usage_rows = fetch_usage_rows()
    except RuntimeError:
        st.error("Usage data is temporarily unavailable. Please check that your Supabase access is configured.")
        st.stop()

if page == "Overview":
    render_overview(user, active_connections, connection_errors, usage_rows)
elif page == "AI Chat":
    render_ai_chat(active_connections)
elif page == "My Tasks":
    render_tasks()
elif page == "Integrations":
    render_integrations(accounts_by_toolkit, active_connections, connection_errors)
elif page == "Usage":
    render_usage(usage_rows)
elif page == "Settings":
    render_settings(user)
