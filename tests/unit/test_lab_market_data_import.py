import pytest

from hope.application.market_data.lab_import import validate_lab_database_name


@pytest.mark.parametrize(
    "name",
    ["hope_lab_market_data", "hope_m3_integration_test", "hope_nov17_recovery_fresh"],
)
def test_explicit_lab_test_and_recovery_database_names_are_accepted(name):
    assert validate_lab_database_name(name) == name


@pytest.mark.parametrize(
    "name",
    ["hope", "postgres", "HOPE_LAB", "hope-preview", "hope_prod", " hope_lab"],
)
def test_production_and_ambiguous_database_names_are_refused(name):
    with pytest.raises(ValueError, match="LAB_DATABASE_NAME_REQUIRED|PRODUCTION_DATABASE_FORBIDDEN"):
        validate_lab_database_name(name)
