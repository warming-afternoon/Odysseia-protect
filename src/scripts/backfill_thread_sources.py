"""从 Discord 回填历史来源帖子的名称与服务器 ID。"""

import asyncio
import os

from dotenv import load_dotenv

load_dotenv()

import discord

from src.database.database import AsyncSessionLocal, engine, init_db
from src.database.repositories.thread import ThreadRepository
from src.enums import SourceStatus


BACKFILL_REQUEST_DELAY_SECONDS = 0.3


async def backfill() -> int:
    token = os.getenv("DISCORD_BOT_TOKEN")
    if not token:
        print("❌ 缺少 DISCORD_BOT_TOKEN，无法访问 Discord。")
        return 1

    await init_db()
    repository = ThreadRepository()
    client = discord.Client(intents=discord.Intents.none())
    updated = 0
    deleted = 0
    forbidden = 0
    failed = 0

    try:
        await client.login(token)
        async with AsyncSessionLocal() as session:
            threads = await repository.get_missing_source_metadata(session)
            total = len(threads)
            print(f"ℹ️  发现 {total} 条缺少来源元数据的帖子记录。")

            for index, thread in enumerate(threads, start=1):
                public_thread_id = thread.public_thread_id
                try:
                    channel = await client.fetch_channel(public_thread_id)
                    guild = getattr(channel, "guild", None)
                    name = getattr(channel, "name", None)
                    if guild is None or not isinstance(name, str) or not name:
                        raise ValueError("Discord 返回的频道缺少服务器或名称信息")

                    await repository.update_source_metadata(
                        session,
                        public_thread_id=public_thread_id,
                        guild_id=guild.id,
                        public_thread_name=name,
                        source_status=SourceStatus.ACTIVE,
                    )
                    await session.commit()
                    updated += 1
                    print(f"[{index}/{total}] ✅ {public_thread_id}: {name}")
                except discord.NotFound:
                    await session.rollback()
                    await repository.update_source_metadata(
                        session,
                        public_thread_id=public_thread_id,
                        source_status=SourceStatus.DELETED,
                    )
                    await session.commit()
                    deleted += 1
                    print(f"[{index}/{total}] ⚠️ {public_thread_id}: 原帖已删除")
                except discord.Forbidden:
                    await session.rollback()
                    forbidden += 1
                    print(f"[{index}/{total}] ⏭️ {public_thread_id}: Bot 无权访问")
                except (discord.HTTPException, ValueError) as exc:
                    await session.rollback()
                    failed += 1
                    print(f"[{index}/{total}] ❌ {public_thread_id}: {exc}")
                except Exception as exc:
                    await session.rollback()
                    failed += 1
                    print(f"[{index}/{total}] ❌ {public_thread_id}: 未知错误: {exc}")

                if index < total:
                    await asyncio.sleep(BACKFILL_REQUEST_DELAY_SECONDS)
    finally:
        await client.close()
        await engine.dispose()

    print(
        "完成："
        f"更新 {updated}，已删除 {deleted}，无权限 {forbidden}，失败 {failed}。"
    )
    return 0 if failed == 0 else 1


def main() -> None:
    raise SystemExit(asyncio.run(backfill()))


if __name__ == "__main__":
    main()
