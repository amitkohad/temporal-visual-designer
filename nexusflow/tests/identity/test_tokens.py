"""Local signed RSA tests only; no live Entra or Graph requests."""

import json
import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from nexusflow.modules.identity_environments.tokens import EntraTokenVerifier
from nexusflow.modules.identity_environments.types import DirectoryBinding, IdentityError

TENANT = "11111111-1111-4111-8111-111111111111"
OBJECT = "22222222-2222-4222-8222-222222222222"
GROUP = "33333333-3333-4333-8333-333333333333"
BINDING = DirectoryBinding(
    "org",
    TENANT,
    "api://nexus",
    allowed_user_clients=("browser",),
    allowed_workload_clients=("worker",),
)


class Keys:
    def __init__(self):
        self.private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.kid = "one"
        self.fail = False

    def jwks(self, tenant):
        assert tenant == TENANT
        if self.fail:
            raise RuntimeError("secret")
        return {
            "keys": [
                {
                    **json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(self.private.public_key())),
                    "kid": self.kid,
                }
            ]
        }

    def token(self, changes=None, headers=None, remove=()):
        now = int(time.time())
        claims = dict(
            tid=TENANT,
            oid=OBJECT,
            sub="subject",
            ver="2.0",
            aud=BINDING.audience,
            iss=f"https://login.microsoftonline.com/{TENANT}/v2.0",
            iat=now,
            nbf=now,
            exp=now + 300,
            azp="browser",
            scp="access_as_user",
            groups=[],
        )
        claims.update(changes or {})
        for name in remove:
            claims.pop(name, None)
        return jwt.encode(
            claims, self.private, algorithm="RS256", headers={"kid": self.kid, **(headers or {})}
        )


class Groups:
    memberships = frozenset({GROUP})
    fail = False

    def groups(self, tenant, oid):
        assert (tenant, oid) == (TENANT, OBJECT)
        if self.fail:
            raise RuntimeError("secret")
        return self.memberships


@pytest.fixture
def keys():
    return Keys()


def test_stable_principal(keys):
    principal = EntraTokenVerifier(BINDING, keys).verify(keys.token())
    assert principal.actor.subject == "subject"
    assert principal.object_id == OBJECT
    assert principal.kind == "user"


@pytest.mark.parametrize(
    "changes",
    [
        {"aud": "bad"},
        {"iss": "https://evil"},
        {"exp": 1},
        {"tid": "bad"},
        {"oid": "bad"},
        {"ver": "1.0"},
        {"azp": "other"},
        {"scp": "bad"},
        {"sub": ""},
        {"exp": "9999999999"},
        {"iat": True},
        {"idtyp": "other"},
        {"groups": ["display-name"]},
    ],
)
def test_invalid_claims(keys, changes):
    with pytest.raises(IdentityError) as error:
        EntraTokenVerifier(BINDING, keys).verify(keys.token(changes))
    assert error.value.code == "unauthenticated"
    assert str(error.value) == "Invalid access token"


@pytest.mark.parametrize("name", ["tid", "oid", "sub", "exp", "iat", "nbf", "ver", "azp"])
def test_missing_claims(keys, name):
    with pytest.raises(IdentityError):
        EntraTokenVerifier(BINDING, keys).verify(keys.token(remove=(name,)))


@pytest.mark.parametrize(
    "header",
    [
        {"jku": "https://evil"},
        {"jwk": {}},
        {"x5u": "https://evil"},
        {"crit": []},
        {"kid": "unknown"},
    ],
)
def test_header_injection(keys, header):
    with pytest.raises(IdentityError):
        EntraTokenVerifier(BINDING, keys).verify(keys.token(headers=header))


def test_algorithm_confusion(keys):
    token = jwt.encode(
        {"secret": "never-leak"}, "a" * 64, algorithm="HS256", headers={"kid": "one"}
    )
    with pytest.raises(IdentityError) as error:
        EntraTokenVerifier(BINDING, keys).verify(token)
    assert "never-leak" not in str(error.value)


