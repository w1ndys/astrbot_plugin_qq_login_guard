"""二维码渲染用例：缺 qrcode 依赖就跳过，不 import astrbot。"""

import sys
import tempfile
import unittest
from importlib import import_module
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

qr_mod = import_module(ROOT.name + ".business.qr")

# 渲染要 qrcode + pillow，开发机没装时只跳过渲染用例
try:
    import qrcode  # noqa: F401

    HAS_QR = True
except ImportError:
    HAS_QR = False

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


class QrRenderTest(unittest.TestCase):
    """渲染成功要有真 PNG，空内容要明确报错。"""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)

    def test_empty_content_rejected(self) -> None:
        with self.assertRaises(qr_mod.QrRenderError):
            qr_mod.render_qr("", self.base / "empty.png")

    @unittest.skipUnless(HAS_QR, "开发机缺 qrcode 依赖")
    def test_render_writes_png(self) -> None:
        # 目录不存在也要能自己建出来
        path = qr_mod.render_qr("https://txz.qq.com/p?k=test", self.base / "sub" / "qr.png")
        self.assertTrue(path.exists())
        self.assertTrue(path.read_bytes().startswith(PNG_MAGIC))

    @unittest.skipUnless(HAS_QR, "开发机缺 qrcode 依赖")
    def test_render_overwrites(self) -> None:
        path = self.base / "qr.png"
        qr_mod.render_qr("first", path)
        first = path.read_bytes()
        qr_mod.render_qr("second", path)
        self.assertNotEqual(first, path.read_bytes())


if __name__ == "__main__":
    unittest.main()
