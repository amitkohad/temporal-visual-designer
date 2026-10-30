"""Trusted host selection plus fresh worker grants; no environment-name heuristic."""

from unittest.mock import Mock

import pytest

from nexusflow.contracts.models import Scope
from nexusflow.modules.connectors.mock_authorization import ExplicitDevHTTPMockPolicy
from nexusflow.modules.connectors.mock_types import HTTPMockError

SCOPE = Scope(organization_id="acme", environment_id="sandbox-42")


def test_explicit_scope_and_worker_permission_both_required():
    worker = Mock()
    worker.authorize.return_value = "worker-1"
    policy = ExplicitDevHTTPMockPolicy(worker, (SCOPE,))
    grant = policy.authorize(SCOPE, "connector.mock.execute", "hr")
    assert grant.scope == SCOPE and grant.environment_kind == "dev" and grant.actor_id == "worker-1"
    worker.authorize.assert_called_once_with(SCOPE, "worker.execute", "hr")


@pytest.mark.parametrize("environment", ["dev", "uat", "prod", "sandbox-43"])
def test_names_do_not_authorize_unknown_scopes(environment):
    worker = Mock()
    policy = ExplicitDevHTTPMockPolicy(worker, (SCOPE,))
    with pytest.raises(HTTPMockError) as caught:
        policy.authorize(
            Scope(organization_id="acme", environment_id=environment),
            "connector.mock.execute",
            "hr",
        )
    assert caught.value.code == "forbidden"
    worker.authorize.assert_not_called()


def test_worker_revocation_is_fresh_on_each_call():
    worker = Mock()
    worker.authorize.return_value = "worker-1"
    policy = ExplicitDevHTTPMockPolicy(worker, (SCOPE,))
    policy.authorize(SCOPE, "connector.mock.execute", "hr")
    worker.authorize.side_effect = PermissionError("private-worker-error")
    with pytest.raises(HTTPMockError) as caught:
        policy.authorize(SCOPE, "connector.mock.execute", "hr")
    assert caught.value.code == "forbidden" and not caught.value.delivery_may_have_occurred
    assert "private-worker-error" not in str(caught.value) + repr(caught.value)


@pytest.mark.parametrize("actor", [None, False, 1, "", "bad actor", "private\nworker"])
def test_invalid_worker_actors_fail_closed(actor):
    worker = Mock()
    worker.authorize.return_value = actor
    with pytest.raises(HTTPMockError) as caught:
        ExplicitDevHTTPMockPolicy(worker, (SCOPE,)).authorize(SCOPE, "connector.mock.execute", "hr")
    assert caught.value.code == "invalid_provider_result" and not caught.value.retryable


@pytest.mark.parametrize("scopes", [[SCOPE], (SCOPE, SCOPE), (None,)])
def test_invalid_or_duplicate_scope_configuration_is_rejected(scopes):
    with pytest.raises(ValueError, match="Invalid HTTP mock Dev scope configuration"):
        ExplicitDevHTTPMockPolicy(Mock(), scopes)


def test_original_scope_mutation_does_not_change_configuration():
    selected = Scope(organization_id="acme", environment_id="sandbox-42")
    worker = Mock()
    worker.authorize.return_value = "worker-1"
    policy = ExplicitDevHTTPMockPolicy(worker, (selected,))
    object.__setattr__(selected, "environment_id", "prod")
    with pytest.raises(HTTPMockError):
        policy.authorize(selected, "connector.mock.execute", "hr")
    assert policy.authorize(SCOPE, "connector.mock.execute", "hr").scope == SCOPE


def test_unexpected_permission_and_cross_org_denied_before_worker():
    worker = Mock()
    policy = ExplicitDevHTTPMockPolicy(worker, (SCOPE,))
    for scope, permission in (
        (SCOPE, "worker.execute"),
        (
            Scope(organization_id="other", environment_id=SCOPE.environment_id),
            "connector.mock.execute",
        ),
    ):
        with pytest.raises(HTTPMockError) as caught:
            policy.authorize(scope, permission, "hr")
        assert caught.value.code == "forbidden"
    worker.authorize.assert_not_called()
