"""登录状态与探测结果的固定形状。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class LoginState(str, Enum):
    """协议端登录状态。"""

    ONLINE = "online"
    NEED_LOGIN = "need_login"
    OFFLINE = "offline"
    # 协议端自报重连中：既不是在线也不是掉线，靠宽限期决定要不要提醒
    RECONNECTING = "reconnecting"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ProbeResult:
    """一次探测的结论。"""

    state: LoginState
    detail: str = ""
    qrcode_url: str = ""
    # 配置错误（如 token 不对）不算掉线，只记日志
    config_error: bool = False


@dataclass
class GuardState:
    """跨轮次保留的状态机内部变量。"""

    state: LoginState = LoginState.UNKNOWN
    streak: int = 0
    notified: bool = False
    last_qr_url: str = ""
    next_refresh_at: float = 0.0
    backoff: int = 0
    # 这一段重连从什么时候开始（秒，用时序单调时钟）；None 表示当前不在重连中
    reconnect_since: float | None = None
    # 这一段重连是否已经报过告警，避免超时后每轮都发
    reconnect_notified: bool = False
