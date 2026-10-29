"""一轮守护：探测 → 状态机 → 到点续码 → 推送飞书。"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

from astrbot.api import logger

from ..entity.constants import QR_FILE_NAME
from ..entity.state import LoginState, ProbeResult
from .feishu import FeishuBot
from .guard import Action, Guard
from .napcat import NapCatProbe
from .qr import QrRenderError, render_qr


def clock_text() -> str:
    """时间戳只给人看，不参与判断。"""
    return time.strftime("%m-%d %H:%M")


def qr_caption(detail: str) -> str:
    """推码前那句说明，写得像人话。"""
    return f"QQ 掉线了（{detail}），用手机 QQ 扫这张码重登，码过期会自动续一张。"


class Monitor:
    """按间隔跑一轮，把所有网络细节留在业务层。"""

    def __init__(
        self,
        probe: NapCatProbe,
        notifier: FeishuBot,
        guard: Guard,
        data_dir: Path,
        interval: int = 30,
    ) -> None:
        self.probe = probe
        self.notifier = notifier
        self.guard = guard
        self.data_dir = Path(data_dir)
        self.interval = max(10, int(interval))
        self._qr_path = self.data_dir / QR_FILE_NAME

    @property
    def ready(self) -> bool:
        """探测和通知两侧都配齐才值得起循环。"""
        return self.probe.configured and self.notifier.ready

    @property
    def missing(self) -> str:
        """启动时直接告诉用户缺哪几个配置项，别让他翻 schema。"""
        gaps = []
        if not self.probe.configured:
            gaps.append("napcat_webui_url / napcat_webui_token")
        if not self.notifier.ready:
            gaps.append("feishu_app_id / feishu_app_secret / feishu_receive_id")
        return "、".join(gaps)

    async def run(self, stop: asyncio.Event) -> None:
        """一直查到插件被禁用；单轮出错只记日志，不让循环退出。"""
        while not stop.is_set():
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # 探测或推送的意外都只影响这一轮
                logger.warning("守护轮次出错：%s", exc)
            try:
                await asyncio.wait_for(stop.wait(), timeout=self.interval)
            except (asyncio.TimeoutError, TimeoutError):
                pass

    async def tick(self) -> None:
        """先探测，到点再续码，最后只发状态机产出的动作。"""
        result = await self.probe.check()
        actions = self.guard.feed(result, time.monotonic())
        # 这一轮没推出码，可能协议端还没出码，到点就主动续一张
        if not actions and self.guard.should_refresh(time.monotonic()):
            result = await self._refresh()
            actions = self.guard.feed(result, time.monotonic())
        await self._run(actions)

    async def _refresh(self) -> ProbeResult:
        """续一张二维码；失败只记日志，下一轮再来。"""
        try:
            url = await self.probe.refresh()
        except Exception as exc:  # 协议端报错、超时都算这一次没续到
            logger.warning("续二维码失败：%s", exc)
            url = ""
        # 成败都记一次，否则连续失败会每个间隔都打一遍协议端
        self.guard.mark_refreshed(time.monotonic())
        if url:
            return ProbeResult(LoginState.NEED_LOGIN, "已续出新的二维码", url)
        return ProbeResult(LoginState.NEED_LOGIN, "续码没拿到二维码内容")

    async def _run(self, actions: list[Action]) -> None:
        """按动作类型分发；文字失败只记日志，二维码失败下一轮重推。"""
        for action in actions:
            if action.kind == "qr":
                await self._push_qr(action)
            elif action.kind == "recover":
                await self._push_text(f"QQ 已恢复登录（{clock_text()}）")
            else:
                await self._push_text(f"QQ 登录异常：{action.detail}（{clock_text()}）")

    async def _push_qr(self, action: Action) -> None:
        """渲染 PNG 再发：先发图，图到了再补一句说明。"""
        try:
            path = render_qr(action.qrcode_url, self._qr_path)
            await self.notifier.send_image(path)
        except QrRenderError as exc:
            # 渲染失败几乎都是依赖没装全，用 error 级别指到具体那一处
            logger.error("二维码渲染失败，检查 qrcode / pillow 是否装好：%s", exc)
            # 清掉去重标记，下一轮还会拿同一张码重试
            self.guard.clear_last_qr()
            return
        except Exception as exc:  # 上传失败说明码没送到，文案也就不发了
            logger.warning("推送二维码失败（%s）：%s", type(exc).__name__, exc)
            # 同样清掉，下一轮立刻重取一张再推
            self.guard.clear_last_qr()
            return
        # 图已经送到了，说明失败只影响文案；清去重只会让用户再收一张重复的码
        await self._push_text(qr_caption(action.detail))

    async def _push_text(self, text: str) -> None:
        """文字通知发一次；失败只记日志，不阻塞后续轮次。"""
        try:
            await self.notifier.send_text(text)
        except Exception as exc:
            logger.warning("飞书发文字失败：%s", exc)
