import sqlite3
import subprocess
import os
import sys
from pathlib import Path
from uuid import uuid4

import pytest
import migrate

from migrate import (
    backup_database,
    get_recorded_revision,
)


@pytest.fixture
def database_path():
    path = (
        Path(__file__).resolve().parents[1]
        / "temp"
        / f"migration-test-{uuid4().hex}.db"
    )
    path.parent.mkdir(exist_ok=True)
    try:
        yield path
    finally:
        path.unlink(missing_ok=True)


def create_legacy_database(path, *, with_download_panel: bool = False):
    conn = sqlite3.connect(str(path))
    panel_column = (
        ", download_panel_message_id BIGINT"
        if with_download_panel
        else ""
    )
    conn.executescript(
        f"""
        CREATE TABLE threads (
            id INTEGER PRIMARY KEY NOT NULL,
            public_thread_id BIGINT NOT NULL,
            warehouse_thread_id BIGINT,
            author_id BIGINT NOT NULL,
            quick_mode_enabled BOOLEAN NOT NULL,
            created_at DATETIME NOT NULL
            {panel_column}
        );
        CREATE TABLE resources (
            id INTEGER PRIMARY KEY NOT NULL,
            thread_id INTEGER NOT NULL,
            version_info TEXT NOT NULL,
            upload_mode VARCHAR(6) NOT NULL,
            password TEXT,
            description TEXT,
            source_message_id BIGINT NOT NULL,
            filename VARCHAR(255),
            created_at DATETIME NOT NULL,
            download_count INTEGER NOT NULL
        );
        CREATE TABLE users (
            id BIGINT PRIMARY KEY NOT NULL,
            has_agreed_to_privacy_policy BOOLEAN NOT NULL,
            created_at DATETIME NOT NULL
        );
        """
    )
    conn.commit()
    conn.close()


def test_rejects_database_without_alembic_version_table(database_path):
    create_legacy_database(database_path)
    with pytest.raises(RuntimeError, match="alembic_version"):
        get_recorded_revision(database_path)


def test_rejects_empty_alembic_version_table(database_path):
    create_legacy_database(database_path)
    conn = sqlite3.connect(str(database_path))
    conn.execute(
        "CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"
    )
    conn.commit()
    conn.close()

    with pytest.raises(RuntimeError, match="没有版本记录"):
        get_recorded_revision(database_path)


def test_returns_recorded_revision(database_path):
    create_legacy_database(database_path)
    conn = sqlite3.connect(str(database_path))
    conn.execute(
        "CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"
    )
    conn.execute("INSERT INTO alembic_version VALUES ('5e6f70913e2c')")
    conn.commit()
    conn.close()

    assert get_recorded_revision(database_path) == "5e6f70913e2c"


def test_main_prints_recorded_revision_before_backup(
    database_path, monkeypatch, capsys
):
    create_legacy_database(database_path)
    conn = sqlite3.connect(str(database_path))
    conn.execute(
        "CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"
    )
    conn.execute("INSERT INTO alembic_version VALUES ('5e6f70913e2c')")
    conn.commit()
    conn.close()

    monkeypatch.setattr(migrate, "DB_PATH", database_path)
    monkeypatch.setattr(migrate, "DATA_DIR", database_path.parent)
    monkeypatch.setattr(migrate, "backup_database", lambda *_: None)
    monkeypatch.setattr(
        migrate,
        "run_alembic",
        lambda *args: subprocess.CompletedProcess(
            args=args,
            returncode=0,
            stdout="c84b8e9a2d11\n",
            stderr="",
        ),
    )

    migrate.main()

    output = capsys.readouterr().out
    version_message = "数据库记录的 Alembic 版本: 5e6f70913e2c"
    assert version_message in output
    assert output.index(version_message) < output.index("正在备份数据库")


def test_sqlite_backup_includes_committed_wal_data(database_path):
    backup_path = database_path.with_name(f"{database_path.stem}-backup.db")
    conn = sqlite3.connect(str(database_path))
    try:
        assert conn.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
        conn.execute("CREATE TABLE example (value TEXT NOT NULL)")
        conn.execute("INSERT INTO example VALUES ('from-wal')")
        conn.commit()

        backup_database(database_path, backup_path)

        backup = sqlite3.connect(str(backup_path))
        try:
            assert backup.execute("SELECT value FROM example").fetchall() == [
                ("from-wal",)
            ]
            assert backup.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        finally:
            backup.close()
    finally:
        conn.close()
        backup_path.unlink(missing_ok=True)


