from meridian.auth_audit import email_domain_only


def test_email_domain_only():
    assert email_domain_only("User@Example.COM") == "example.com"
    assert email_domain_only("bad") == "invalid"
    assert email_domain_only("") == "invalid"
