"""把二维码内容渲染成 PNG 文件。"""

from __future__ import annotations

from pathlib import Path


class QrRenderError(Exception):
    """二维码渲染失败，多半是缺依赖或内容为空。"""


def render_qr(content: str, path: Path) -> Path:
    """渲染二维码到指定路径并返回该路径。"""
    # 内容为空说明协议端还没出码，直接失败比存一张空白图好查
    if not content:
        raise QrRenderError("二维码内容为空")
    # 延迟导入，缺依赖时只影响这一步，不影响探测和文字通知
    # 出 PNG 还要 pillow，qrcode.make 时才报缺，所以一起兜
    try:
        import qrcode

        image = qrcode.make(content)
    except ImportError as exc:
        raise QrRenderError("缺少 qrcode / pillow 依赖，请重装插件依赖") from exc
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(str(path))
    return path
