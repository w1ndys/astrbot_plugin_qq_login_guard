"""状态机：把探测结果翻译成这一轮要不要发通知。"""

from __future__ import annotations

from dataclasses import dataclass

from ..entity.state import GuardState, LoginState, ProbeResult


@dataclass
class Action:
    """状态机产出的一个待执行动作。"""

    kind: str
    detail: str = ""
    qrcode_url: str = ""


class Guard:
    """只在状态跳变时产出动作，避免每轮重复推送。"""

    def __init__(
        self,
        confirm_rounds: int = 2,
        refresh_seconds: int = 120,
        backoff_max: int = 900,
        notify_recover: bool = True,
    ) -> None:
        self.confirm_rounds = max(1, int(confirm_rounds))
        self.refresh_seconds = max(30, int(refresh_seconds))
        self.backoff_max = max(self.refresh_seconds, int(backoff_max))
        self.notify_recover = bool(notify_recover)
        self.state = GuardState()

    def feed(self, result: ProbeResult, now: float) -> list[Action]:
        """吃进一次探测结果，返回本轮要执行的动作。"""
        # 配置错误不参与状态机，否则填错 token 会一直误报掉线
        if result.config_error:
            return []
        if result.state is LoginState.NEED_LOGIN:
            return self._on_need_login(result, now)
        if result.state is LoginState.ONLINE:
            return self._on_online()
        return self._on_bad(result)

    def _on_need_login(self, result: ProbeResult, now: float) -> list[Action]:
        """进入或停留在等扫码状态，二维码变了才推。"""
        self.state.state = LoginState.NEED_LOGIN
        self.state.streak = 0
        self.state.notified = False
        url = result.qrcode_url
        # 没有码就等下一轮的主动续码，不推空消息
        if not url:
            return []
        # 同一张二维码重复推送只会刷屏
        if url == self.state.last_qr_url:
            return []
        self.state.last_qr_url = url
        # 推了新码就把续码时钟从这一刻起算，否则下一轮会立刻再续一张、连推两次
        # 已经排到更晚的时间（退避中）就不动它，免得退避失效
        if self.state.next_refresh_at <= now:
            self.state.next_refresh_at = now + self.refresh_seconds
        return [Action(kind="qr", detail=result.detail or "等待扫码", qrcode_url=url)]

    def _on_online(self) -> list[Action]:
        """恢复在线，清空去重状态并按开关决定是否报平安。"""
        was_offline = self.state.state in (LoginState.NEED_LOGIN, LoginState.OFFLINE)
        self.state.state = LoginState.ONLINE
        self.state.streak = 0
        self.state.notified = False
        self.state.last_qr_url = ""
        self.state.backoff = 0
        # 清掉续码时钟，下次掉线重新从第一张码开始算
        self.state.next_refresh_at = 0.0
        if was_offline and self.notify_recover:
            return [Action(kind="recover", detail="已恢复登录")]
        return []

    def _on_bad(self, result: ProbeResult) -> list[Action]:
        """离线或不可达：连续够轮数、且没通知过才发一次。"""
        self.state.streak += 1
        # 连续轮数不够时先当作抖动，不打扰
        if self.state.streak < self.confirm_rounds:
            return []
        # 同一段异常只报一次，恢复后重置
        if self.state.notified:
            return []
        self.state.state = result.state
        self.state.notified = True
        self.state.backoff = 0
        # 已经不算等扫码了，续码时钟作废，回到等扫码时重新排
        self.state.next_refresh_at = 0.0
        return [Action(kind="offline", detail=result.detail or "探测失败")]

    def should_refresh(self, now: float) -> bool:
        """等扫码时到了该续码的时间点。"""
        return (
            self.state.state is LoginState.NEED_LOGIN
            and now >= self.state.next_refresh_at
        )

    def mark_refreshed(self, now: float) -> None:
        """记一次续码尝试（成败都算），并按退避策略推迟下一次。"""
        # 一直没人扫码时逐次翻倍，避免整夜刷屏
        base = self.state.backoff or self.refresh_seconds
        wait = min(base * 2, self.backoff_max) if self.state.backoff else base
        self.state.backoff = wait
        self.state.next_refresh_at = now + wait

    def clear_last_qr(self) -> None:
        """推送失败时清掉去重标记，下一轮立刻重取一张码再推。"""
        self.state.last_qr_url = ""
        self.state.next_refresh_at = 0.0
