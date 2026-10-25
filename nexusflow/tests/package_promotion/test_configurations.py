"""Destination configuration immutability, isolation and safe local validation."""

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from nexusflow.contracts.models import Scope
from nexusflow.modules.package_promotion.configuration_types import ConfigurationWrite
from nexusflow.modules.package_promotion.configurations import (
    ConfigurationService,
    SQLiteConfigurationRepository,
)
from nexusflow.modules.package_promotion.types import ActivationError

SCOPE = Scope(organization_id="org", environment_id="uat")


class Authorization:
    def __init__(self):
        self.calls = []
        self.allowed = True

    def authorize(self, scope, permission, resource):
        self.calls.append((scope, permission, resource))
        if not self.allowed:
            raise ActivationError("forbidden", "Denied")
        return "actor"


def test_retry_restart_isolation_and_pagination(tmp_path):
    path = tmp_path / "configuration.db"
    repo = SQLiteConfigurationRepository(path, maximum_revisions_per_project=2)
    first = repo.put(SCOPE, "project", "v1", '{"b":2,"a":1}', "first")
    assert repo.put(SCOPE, "project", "v1", '{ "a":1,"b":2 }', "second") == first
    repo.put(SCOPE, "project", "v2", "{}", "actor")
    page = repo.list(SCOPE, "project", limit=1)
    assert page.items[0].configuration_revision == "v2"
    assert repo.list(SCOPE, "project", before_revision=page.next_before_revision).items == (first,)
    with pytest.raises(ActivationError, match="immutable"):
        repo.put(SCOPE, "project", "v1", "{}", "actor")
    with pytest.raises(ActivationError, match="quota"):
        repo.put(SCOPE, "project", "v3", "{}", "actor")
    assert repo.put(SCOPE, "project", "v1", '{"a":1,"b":2}', "third") == first
    repo.close()
    repo = SQLiteConfigurationRepository(path)
    assert repo.get(SCOPE, "project", "v1").metadata == first
    assert repo.get(SCOPE, "project", "v1").values_json == '{"a":1,"b":2}'
    for scope, project in [
        (Scope(organization_id="other", environment_id="uat"), "project"),
        (Scope(organization_id="org", environment_id="prod"), "project"),
        (SCOPE, "other"),
    ]:
        assert not repo.supports(scope, project, "v1", {})
    repo.close()


def test_concurrent_connections_retry_and_quota(tmp_path):
    path = tmp_path / "configuration.db"
    repositories = [
        SQLiteConfigurationRepository(path, maximum_revisions_per_project=1) for _ in range(2)
    ]
    with ThreadPoolExecutor(2) as pool:
        receipts = list(
            pool.map(lambda repo: repo.put(SCOPE, "p", "r", "{}", "actor"), repositories)
        )
    assert receipts[0] == receipts[1]
    for repo in repositories:
        repo.close()


@pytest.mark.parametrize(
    "value",
    [
        '{"value":NaN}',
        '{"value":Infinity}',
        "[]",
        '{"value":"' + "x" * 32768 + '"}',
        json.dumps({"items": [0] * 1001}),
        '{"a":' * 18 + "0" + "}" * 18,
    ],
    ids=["nan", "infinity", "array", "bytes", "entries", "depth"],
)
def test_bounded_values(value):
    repo = SQLiteConfigurationRepository(":memory:")
    with pytest.raises(ActivationError) as error:
        repo.put(SCOPE, "p", "r", value, "actor")
    assert error.value.code == "invalid_request"
    assert repo.list(SCOPE, "p").items == ()
    repo.close()


def test_local_schema_checks_and_refs():
    repo = SQLiteConfigurationRepository(":memory:")
    repo.put(SCOPE, "p", "r", '{"count":3}', "actor")
    assert repo.supports(
        SCOPE,
        "p",
        "r",
        {"type": "object", "required": ["count"], "properties": {"count": {"type": "integer"}}},
    )
    assert not repo.supports(SCOPE, "p", "r", {"required": ["missing"]})
    for schema in [
        {"type": "garbage"},
        {"$ref": "https://invalid.example/schema"},
        {"$defs": {"x": {"$dynamicRef": "#x"}}},
        {"$defs": {"x": {"$ref": "#"}}},
    ]:
        assert not repo.supports(SCOPE, "p", "r", schema)
    repo.close()
    with pytest.raises(OSError, match="unavailable"):
        repo.supports(SCOPE, "p", "r", {})


@pytest.mark.parametrize("replacement", ['{"changed":true}', '{ "x": 1 }', "not-json"])
def test_corruption_fails_closed(tmp_path, replacement):
    path = tmp_path / "configuration.db"
    repo = SQLiteConfigurationRepository(path)
    repo.put(SCOPE, "p", "r", '{"x":1}', "actor")
    with sqlite3.connect(path) as db:
        db.execute("UPDATE configurations SET values_json=?", (replacement,))
    with pytest.raises(OSError, match="unavailable"):
        repo.get(SCOPE, "p", "r")
    with pytest.raises(OSError, match="unavailable"):
        repo.supports(SCOPE, "p", "r", {})
    repo.close()


def test_service_authorizes_fresh_and_returns_only_metadata():
    repo = SQLiteConfigurationRepository(":memory:")
    auth = Authorization()
    service = ConfigurationService(repo, auth)
    metadata = service.put(SCOPE, "p", "r", ConfigurationWrite(values={"public": "value"}))
    assert service.get(SCOPE, "p", "r") == metadata
    assert service.list(SCOPE, "p").items == (metadata,)
    assert "values" not in metadata.model_dump()
    assert len(auth.calls) == 3
    assert all(call[1:] == ("connection.manage", "configurations") for call in auth.calls)
    auth.allowed = False
    with pytest.raises(ActivationError) as error:
        service.get(SCOPE, "p", "r")
    assert error.value.code == "forbidden"
    auth.allowed = True
    repo.close()
    with pytest.raises(ActivationError) as error:
        service.get(SCOPE, "p", "r")
    assert error.value.code == "unavailable"


def test_maximum_revision_is_listed(tmp_path):
    path = tmp_path / "configuration.db"
    repo = SQLiteConfigurationRepository(path)
    repo.put(SCOPE, "p", "r1", "{}", "actor")
    with sqlite3.connect(path) as db:
        db.execute("UPDATE configurations SET revision=?", (2**63 - 2,))
    last = repo.put(SCOPE, "p", "r2", "{}", "actor")
    assert last.revision == 2**63 - 1
    assert repo.list(SCOPE, "p", limit=1).items == (last,)
    assert repo.list(SCOPE, "p", before_revision=last.revision).items[0].revision == 2**63 - 2
    with pytest.raises(ActivationError, match="quota"):
        repo.put(SCOPE, "p", "r3", "{}", "actor")
    repo.close()


def test_distinct_concurrent_revisions_respect_atomic_quota(tmp_path):
    path = tmp_path / "configuration.db"
    repositories = [
        SQLiteConfigurationRepository(path, maximum_revisions_per_project=1) for _ in range(2)
    ]

    def write(pair):
        index, repo = pair
        try:
            return repo.put(SCOPE, "p", f"r{index}", "{}", "actor")
        except ActivationError as error:
            return error.code

    with ThreadPoolExecutor(2) as pool:
        outcomes = list(pool.map(write, enumerate(repositories)))
    assert sum(outcome == "conflict" for outcome in outcomes) == 1
    assert len(repositories[0].list(SCOPE, "p").items) == 1
    for repo in repositories:
        repo.close()
