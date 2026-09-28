import os

import pytest
from sqlalchemy import create_engine


@pytest.fixture
def postgres_engine():
    url = os.getenv("HOPE_DATABASE_URL")
    if not url:
        pytest.skip("HOPE_DATABASE_URL is not configured")

    engine = create_engine(url)
    try:
        yield engine
    finally:
        engine.dispose(close=True)
