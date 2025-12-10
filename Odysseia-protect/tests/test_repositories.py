import pytest
from sqlalchemy.ext.asyncio import AsyncSession

# Import your project's models and repositories
from src.database.models import UploadMode
from src.database.repositories.resource import ResourceRepository
from src.database.repositories.thread import ThreadRepository
from src.database.schemas import ResourceCreate, ThreadCreate


@pytest.mark.asyncio
async def test_create_and_get_resource(db_session: AsyncSession):
    """
    Tests the creation and retrieval of a Resource entity.
    """
    resource_repo = ResourceRepository()
    thread_repo = ThreadRepository()

    # 1. First, create a Thread as a dependency
    thread_obj_in = ThreadCreate(
        public_thread_id=98765,
        warehouse_thread_id=56789,
        author_id=123456,
    )
    created_thread = await thread_repo.create(db_session, obj_in=thread_obj_in)
    await db_session.flush()  # Flush to get the auto-generated ID
    assert created_thread.id is not None

    # 2. Now, create a Resource that links to the Thread
    resource_obj_in = ResourceCreate(
        thread_id=created_thread.id,
        upload_mode=UploadMode.SECURE,
        filename="test_file.zip",
        version_info="v1.0",
        source_message_id=1122334455,
        description=None,
        password=None,
    )
    created_resource = await resource_repo.create(db_session, obj_in=resource_obj_in)
    await db_session.flush()  # Flush to get the resource ID

    # 3. Assert the created object has the correct data
    assert created_resource.id is not None
    assert created_resource.filename == "test_file.zip"
    assert created_resource.source_message_id == 1122334455
    assert created_resource.thread_id == created_thread.id

    # 4. Retrieve the object from the database
    await (
        db_session.flush()
    )  # Manually flush the session to make the data available for querying
    retrieved_resource = await resource_repo.get(db_session, id=created_resource.id)

    # 5. Assert the retrieved object is the same and data matches
    assert retrieved_resource is not None
    assert retrieved_resource.id == created_resource.id
    assert retrieved_resource.version_info == "v1.0"


@pytest.mark.asyncio
async def test_update_resource(db_session: AsyncSession):
    """
    Tests updating an existing Resource.
    """
    resource_repo = ResourceRepository()
    thread_repo = ThreadRepository()

    # 1. Create dependencies
    thread_obj_in = ThreadCreate(
        public_thread_id=98765,
        author_id=123456,
        warehouse_thread_id=None,
    )
    created_thread = await thread_repo.create(db_session, obj_in=thread_obj_in)
    await db_session.flush()  # Flush to get the auto-generated ID

    resource_obj_in = ResourceCreate(
        thread_id=created_thread.id,
        upload_mode=UploadMode.NORMAL,
        filename="test_file.zip",
        version_info="v1.0",
        source_message_id=1122334455,
        description=None,
        password=None,
    )
    created_resource = await resource_repo.create(db_session, obj_in=resource_obj_in)
    await db_session.flush()  # Flush to get the resource ID

    # 2. Update the resource
    update_data = {"version_info": "v1.1-updated", "password": "new_password"}
    updated_resource = await resource_repo.update(
        db_session, db_obj=created_resource, obj_in=update_data
    )

    # 3. Assert the returned object is updated
    assert updated_resource is not None
    assert updated_resource.version_info == "v1.1-updated"
    assert updated_resource.password == "new_password"

    # 4. Verify by fetching from DB again
    await db_session.commit()  # Commit the transaction to save the update
    await db_session.refresh(updated_resource)
    assert updated_resource.version_info == "v1.1-updated"


@pytest.mark.asyncio
async def test_delete_resource(db_session: AsyncSession):
    """
    Tests deleting a Resource.
    """
    resource_repo = ResourceRepository()
    thread_repo = ThreadRepository()

    # 1. Create dependencies
    thread_obj_in = ThreadCreate(
        public_thread_id=98765,
        author_id=123456,
        warehouse_thread_id=None,
    )
    created_thread = await thread_repo.create(db_session, obj_in=thread_obj_in)
    await db_session.flush()  # Flush to get the auto-generated ID

    resource_obj_in = ResourceCreate(
        thread_id=created_thread.id,
        upload_mode=UploadMode.NORMAL,
        filename="test_file.zip",
        version_info="v1.0",
        description=None,
        source_message_id=1122334455,
        password=None,
    )
    created_resource = await resource_repo.create(db_session, obj_in=resource_obj_in)
    await db_session.flush()  # Flush to get the resource ID
    resource_id = created_resource.id

    # 2. Delete the resource
    deleted_resource = await resource_repo.remove(db_session, id=resource_id)

    # 3. Assert the correct object was returned on deletion
    assert deleted_resource is not None
    assert deleted_resource.id == resource_id

    # 4. Verify it's gone from the DB
    retrieved_resource = await resource_repo.get(db_session, id=resource_id)
    assert retrieved_resource is None
