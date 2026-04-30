from meridian.agent_streaming import iter_static_text_chunks


def test_iter_static_text_chunks():
    assert "".join(iter_static_text_chunks("Hello world", chunk_chars=5)) == "Hello world"
