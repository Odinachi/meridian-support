from meridian.logout_intent import looks_like_logout_request, parse_logout_confirmation


def test_logout_phrases():
    assert looks_like_logout_request("logout")
    assert looks_like_logout_request("  Log Out  ")
    assert looks_like_logout_request("sign out")
    assert looks_like_logout_request("I want to log out")
    assert not looks_like_logout_request("log out please")


def test_confirmation():
    assert parse_logout_confirmation("yes") == "yes"
    assert parse_logout_confirmation("Y") == "yes"
    assert parse_logout_confirmation("sign me out") == "yes"
    assert parse_logout_confirmation("no") == "no"
    assert parse_logout_confirmation("Cancel") == "no"
    assert parse_logout_confirmation("maybe") is None
