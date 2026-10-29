"""登录状态与探测结果的固定形状。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class LoginState(str, Enum):
    """协议端登录状态。"""

    ONLINE = "online"
    NEED_LOGIN = "need_login"
    OFFLINE = "offline"
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
