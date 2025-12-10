"""
This file contains shared fixtures for the test suite.
Fixtures defined here are automatically discovered by pytest and can be used in any test file.
"""

import asyncio
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession

# Import the Base model so that the test database knows about our tables.
from src.database.models import Base

# Use an in-memory SQLite database for all tests to ensure isolation and speed.
TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"


@pytest.fixture(scope="session")
def event_loop():
    """
    Creates an instance of the default event loop for the whole test session.
    This is necessary for pytest-asyncio to work correctly with a session-scoped fixture.
    """
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(scope="function")
async def async_engine():
    """
    Creates a new in-memory database engine for each test function.
    It creates all tables defined in the Base metadata before the test runs.
    """
    engine = create_async_engine(TEST_DATABASE_URL)
    async with engine.begin() as conn:
        # Create all tables for the test
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    # Clean up the engine after the test
    await engine.dispose()


@pytest_asyncio.fixture(scope="function")
async def db_session(async_engine):
    """
    Yields a new database session with a transaction that is rolled back after
    the test, ensuring complete test isolation. Each test gets a fresh start.
    """
    connection = await async_engine.connect()
    transaction = await connection.begin()

    session = AsyncSession(bind=connection, expire_on_commit=False)

    yield session

    # After the test, clean up the session and roll back the transaction
    await session.close()
    await transaction.rollback()
    await connection.close()
