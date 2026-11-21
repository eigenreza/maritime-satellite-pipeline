from pathlib import Path

import pytest

FIX = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture
def fix() -> Path:
    return FIX


def db_available() -> bool:
    try:
        import psycopg

        from msp.config import db_url

        with psycopg.connect(db_url(), connect_timeout=3):
            return True
    except Exception:
        return False