def test_upgrade_current_head_preserves_resources_and_users(database_path):
    """验证协作者迁移的升级和回退均保留已有帖子、资源与用户数据。"""
    from contextlib import closing
    from sqlalchemy import create_engine
    from src.models import Base

    # 排除协作者表来构造升级前的数据库结构。
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    try:
        Base.metadata.create_all(engine, tables=[
            table for table in Base.metadata.sorted_tables if table.name != "author_collaborators"
        ])
    finally:
        engine.dispose()
    # 写入旧迁移版本和具有密码、下载计数的资源，保存数据快照供比较。
    with closing(sqlite3.connect(str(database_path))) as conn:
        conn.executescript("""
            CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL PRIMARY KEY);
            INSERT INTO alembic_version VALUES ('e4c6b8d0f2a1');
            INSERT INTO threads (id, public_thread_id, author_id, quick_mode_enabled, created_at)
                VALUES (1, 123, 456, 1, CURRENT_TIMESTAMP);
            INSERT INTO resources (id, thread_id, upload_mode, version_info, filename,
                source_message_id, password, download_count, created_at)
                VALUES (1, 1, 'SECURE', 'v1', 'file.zip', 789, 'secret', 42, CURRENT_TIMESTAMP);
            INSERT INTO users (id, has_agreed_to_privacy_policy, has_agreed_to_wishlist_policy, created_at)
                VALUES (456, 1, 0, CURRENT_TIMESTAMP);
        """)
        before = {name: conn.execute(f"SELECT * FROM {name}").fetchall()
                  for name in ("threads", "resources", "users")}
    # 将迁移命令指向临时数据库，分别执行升级与回退。
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite+aiosqlite:///{database_path.resolve().as_posix()}"
    for action, target in (("upgrade", "head"), ("downgrade", "e4c6b8d0f2a1")):
        result = subprocess.run(
            [sys.executable, "-m", "alembic", action, target],
            cwd=Path(__file__).resolve().parents[1], env=env,
            capture_output=True, text=True, encoding="utf-8",
        )
        assert result.returncode == 0, result.stderr
        # 每次迁移后检查原数据完全保留，授权表仅在升级后存在。
        with closing(sqlite3.connect(str(database_path))) as conn:
            assert {name: conn.execute(f"SELECT * FROM {name}").fetchall()
                    for name in before} == before
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            assert ("author_collaborators" in tables) is (action == "upgrade")


def test_upgrade_adds_thread_source_metadata_to_wishlist_head(database_path):
    conn = sqlite3.connect(str(database_path))
    conn.executescript(
        """
        CREATE TABLE threads (
            id INTEGER NOT NULL PRIMARY KEY,
            public_thread_id BIGINT NOT NULL UNIQUE,
            warehouse_thread_id BIGINT UNIQUE,
            download_panel_message_id BIGINT,
            author_id BIGINT NOT NULL,
            quick_mode_enabled BOOLEAN NOT NULL DEFAULT 0,
            created_at DATETIME
        );
        CREATE INDEX ix_threads_public_thread_id
            ON threads (public_thread_id);
        CREATE TABLE alembic_version (
            version_num VARCHAR(32) NOT NULL PRIMARY KEY
        );
        INSERT INTO alembic_version VALUES ('c84b8e9a2d11');
        INSERT INTO threads (
            id, public_thread_id, author_id,
            quick_mode_enabled, created_at
        ) VALUES (1, 123, 456, 0, CURRENT_TIMESTAMP);
        """
    )
    conn.commit()
    conn.close()

    env = os.environ.copy()
    env["DATABASE_URL"] = (
        f"sqlite+aiosqlite:///{database_path.resolve().as_posix()}"
    )
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert result.returncode == 0, result.stderr

    conn = sqlite3.connect(str(database_path))
    try:
        columns = {
            row[1] for row in conn.execute("PRAGMA table_info(threads)")
        }
        assert {
            "guild_id",
            "public_thread_name",
            "source_status",
        } <= columns
        assert conn.execute(
            "SELECT guild_id, public_thread_name, source_status FROM threads"
        ).fetchone() == (None, None, "unknown")
        assert conn.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone() == ("a9c2e5f8b1d4",)
        assert conn.execute("SELECT author_id FROM threads").fetchone() == (456,)
        columns = {row[1] for row in conn.execute("PRAGMA table_info(author_collaborators)")}
        assert {"author_id", "collaborator_id", "granted_by", "created_at"} <= columns
        conn.execute("INSERT INTO author_collaborators (author_id, collaborator_id, granted_by) VALUES (456, 789, 456)")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO author_collaborators (author_id, collaborator_id, granted_by) VALUES (456, 789, 456)")
    finally:
        conn.close()
