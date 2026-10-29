"""状态机用例：验判定、去重和续码时钟，不 import astrbot。"""

import sys
import unittest
from importlib import import_module
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

state_mod = import_module(ROOT.name + ".entity.state")
guard_mod = import_module(ROOT.name + ".business.guard")

LoginState = state_mod.LoginState
ProbeResult = state_mod.ProbeResult
Guard = guard_mod.Guard


def need_login(url: str = "https://txz.qq.com/p?k=1") -> "ProbeResult":
    """协议端在等扫码。"""
    return ProbeResult(LoginState.NEED_LOGIN, "协议端等待扫码", url)


def online() -> "ProbeResult":
    """协议端自报已登录。"""
    return ProbeResult(LoginState.ONLINE, "协议端自报已登录")


def unknown(detail: str = "WebUI 不可达") -> "ProbeResult":
    """连不上或判不出来。"""
    return ProbeResult(LoginState.UNKNOWN, detail)


class FirstPushTest(unittest.TestCase):
    """首次进等扫码只推一次码，别紧接着又续一张。"""

    def test_push_once(self) -> None:
        guard = Guard(refresh_seconds=120)
        actions = guard.feed(need_login(), 1000.0)
        self.assertEqual([a.kind for a in actions], ["qr"])
        self.assertEqual(actions[0].qrcode_url, "https://txz.qq.com/p?k=1")

    def test_clock_moved_after_push(self) -> None:
        guard = Guard(refresh_seconds=120)
        guard.feed(need_login(), 1000.0)
        # 推完码立刻续码会连推两次，所以当期不该续
        self.assertFalse(guard.should_refresh(1000.0))
        self.assertTrue(guard.should_refresh(1120.0))

    def test_same_qr_not_pushed_twice(self) -> None:
        guard = Guard()
        guard.feed(need_login(), 0.0)
        self.assertEqual(guard.feed(need_login(), 30.0), [])

    def test_new_qr_pushed_again(self) -> None:
        guard = Guard()
        guard.feed(need_login("A"), 0.0)
        self.assertEqual([a.kind for a in guard.feed(need_login("B"), 10.0)], ["qr"])

    def test_empty_url_not_pushed(self) -> None:
        guard = Guard()
        self.assertEqual(guard.feed(need_login(""), 0.0), [])


class RefreshBackoffTest(unittest.TestCase):
    """没人扫码时续码间隔逐次翻倍并封顶。"""

    def test_backoff_doubles_and_caps(self) -> None:
        guard = Guard(refresh_seconds=120, backoff_max=900)
        guard.mark_refreshed(0.0)
        self.assertEqual(guard.state.next_refresh_at, 120.0)
        guard.mark_refreshed(120.0)
        self.assertEqual(guard.state.next_refresh_at, 360.0)
        guard.mark_refreshed(360.0)
        self.assertEqual(guard.state.next_refresh_at, 840.0)
        guard.mark_refreshed(840.0)
        self.assertEqual(guard.state.next_refresh_at, 1740.0)
        self.assertEqual(guard.state.backoff, 900)

    def test_push_keeps_reserved_slot(self) -> None:
        """续码后再推新码，不能把排到更晚的续码时间改早。"""
        guard = Guard(refresh_seconds=120, backoff_max=900)
        guard.feed(need_login("A"), 0.0)
        guard.mark_refreshed(120.0)
        guard.feed(need_login("B"), 125.0)
        self.assertEqual(guard.state.next_refresh_at, 240.0)


class AbnormalTest(unittest.TestCase):
    """离线要连续够轮数，且同一段异常只报一次。"""

    def test_need_confirm_rounds(self) -> None:
        guard = Guard(confirm_rounds=2)
        self.assertEqual(guard.feed(unknown(), 0.0), [])
        self.assertEqual([a.kind for a in guard.feed(unknown(), 1.0)], ["offline"])

    def test_report_once(self) -> None:
        guard = Guard(confirm_rounds=1)
        self.assertEqual([a.kind for a in guard.feed(unknown(), 0.0)], ["offline"])
        self.assertEqual(guard.feed(unknown(), 1.0), [])

    def test_config_error_ignored(self) -> None:
        guard = Guard(confirm_rounds=1)
        bad = ProbeResult(LoginState.UNKNOWN, "WebUI token 不正确", config_error=True)
        self.assertEqual(guard.feed(bad, 0.0), [])
        # 配置错不能累积成掉线告警
        self.assertEqual(guard.state.streak, 0)
        self.assertEqual(guard.state.state, LoginState.UNKNOWN)

    def test_offline_stops_refresh(self) -> None:
        guard = Guard(confirm_rounds=1)
        guard.feed(need_login(), 0.0)
        self.assertEqual([a.kind for a in guard.feed(unknown(), 1.0)], ["offline"])
        self.assertFalse(guard.should_refresh(2.0))


class RecoverTest(unittest.TestCase):
    """恢复登录报一次平安，并把去重状态清干净。"""

    def test_recover_notified(self) -> None:
        guard = Guard(notify_recover=True)
        guard.feed(need_login(), 0.0)
        self.assertEqual([a.kind for a in guard.feed(online(), 1.0)], ["recover"])

    def test_recover_silent_when_off(self) -> None:
        guard = Guard(notify_recover=False)
        guard.feed(need_login(), 0.0)
        self.assertEqual(guard.feed(online(), 1.0), [])

    def test_no_recover_when_already_online(self) -> None:
        guard = Guard()
        guard.feed(online(), 0.0)
        self.assertEqual(guard.feed(online(), 1.0), [])

    def test_same_qr_after_recover_pushed(self) -> None:
        guard = Guard()
        guard.feed(need_login("A"), 0.0)
        guard.feed(online(), 1.0)
        self.assertEqual([a.kind for a in guard.feed(need_login("A"), 2.0)], ["qr"])


class RetryTest(unittest.TestCase):
    """推送失败清掉去重标记后，同一张码还能再推一次。"""

    def test_clear_last_qr(self) -> None:
        guard = Guard()
        guard.feed(need_login("A"), 0.0)
        guard.clear_last_qr()
        self.assertEqual([a.kind for a in guard.feed(need_login("A"), 5.0)], ["qr"])

    def test_clear_last_qr_rearms_refresh(self) -> None:
        """推失败后要马上重试，别等退避排到 900 秒后。"""
        guard = Guard()
        guard.feed(need_login("A"), 0.0)
        guard.mark_refreshed(120.0)
        guard.clear_last_qr()
        self.assertTrue(guard.should_refresh(121.0))


if __name__ == "__main__":
    unittest.main()
