"""Feature boundary integration with real token verification and identity storage."""

import json

import pytest
from jsonschema import Draft202012Validator

from nexusflow.modules.identity_environments.service import IdentityService
from nexusflow.modules.identity_environments.types import IdentityError
from nexusflow.modules.project_versioning.authorization import IdentityProjectAuthorization
from nexusflow.modules.project_versioning.canonical import compare_json
from nexusflow.modules.project_versioning.repository import SQLiteProjectRepository
from nexusflow.modules.project_versioning.service import ProjectService
from nexusflow.modules.project_versioning.types import ProjectContent
from nexusflow.modules.project_versioning.validation import BuiltinContentValidator
from nexusflow.tests.identity.test_service import identity  # noqa: F401


class NoArtifacts:
    def resolve(self, reference):
        raise AssertionError("Empty draft must not resolve activities")


def test_revocation_checked_through_project_service(identity, tmp_path):  # noqa: F811
    identity_repo, scope, grant, keys, token = identity
    repo = SQLiteProjectRepository(tmp_path / "project.db")
    try:
        service = ProjectService(
            repo,
            IdentityProjectAuthorization(IdentityService(identity_repo, keys), token()),
            BuiltinContentValidator(),
            NoArtifacts(),
        )
        revision = service.create(scope, "onboarding", "Onboarding", '{"schema_version":"0.1"}')
        assert revision.author_id == "pairwise-subject"
        identity_repo.revoke_grant(scope, grant.grant_id, expected_revision=1)
        with pytest.raises(IdentityError):
            service.read(scope, "onboarding")
        assert repo.revision(scope, "onboarding").number == 1
    finally:
        repo.close()


def test_comparison_escapes_paths_and_distinguishes_absence_from_null():
    changes = compare_json('{"a/b":null,"layout":{"x":1}}', '{"til~de":null,"layout":{"x":2}}')
    assert [(change.path, change.before_json, change.after_json) for change in changes] == [
        ("/a~1b", "null", None),
        ("/til~0de", None, "null"),
    ]
    assert len(compare_json('{"layout":{}}', '{"layout":{"x":1}}', True)) == 1


def test_project_schema_export_is_valid():
    schema = ProjectContent.model_json_schema()
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(json.loads('{"schema_version":"0.1"}'))
