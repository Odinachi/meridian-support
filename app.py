"""Simple Streamlit chatbot UI with session history and streamed replies."""

import time

import streamlit as st


def mock_reply(user_text: str) -> str:
    """Demo assistant — swap this for an API call (OpenAI, Anthropic, etc.)."""
    return (
        f"You wrote **{len(user_text)}** characters.\n\n"
        "This is a local demo response. Replace `mock_reply` in `app.py` "
        "with your model client to use a real chatbot."
    )


def stream_text(text: str, chunk_chars: int = 8):
    """Yield pieces of text for `st.write_stream` (typing effect)."""
    for i in range(0, len(text), chunk_chars):
        yield text[i : i + chunk_chars]
        time.sleep(0.02)


def main():
    st.set_page_config(page_title="Chatbot", page_icon="💬", layout="centered")
    st.title("💬 Simple chatbot")
    st.caption("Demo UI — plug in your LLM where `mock_reply` is called.")

    if "messages" not in st.session_state:
        st.session_state.messages = []

    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])

    if prompt := st.chat_input("Message the bot…"):
        st.session_state.messages.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)

        reply = mock_reply(prompt)
        with st.chat_message("assistant"):
            st.write_stream(stream_text(reply))
        st.session_state.messages.append({"role": "assistant", "content": reply})


if __name__ == "__main__":
    main()
