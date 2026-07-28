import shutil
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


def main():
    """执行完整的数据库迁移流程"""
    print_info("=" * 50)
    print_info("=  数据库自动迁移脚本启动")
    print_info("=" * 50)

    # 备份数据库
    print_info(f"正在备份数据库 '{DB_PATH.name}'...")
    if not DB_PATH.exists():
        print_error(f"错误：数据库文件未找到于 '{DB_PATH}'。请确保文件存在。")
        sys.exit(1)

    try:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_filename = f"{DB_PATH.stem}.backup_{timestamp}{DB_PATH.suffix}"
        backup_path = DATA_DIR / backup_filename

        shutil.copy2(DB_PATH, backup_path)
        print_success(f"数据库已成功备份到: '{backup_path}'")
    except Exception as e:
        print_error(f"备份数据库时发生错误: {e}")
        sys.exit(1)

    # 运行 Alembic 迁移
    print_info("准备执行 Alembic 数据库迁移...")
    print_warning("这将更新数据库结构。请勿中断此过程。")

    try:
        command = ["alembic", "upgrade", "head"]
        result = subprocess.run(
            command, check=True, capture_output=True, text=True, encoding="utf-8"
        )

        print("--- Alembic 输出开始 ---")
        print(result.stdout)
        print("--- Alembic 输出结束 ---")

        print_success("数据库迁移成功完成！")

        current_result = subprocess.run(
            ["alembic", "current"], check=True, capture_output=True, text=True, encoding="utf-8"
        )
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
