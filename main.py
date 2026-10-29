"""QQ 登录守护：定时看 NapCat 登录态，掉线时续码并推飞书。"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from pathlib import Path

from astrbot.api import logger
from astrbot.api.star import Context, Star, StarTools

from .business.feishu import FeishuBot
from .business.guard import Guard
from .business.monitor import Monitor
from .business.napcat import NapCatProbe
from .entity.constants import PLUGIN_NAME


def cfg_int(config, key: str, fallback: int) -> int:
    """配置可能被手改成字符串，转不动就用默认值，别让插件起不来。"""
    try:
        return int(config.get(key, fallback))
    except (TypeError, ValueError):
        return fallback


class QQLoginGuardPlugin(Star):
    """入口层：只装配对象、起停循环，不直接发请求。"""

    def __init__(self, context: Context, config=None) -> None:
        super().__init__(context)
        self.config = config or {}
        self._task: asyncio.Task | None = None
        self._stop: asyncio.Event | None = None

    async def initialize(self) -> None:
        """插件启用时起后台循环，此时才有事件循环可用。"""
        try:
            monitor = self._build_monitor()
        except Exception as exc:  # 数据目录建不出来等，记下来别让插件激活失败
            logger.error("QQ 登录守护初始化失败：%s", exc)
            return
        # 配置不全只提示一次，不阻断 AstrBot 其它功能
        if not monitor.ready:
            logger.warning("QQ 登录守护未启动，缺少配置：%s", monitor.missing)
            return
        self._stop = asyncio.Event()
        self._task = asyncio.create_task(monitor.run(self._stop))
        logger.info("QQ 登录守护已启动，每 %s 秒探测一次", monitor.interval)

    async def terminate(self) -> None:
        """禁用或重载时先置停止位再等任务结束，避免任务泄漏。"""
        if self._stop is not None:
            self._stop.set()
        if self._task is None:
            return
        self._task.cancel()
        with suppress(asyncio.CancelledError):
            await self._task
        self._task = None

    def _build_monitor(self) -> Monitor:
        """把 WebUI 配置翻译成对象树。"""
        probe = NapCatProbe(
            base_url=self.config.get("napcat_webui_url", ""),
            token=self.config.get("napcat_webui_token", ""),
            timeout=cfg_int(self.config, "probe_timeout", 5),
            qr_timeout=cfg_int(self.config, "qr_timeout", 15),
            onebot_url=self.config.get("napcat_onebot_url", ""),
            onebot_token=self.config.get("napcat_onebot_token", ""),
        )
        notifier = FeishuBot(
            app_id=self.config.get("feishu_app_id", ""),
            app_secret=self.config.get("feishu_app_secret", ""),
            receive_type=self.config.get("feishu_receive_type", "chat_id"),
            receive_id=self.config.get("feishu_receive_id", ""),
        )
        guard = Guard(
            # 新键是 offline_confirm_rounds；旧键 unknown_confirm_rounds 仍读一次，再退默认 2 轮
            confirm_rounds=cfg_int(
                self.config,
                "offline_confirm_rounds",
                cfg_int(self.config, "unknown_confirm_rounds", 2),
            ),
            refresh_seconds=cfg_int(self.config, "qr_refresh_seconds", 120),
            backoff_max=cfg_int(self.config, "qr_push_backoff_max", 900),
            notify_recover=bool(self.config.get("notify_recover", True)),
            # 重连中要持续多久才告警，默认 5 分钟，避开 30 秒探测下的瞬态抖动
            reconnect_grace=cfg_int(self.config, "reconnect_grace_seconds", 300),
        )
        # 二维码图放插件数据目录，重装插件不丢，也不污染源码目录
        data_dir = Path(StarTools.get_data_dir(PLUGIN_NAME))
        interval = cfg_int(self.config, "probe_interval", 30)
        return Monitor(probe, notifier, guard, data_dir, interval)
