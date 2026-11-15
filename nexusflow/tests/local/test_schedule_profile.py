"""Local schedule enablement profile; never starts Temporal or HTTP."""

from nexusflow.apps.local_bootstrap import initialize
from nexusflow.apps.local_schedules import enable_schedules, load_schedule_profile


def test_enable_schedules_is_idempotent_and_dev_only(tmp_path):
    initialize(tmp_path)
    first = enable_schedules(tmp_path)
    second = enable_schedules(tmp_path)
    assert first == second
    settings = __import__(
        "nexusflow.apps.local_bootstrap", fromlist=["load_settings"]
    ).load_settings(tmp_path)
    loaded = load_schedule_profile(tmp_path, settings)
    assert loaded == first
    assert loaded.scope.environment_id == "dev"
    assert (tmp_path / "schedules.db").is_file()
    assert (tmp_path / "schedules.json").is_file()
