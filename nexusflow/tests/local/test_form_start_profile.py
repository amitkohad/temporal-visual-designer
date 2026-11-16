"""Local form-start enablement profile; never starts Temporal or HTTP."""

from nexusflow.apps.local_bootstrap import initialize
from nexusflow.apps.local_form_starts import enable_form_starts, load_form_start_profile


def test_enable_form_starts_is_idempotent_and_dev_only(tmp_path):
    initialize(tmp_path)
    first = enable_form_starts(tmp_path)
    second = enable_form_starts(tmp_path)
    assert first == second
    settings = __import__(
        "nexusflow.apps.local_bootstrap", fromlist=["load_settings"]
    ).load_settings(tmp_path)
    loaded = load_form_start_profile(tmp_path, settings)
    assert loaded == first
    assert loaded.scope.environment_id == "dev"
    assert (tmp_path / "form-starts.db").is_file()
    assert (tmp_path / "form-starts.json").is_file()
