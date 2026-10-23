"""Independent-review regressions and portable intake contracts."""

import json

import pytest
from jsonschema import Draft202012Validator

from nexusflow.modules.activity_catalog.types import CatalogError, IntakeLimits
from nexusflow.modules.activity_catalog.validation import validate_bundle
from nexusflow.tests.activity_catalog.test_intake import source_bundle
from nexusflow.tests.activity_catalog.test_validation import archive


def test_declared_python_312_rejects_newer_grammar_even_on_a_313_host():
    data, _ = source_bundle(b"def run[T = int](value):\n    return value\n")
    with pytest.raises(CatalogError) as error:
        validate_bundle(data)
    assert error.value.code == "incompatible_runtime"


@pytest.mark.parametrize("reverse", [False, True])
def test_source_file_cannot_also_be_a_parent_directory(reverse):
    items = [("src/activity.py", b"pass"), ("src/activity.py/child.py", b"pass")]
    data = archive(dict(reversed(items) if reverse else items))
    with pytest.raises(CatalogError) as error:
        validate_bundle(data)
    assert error.value.code == "invalid_bundle"


@pytest.mark.parametrize("value", [False, 0, -1, 1.5])
def test_intake_limits_require_positive_integers(value):
    with pytest.raises(ValueError):
        IntakeLimits(maximum_archive_bytes=value)


def test_manifest_schema_export_is_valid_and_deterministic(tmp_path, monkeypatch):
    from nexusflow.modules.activity_catalog.export import main

    monkeypatch.chdir(tmp_path)
    main()
    path = tmp_path / "artifacts/schemas/0.1/activity-bundle-manifest.schema.json"
    original = path.read_bytes()
    Draft202012Validator.check_schema(json.loads(original))
    main()
    assert path.read_bytes() == original


@pytest.mark.parametrize("value", [bytearray(b"zip"), "zip"])
def test_validation_requires_immutable_archive_bytes(value):
    with pytest.raises(CatalogError):
        validate_bundle(value)
