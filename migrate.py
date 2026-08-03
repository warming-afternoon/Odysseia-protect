import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path

# --- 配置 ---
PROJECT_ROOT = Path(__file__).parent.resolve()
DATA_DIR = PROJECT_ROOT / "data"
DB_PATH = DATA_DIR / "bot_database.db"
# --- 结束配置 ---

def print_color(text, color_code):
    """在终端打印彩色文本"""
    print(f"\033[{color_code}m{text}\033[0m")


def print_info(message):
    print_color(f"ℹ️  {message}", "94")  # Blue


def print_success(message):
    print_color(f"✅ {message}", "92")  # Green


def print_warning(message):
    print_color(f"⚠️  {message}", "93")  # Yellow


def print_error(message):
    print_color(f"❌ {message}", "91")  # Red


def _table_names(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'"
    ).fetchall()
    return {str(row[0]) for row in rows}


def get_recorded_revision(db_path: Path) -> str:
    """读取版本记录"""
    conn = sqlite3.connect(str(db_path))
    try:
        tables = _table_names(conn)
        if "alembic_version" not in tables:
            raise RuntimeError("数据库没有 alembic_version 表。")

        rows = conn.execute("SELECT version_num FROM alembic_version").fetchall()
        revisions = [str(row[0]).strip() for row in rows if str(row[0]).strip()]
        if not revisions:
            raise RuntimeError("alembic_version 没有版本记录。")
        if len(revisions) != 1:
            raise RuntimeError(
                f"alembic_version 应当只有一个版本记录，实际为 {revisions}。"
            )
        return revisions[0]
    finally:
        conn.close()


def backup_database(source_path: Path, backup_path: Path) -> None:
    """创建事务一致的 SQLite 备份，包括 WAL 中已提交的数据。"""
    source = sqlite3.connect(str(source_path))
    destination = sqlite3.connect(str(backup_path))
    try:
        source.backup(destination)
        integrity = destination.execute("PRAGMA integrity_check").fetchone()
        if integrity is None or integrity[0] != "ok":
            raise RuntimeError(
                f"备份数据库完整性检查失败: {integrity[0] if integrity else '无结果'}"
            )
    finally:
        destination.close()
        source.close()


def run_alembic(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def main():
    """执行完整的数据库迁移流程"""
    print_info("=" * 50)
    print_info("=  数据库自动迁移脚本启动")
    print_info("=" * 50)

    if not DB_PATH.exists():
        print_error(f"错误：数据库文件未找到于 '{DB_PATH}'。请确保文件存在。")
        sys.exit(1)

    try:
        recorded_revision = get_recorded_revision(DB_PATH)
        print_info(f"数据库记录的 Alembic 版本: {recorded_revision}")
    except Exception as e:
        print_error(f"迁移已中止: {e}")
        print_warning(
            "请人工核对数据库对应的 revision，再执行 "
            "`python -m alembic stamp <revision>`；脚本不会根据表结构猜测版本。"
        )
        sys.exit(1)

    # 备份数据库
    print_info(f"正在备份数据库 '{DB_PATH.name}'...")
    try:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_filename = f"{DB_PATH.stem}.backup_{timestamp}{DB_PATH.suffix}"
        backup_path = DATA_DIR / backup_filename

        backup_database(DB_PATH, backup_path)
        print_success(f"数据库已成功备份到: '{backup_path}'")
    except Exception as e:
        print_error(f"备份数据库时发生错误: {e}")
        sys.exit(1)

    # 运行 Alembic 迁移
    print_info("准备执行 Alembic 数据库迁移...")
    print_warning("这将更新数据库结构。请勿中断此过程。")

    try:
        result = run_alembic("upgrade", "head")

        print("--- Alembic 输出开始 ---")
        print(result.stdout)
        print("--- Alembic 输出结束 ---")

        print_success("数据库迁移成功完成！")

        current_result = run_alembic("current")
        print_info(f"当前数据库版本: {current_result.stdout.strip()}")
    except FileNotFoundError:
        print_error("错误：'alembic' 命令未找到。")
        print_error("请确保 Alembic 已通过 uv 安装在项目依赖中。")
        sys.exit(1)
    except subprocess.CalledProcessError as e:
        print_error("Alembic 迁移过程中发生错误！")
        print_error("--- Alembic 错误输出开始 ---")
        print(e.stderr)
        print_error("--- Alembic 错误输出结束 ---")
        print_warning("数据库结构可能处于不一致状态。建议使用备份文件进行恢复。")
        sys.exit(1)
    except Exception as e:
        print_error(f"执行迁移时发生未知错误: {e}")
        sys.exit(1)

    try:
        conn = sqlite3.connect(str(DB_PATH))
        conn.execute("VACUUM;")
        conn.close()
    except Exception as e:
        print_error(f"执行 VACUUM 时发生错误: {e}")
        print_warning("数据库结构已更新，但优化步骤失败。机器人仍可正常运行。")

    print_info("=" * 50)
    print_success(" 所有操作已成功完成！现在可以启动机器人了。")
    print_info("=" * 50)


if __name__ == "__main__":
    main()
