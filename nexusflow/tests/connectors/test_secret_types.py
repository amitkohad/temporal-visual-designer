"""Private credential handles and closed secret-resolver errors."""

import pytest

from nexusflow.contracts.models import Scope
from nexusflow.modules.connectors.secret_types import (
    HTTPAuthorizationCredential,
    SecretResolverError,
)

PRIVATE = "bearer-token-sentinel-value"
SCOPE = Scope(organization_id="organization", environment_id="dev")


def test_credential_repr_hides_token_and_builds_authorization_header():
    credential = HTTPAuthorizationCredential("Bearer", PRIVATE)
    assert credential.authorization_header() == "Bearer " + PRIVATE
    assert PRIVATE not in repr(credential)
    assert "token" not in repr(credential)


@pytest.mark.parametrize(
    "scheme,token",
    [
        ("Basic", PRIVATE),
        ("Bearer", ""),
        ("Bearer", "x" * 8193),
        ("Bearer", "bad\ntoken"),
        ("Bearer", "bad\rtoken"),
        ("Bearer", "bad\x00token"),
        ("Bearer", None),
    ],
)
def test_invalid_credentials_are_rejected(scheme, token):
    with pytest.raises((ValueError, TypeError)):
        HTTPAuthorizationCredential(scheme, token)


def test_secret_resolver_error_normalizes_unknown_codes_without_echoing_private_data():
    error = SecretResolverError(PRIVATE)
    assert error.code == "unavailable"
    assert PRIVATE not in str(error) + repr(error)
    assert error.retryable is False