def test_key_rotation(keys):
    verifier = EntraTokenVerifier(BINDING, keys)
    old = keys.token()
    verifier.verify(old)
    keys.private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    keys.kid = "two"
    verifier.verify(keys.token())
    with pytest.raises(IdentityError):
        verifier.verify(old)


def test_provider_failure(keys):
    token = keys.token()
    keys.fail = True
    with pytest.raises(IdentityError) as error:
        EntraTokenVerifier(BINDING, keys).verify(token)
    assert error.value.code == "unavailable"
    assert "secret" not in str(error.value)


@pytest.mark.parametrize(
    "overage",
    [
        {},
        {"hasgroups": True},
        {
            "_claim_names": {"groups": "src"},
            "_claim_sources": {"src": {"endpoint": "https://evil"}},
        },
    ],
)
def test_current_membership_no_cache(keys, overage):
    groups = Groups()
    verifier = EntraTokenVerifier(BINDING, keys, groups)
    token = keys.token(overage)
    assert verifier.verify(token).groups == frozenset({GROUP})
    groups.memberships = frozenset()
    assert not verifier.verify(token).groups
    groups.fail = True
    with pytest.raises(IdentityError) as error:
        verifier.verify(token)
    assert error.value.code == "unavailable"


@pytest.mark.parametrize(
    "claims", [{"groups": [GROUP]}, {"hasgroups": True}, {"_claim_names": {"groups": "src"}}]
)
def test_group_claims_require_current_provider(keys, claims):
    with pytest.raises(IdentityError) as error:
        EntraTokenVerifier(BINDING, keys).verify(keys.token(claims))
    assert error.value.code == "unavailable"


def test_workload_separation(keys):
    token = keys.token(
        {"idtyp": "app", "azp": "worker", "roles": ["NexusFlow.Worker"], "groups": [GROUP]},
        remove=("scp",),
    )
    principal = EntraTokenVerifier(BINDING, keys).verify(token)
    assert principal.kind == "workload"
    assert not principal.groups
    with pytest.raises(IdentityError):
        EntraTokenVerifier(BINDING, keys).verify(
            keys.token({"idtyp": "app", "azp": "worker", "roles": ["NexusFlow.Worker"]})
        )


def test_empty_allowed_clients(keys):
    with pytest.raises(IdentityError):
        EntraTokenVerifier(DirectoryBinding("org", TENANT, BINDING.audience), keys).verify(
            keys.token()
        )


@pytest.mark.parametrize(
    "token", ["", "x" * 32769, "not-a-token"], ids=["empty", "oversized", "malformed"]
)
def test_invalid_input(keys, token):
    with pytest.raises(IdentityError):
        EntraTokenVerifier(BINDING, keys).verify(token)


@pytest.mark.parametrize(
    "changes", [{"roles": []}, {"roles": "NexusFlow.Worker"}, {"azp": "browser"}]
)
def test_workload_requires_explicit_client_and_role(keys, changes):
    claims = {"idtyp": "app", "azp": "worker", "roles": ["NexusFlow.Worker"], **changes}
    with pytest.raises(IdentityError):
        EntraTokenVerifier(BINDING, keys).verify(keys.token(claims, remove=("scp",)))


def test_future_activation_rejected(keys):
    with pytest.raises(IdentityError):
        EntraTokenVerifier(BINDING, keys).verify(keys.token({"nbf": int(time.time()) + 3600}))


def test_signed_group_claims_checked_before_resolution(keys):
    with pytest.raises(IdentityError):
        EntraTokenVerifier(BINDING, keys, Groups()).verify(keys.token({"groups": ["not-uuid"]}))


def test_invalid_resolved_groups_rejected(keys):
    groups = Groups()
    groups.memberships = frozenset({"not-uuid"})
    with pytest.raises(IdentityError):
        EntraTokenVerifier(BINDING, keys, groups).verify(keys.token())


def test_unsigned_token_rejected(keys):
    token = jwt.encode({"sub": "subject"}, key="", algorithm="none", headers={"kid": "one"})
    with pytest.raises(IdentityError):
        EntraTokenVerifier(BINDING, keys).verify(token)
