"""每次下载链路交互只记录一条不含敏感字段的观察日志。"""

import logging
from contextvars import ContextVar
from functools import wraps
from inspect import signature

from src.dto.download_operation_dto import DownloadOperationDTO
from src.exceptions.rate_limit_exceeded import RateLimitExceeded
from src.qo.rate_limit_qo import RateLimitQo
from src.services.rate_limit_service import rate_limiter_for

logger = logging.getLogger(__name__)


# 保留原因码供检索，同时为观察日志补充中文说明。
_REASON_TRANSLATIONS = {
    "minute_rate_limit": "达到分钟下载次数上限",
    "daily_rate_limit": "达到每日下载次数上限",
    "daily_watch": "每日下载次数超过观察阈值",
    "active_watch": "处于观察期",
}


_current: ContextVar[DownloadOperationDTO | None] = ContextVar(
    "download_operation", default=None
)


def update_operation(**values):
    """更新当前观察上下文中的业务字段。"""
    # 上下文随异步任务隔离，不记录用户输入或交付链接。
    operation = _current.get()
    if operation is not None:
        for key, value in values.items():
            setattr(operation, key, value)


async def require_delivery(bot, user_id: int):
    """申请一次交付额度，被拒绝时抛出业务异常。"""
    # 检查结果留在当前操作上下文，由入口统一打印日志。
    result = await rate_limiter_for(bot).check(RateLimitQo(user_id=user_id))
    update_operation(user_id=user_id, result=result)
    if not result.allowed:
        update_operation(outcome="denied")
        raise RateLimitExceeded(result)
    return result


async def send_rate_limit_message(
    interaction, error: RateLimitExceeded, *, replace_response=False
):
    """根据交互响应状态发送私密限流提示。"""
    # 新建私密响应使用独立提示，已有面板通过私密跟进消息提示。
    if replace_response:
        await interaction.edit_original_response(
            content=error.message,
            embeds=[],
            view=None,
            attachments=[],
        )
    elif interaction.response.is_done():
        await interaction.followup.send(error.message, ephemeral=True)
    else:
        await interaction.response.send_message(error.message, ephemeral=True)


async def send_download_error(
    interaction, content=None, *, embed=None, replace_response=False
):
    """延迟应答后提示错误：公开入口替换私密响应，个人面板保留原消息。"""
    if replace_response:
        await interaction.edit_original_response(
            content=content,
            embeds=[embed] if embed is not None else [],
            view=None,
        )
    else:
        await interaction.followup.send(content=content, embed=embed, ephemeral=True)


def _id(value):
    """仅接受真实整数 ID，避免记录任意对象。"""
    # Discord 身份字段只记录整数值。
    return value if isinstance(value, int) else None


def observe_download(name: str):
    """包装下载链路入口并在操作结束后记录观察结果。"""

    # 单次操作在完成后统一输出，避免各层重复记录。
    def decorate(function):
        """保留回调签名并加入观察日志包装。"""
        # 通过原始签名识别命令、组件和关键词监听的参数。
        parameters = signature(function)

        @wraps(function)
        async def wrapped(*args, **kwargs):
            """执行回调并在结束后记录一次观察日志。"""
            # 只捕获业务元数据，操作异常不写入观察记录。
            bound = parameters.bind(*args, **kwargs).arguments
            owner = bound.get("self")
            interaction = bound.get("interaction", bound.get("message"))
            bot = getattr(interaction, "client", None) or getattr(owner, "bot", None)
            user = getattr(interaction, "user", None) or getattr(
                interaction, "author", None
            )
            user_id = _id(getattr(user, "id", None))
            if bot is None or user_id is None:
                return await function(*args, **kwargs)
            operation = DownloadOperationDTO(
                user_id=user_id,
                operation=name,
                guild_id=_id(getattr(getattr(interaction, "guild", None), "id", None)),
                thread_id=_id(
                    getattr(getattr(interaction, "channel", None), "id", None)
                ),
            )
            token = _current.set(operation)
            try:
                return await function(*args, **kwargs)
            except BaseException:
                if operation.outcome != "denied":
                    operation.outcome = "failed"
                raise
            finally:
                try:
                    result = operation.result
                    if result is None:
                        result = await rate_limiter_for(bot).check(
                            RateLimitQo(user_id=user_id, charge=False)
                        )
                    if result.watched:
                        reasons = ",".join(result.reasons) or "active_watch"
                        reasons_zh = ",".join(
                            _REASON_TRANSLATIONS.get(reason, reason)
                            for reason in (result.reasons or ("active_watch",))
                        )
                        method = logger.warning if result.reasons else logger.info
                        method(
                            "WATCH: user_id=%s guild_id=%s thread_id=%s resource_id=%s "
                            "page=%s operation=%s reason=%s reason_zh=%s "
                            "minute_count=%s/%s "
                            "daily_count=%s/%s retry_after=%ss outcome=%s mode=%s",
                            operation.user_id,
                            operation.guild_id,
                            operation.thread_id,
                            operation.resource_id,
                            operation.page,
                            operation.operation,
                            reasons,
                            reasons_zh,
                            result.minute_count,
                            result.minute_limit,
                            result.daily_count,
                            result.daily_limit,
                            result.reset_after,
                            operation.outcome,
                            result.mode,
                        )
                finally:
                    _current.reset(token)

        return wrapped

    return decorate
