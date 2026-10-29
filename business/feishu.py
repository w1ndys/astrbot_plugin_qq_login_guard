"""飞书应用机器人：换 tenant token、上传图片、发消息。"""

from __future__ import annotations

import json
import time
from pathlib import Path

import aiohttp

API_BASE = "https://open.feishu.cn/open-apis"
# token 有效期 7200 秒，提前两分钟换，免得刚换到手就过期
TOKEN_MARGIN = 120


class FeishuError(Exception):
    """飞书接口返回错误或网络失败。"""


class FeishuBot:
    """一次只发一两张图，不持有长连接。"""

    def __init__(
        self,
        app_id: str,
        app_secret: str,
        receive_type: str = "chat_id",
        receive_id: str = "",
        timeout: int = 20,
    ) -> None:
        self.app_id = str(app_id or "").strip()
        self.app_secret = str(app_secret or "").strip()
        self.receive_type = str(receive_type or "chat_id").strip()
        self.receive_id = str(receive_id or "").strip()
        self.timeout = max(5, int(timeout))
        self._token = ""
        self._token_deadline = 0.0

    @property
    def ready(self) -> bool:
        """三样缺一样都发不出去，先挡住，免得每轮打一次 400。"""
        return bool(self.app_id and self.app_secret and self.receive_id)

    async def send_text(self, text: str) -> None:
        """发一条纯文本。"""
        await self._send("text", {"text": str(text)})

    async def send_image(self, path: Path) -> None:
        """先上传拿 image_key，再发图片消息。"""
        data = await self._post(f"{API_BASE}/im/v1/images", image=Path(path))
        image_key = str(data.get("image_key") or "")
        # 没拿到 key 就发不出去，早点报错比发一张空白好
        if not image_key:
            raise FeishuError("上传图片没返回 image_key")
        await self._send("image", {"image_key": image_key})

    async def _send(self, msg_type: str, content: dict) -> None:
        """按配置的接收者类型发一条消息。"""
        body = {
            "receive_id": self.receive_id,
            "msg_type": msg_type,
            "content": json.dumps(content, ensure_ascii=False),
        }
        await self._post(
            f"{API_BASE}/im/v1/messages",
            params={"receive_id_type": self.receive_type},
            json_body=body,
        )

    async def _post(
        self,
        url: str,
        params: dict | None = None,
        json_body: dict | None = None,
        image: Path | None = None,
    ) -> dict:
        """统一带 token 发 POST，401/403 时换一次 token 重试。"""
        for attempt in range(2):
            token = await self._token_value(force=attempt > 0)
            status, payload = await self._request(url, token, params, json_body, image)
            # 鉴权类失败多半是 token 刚失效，重取一次再试，第二次还错就抛
            if status in (401, 403) and attempt == 0:
                continue
            code = payload.get("code")
            # code 非 0 表示飞书明确拒绝，把 msg 带出去好查
            if code not in (0, None):
                raise FeishuError(f"飞书接口报错 code={code} msg={payload.get('msg')}")
            return payload.get("data") or {}
        raise FeishuError("飞书鉴权失败，检查 app_id / app_secret")

    async def _request(
        self,
        url: str,
        token: str,
        params: dict | None,
        json_body: dict | None,
        image: Path | None,
    ) -> tuple[int, dict]:
        """发一个请求，返回状态码和响应体。"""
        timeout = aiohttp.ClientTimeout(total=self.timeout)
        headers = {"Authorization": "Bearer " + token} if token else {}
        # 上传图片时每次新建 FormData，重试才不会撞上已消费的表单
        form = None
        if image is not None:
            form = aiohttp.FormData()
            form.add_field("image_type", "message")
            form.add_field(
                "image",
                image.read_bytes(),
                filename=image.name,
                content_type="image/png",
            )
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                url, params=params, json=json_body, data=form, headers=headers
            ) as resp:
                status = resp.status
                try:
                    payload = await resp.json(content_type=None)
                except Exception:  # 网关报错时返回的是 HTML，别让它炸在这里
                    payload = {"code": -1, "msg": (await resp.text())[:200]}
        return status, payload if isinstance(payload, dict) else {"code": -1}

    async def _token_value(self, force: bool = False) -> str:
        """换 tenant_access_token 并缓存到过期前两分钟。"""
        if not force and self._token and time.time() < self._token_deadline:
            return self._token
        body = {"app_id": self.app_id, "app_secret": self.app_secret}
        _, payload = await self._request(
            f"{API_BASE}/auth/v3/tenant_access_token/internal", "", None, body, None
        )
        token = str(payload.get("tenant_access_token") or "")
        # 拿不到 token 后面全白搭，直接抛错让上层记日志
        if payload.get("code") != 0 or not token:
            raise FeishuError(f"获取 tenant_access_token 失败：{payload.get('msg')}")
        expire = int(payload.get("expire") or 0)
        self._token = token
        self._token_deadline = time.time() + max(60, expire - TOKEN_MARGIN)
        return token
