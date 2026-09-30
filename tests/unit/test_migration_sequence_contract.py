from pathlib import Path
import re


ROOT = Path(__file__).parents[2]
MIGRATIONS_DIR = ROOT / "migrations"
MIGRATION_NAME = re.compile(r"^(?P<number>\d{3})_[a-z0-9_]+\.sql$")


def _migration_files() -> list[Path]:
    return sorted(MIGRATIONS_DIR.glob("*.sql"))


def test_migration_directory_is_nonempty_and_names_are_canonical() -> None:
    files = _migration_files()
    assert files, "HOPE requires at least one schema migration"
    invalid = [path.name for path in files if MIGRATION_NAME.fullmatch(path.name) is None]
    assert invalid == [], f"non-canonical migration filenames: {invalid}"


def test_migration_numbers_are_unique_contiguous_and_match_lexical_order() -> None:
    files = _migration_files()
    matches = [MIGRATION_NAME.fullmatch(path.name) for path in files]
    assert all(match is not None for match in matches)
    numbers = [int(match.group("number")) for match in matches if match is not None]

    assert len(numbers) == len(set(numbers)), "duplicate migration numbers"
    assert numbers == list(range(1, len(numbers) + 1)), (
        "migration numbers must be contiguous from 001 with no gaps"
    )
    assert [path.name for path in files] == sorted(path.name for path in files), (
        "filesystem discovery order must equal deterministic lexical order"
    )


def test_latest_migration_number_matches_discovered_migration_count() -> None:
    files = _migration_files()
    latest = MIGRATION_NAME.fullmatch(files[-1].name)
    assert latest is not None
    assert int(latest.group("number")) == len(files)
