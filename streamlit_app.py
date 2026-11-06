
import streamlit as st

st.set_page_config(page_title="AI Employee", page_icon="🤖")

st.title("🤖 AI Employee")
st.caption("Your personal AI-powered data assistant")

if "messages" not in st.session_state:
    st.session_state.messages = []

for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])

if prompt := st.chat_input("What would you like me to do?"):
    st.session_state.messages.append({
        "role": "user",
        "content": prompt
    })

    with st.chat_message("user"):
        st.markdown(prompt)

    response = "Hello! My chat interface is ready. AI integration is coming next."

    with st.chat_message("assistant"):
        st.markdown(response)

    st.session_state.messages.append({
        "role": "assistant",
        "content": response
    })
