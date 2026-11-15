"""SMTP login credentials hide private material and reject unsafe values."""

import pytest

from nexusflow.modules.connectors.smtp_secret_types import SMTPLoginCredential


def test_repr_hides_username_and_password():
    credential = SMTPLoginCredential("login", "mailer@example.test", "s3cret-value")
    text = repr(credential)
    assert "login" in text
    assert "mailer@example.test" not in text
    assert "s3cret-value" not in text


@pytest.mark.parametrize(
    "kwargs",
    [
        {"scheme": "Bearer", "username": "u", "password": "p"},
        {"scheme": "login", "username": "", "password": "p"},
        {"scheme": "login", "username": "u", "password": ""},
        {"scheme": "login", "username": "u\n", "password": "p"},
        {"scheme": "login", "username": "u", "password": "p\r"},
        {"scheme": "login", "username": "u\x00", "password": "p"},
    ],
)
def test_rejects_invalid_credentials(kwargs):
    with pytest.raises(ValueError):
        SMTPLoginCredential(**kwargs)
