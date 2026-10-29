"""NapCat 探测：WebUI 登录态与续码，WebUI 不可达时用 OneBot 兜底。"""

from __future__ import annotations

import hashlib
import time

import aiohttp

from ..entity.state import LoginState, ProbeResult

AUTH_PATH = "/api/auth/login"
STATUS_PATH = "/api/QQLogin/CheckLoginStatus"
REFRESH_PATH = "/api/QQLogin/RefreshQRcode"
# NapCat 口令盐，官方文档没写，只能从源码拿到
TOKEN_SALT = ".napcat"


class NapCatError(Exception):
    """调用协议端失败。"""


class NapCatAuthError(NapCatError):
    """WebUI token 不正确。"""


class NapCatProbe:
    """一次探测只发一到两个请求，不持有长连接。"""

    def __init__(
        self,
        base_url: str,
        token: str,
        timeout: int = 5,
        qr_timeout: int = 15,
        onebot_url: str = "",
        onebot_token: str = "",
    ) -> None:
        self.base_url = str(base_url or "").rstrip("/")
        self.token = str(token or "").strip()
        self.timeout = max(3, int(timeout))
        # 续码时 NapCat 内部最多等 10 秒，超时给小了会自己打断
        self.qr_timeout = max(12, int(qr_timeout))
        self.onebot_url = str(onebot_url or "").rstrip("/")
        self.onebot_token = str(onebot_token or "")
        self._credential = ""
        self._credential_at = 0.0

    @property
    def configured(self) -> bool:
        """地址和口令都在才值得发请求。"""
        return bool(self.base_url and self.token)

    async def check(self) -> ProbeResult:
        """查一次登录态，各类失败都翻译成 ProbeResult。"""
        # 没填地址是配置问题，当成不可达会误报掉线
        if not self.configured:
            return ProbeResult(LoginState.UNKNOWN, "未配置 WebUI 地址或口令", config_error=True)
        try:
            data = await self._post(STATUS_PATH, {}, self.timeout)
        except NapCatAuthError:
            return ProbeResult(LoginState.UNKNOWN, "WebUI token 不正确", config_error=True)
        except Exception as exc:  # 网络类异常统一走兜底
            return await self._fallback(exc)
        return self._judge(data)

    async def refresh(self) -> str:
        """让协议端重新出一张码，返回二维码内容。"""
        data = await self._post(REFRESH_PATH, {}, self.qr_timeout)
        return str(data.get("qrcodeurl") or "")

    def _judge(self, data: dict) -> ProbeResult:
        """按协议端自报字段判定状态。"""
        if data.get("isLogin") is True:
            return ProbeResult(LoginState.ONLINE, "协议端自报已登录")
        phase = str(data.get("loginPhase") or "")
        url = str(data.get("qrcodeurl") or "")
        if phase == "waiting_qrcode":
            return ProbeResult(LoginState.NEED_LOGIN, "协议端等待扫码", url)
        # 重连中既不是在线也不是掉线，不下结论
        if phase == "reconnecting":
            return ProbeResult(LoginState.UNKNOWN, "协议端重连中")
        if data.get("isOffline") is True:
            err = str(data.get("loginError") or "")
            return ProbeResult(LoginState.OFFLINE, "协议端自报离线" + ("：" + err if err else ""))
        return ProbeResult(LoginState.UNKNOWN, f"状态无法判定（phase={phase or 'unknown'}）")

    async def _post(self, path: str, body: dict, timeout: int) -> dict:
        """带 JWT 的 POST；401/403 时换一次 JWT 重试。"""
        if not self.base_url:
            raise NapCatError("未配置 WebUI 地址")
        cred = await self._login()
        for _ in range(2):
            payload, status = await self._request(path, body, timeout, cred)
            if status in (401, 403):
                cred = await self._login(force=True)
                continue
            if payload.get("code") not in (0, None):
                raise NapCatError(str(payload.get("message") or payload.get("msg") or "接口返回异常"))
            return payload.get("data") or {}
        raise NapCatAuthError("WebUI token 不正确")

    async def _request(self, path: str, body: dict, timeout: int, cred: str) -> tuple[dict, int]:
        """发一个请求，返回响应体和 HTTP 状态码。"""
        timeout_obj = aiohttp.ClientTimeout(total=timeout)
        headers = {"Authorization": "Bearer " + cred}
        async with aiohttp.ClientSession(timeout=timeout_obj) as session:
            async with session.post(self.base_url + path, json=body, headers=headers) as resp:
                return await resp.json(content_type=None), resp.status

    async def _login(self, force: bool = False) -> str:
        """用 webui token 换 JWT，缓存半小时。"""
        if not force and self._credential and time.time() - self._credential_at < 1800:
            return self._credential
        digest = hashlib.sha256((self.token + TOKEN_SALT).encode()).hexdigest()
        timeout_obj = aiohttp.ClientTimeout(total=self.timeout)
        async with aiohttp.ClientSession(timeout=timeout_obj) as session:
            async with session.post(self.base_url + AUTH_PATH, json={"hash": digest}) as resp:
                payload = await resp.json(content_type=None)
        if payload.get("code") != 0:
            raise NapCatAuthError("WebUI 登录失败")
        self._credential = str((payload.get("data") or {}).get("Credential") or "")
        self._credential_at = time.time()
        return self._credential

    async def _fallback(self, exc: Exception) -> ProbeResult:
        """WebUI 挂了时用 OneBot get_status 判断在线，少报一次误警。"""
        if not self.onebot_url:
            return ProbeResult(LoginState.UNKNOWN, "WebUI 不可达：" + type(exc).__name__)
        try:
            data = await self._onebot_status()
        except Exception as exc2:
            detail = f"WebUI 与 OneBot 都不可达（{type(exc).__name__}/{type(exc2).__name__}）"
            return ProbeResult(LoginState.UNKNOWN, detail)
        if data.get("online") is True:
            return ProbeResult(LoginState.ONLINE, "WebUI 不可达，OneBot 自报在线")
        return ProbeResult(LoginState.UNKNOWN, "WebUI 不可达，OneBot 自报不在线")

    async def _onebot_status(self) -> dict:
        """调 OneBot HTTP 的 get_status。"""
        timeout_obj = aiohttp.ClientTimeout(total=self.timeout)
        headers = {"Authorization": "Bearer " + self.onebot_token} if self.onebot_token else {}
        async with aiohttp.ClientSession(timeout=timeout_obj) as session:
            async with session.post(self.onebot_url + "/get_status", json={}, headers=headers) as resp:
                payload = await resp.json(content_type=None)
        return payload.get("data") or {}
