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


def reconnecting(detail: str = "协议端重连中") -> "ProbeResult":
    """协议端自报重连中，既不在线也不掉线。"""
    return ProbeResult(LoginState.RECONNECTING, detail)


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


class ReconnectingTest(unittest.TestCase):
    """重连中是瞬态：宽限期内静默，卡住超时才报一次。"""

    def test_silent_within_grace(self) -> None:
        guard = Guard(reconnect_grace=300)
        self.assertEqual(guard.feed(reconnecting(), 0.0), [])
        self.assertEqual(guard.feed(reconnecting(), 120.0), [])
        # 抖动不能累积成「连续失败轮数」
        self.assertEqual(guard.state.streak, 0)

    def test_report_once_after_grace(self) -> None:
        guard = Guard(reconnect_grace=300)
        guard.feed(reconnecting(), 1000.0)
        # 差一秒还在宽限期内，不能提前报
        self.assertEqual(guard.feed(reconnecting(), 1299.0), [])
        self.assertEqual([a.kind for a in guard.feed(reconnecting(), 1300.0)], ["offline"])
        # 同一段重连只报一次
        self.assertEqual(guard.feed(reconnecting(), 1900.0), [])

    def test_grace_restarts_after_online(self) -> None:
        """重连断开过再重连要重新计时，不能拿上一段的时间接着算。"""
        guard = Guard(reconnect_grace=300)
        guard.feed(reconnecting(), 0.0)
        guard.feed(online(), 100.0)
        self.assertEqual(guard.feed(reconnecting(), 120.0), [])

    def test_does_not_touch_refresh_clock(self) -> None:
        """重连不该把已经排好的续码时间清掉。"""
        guard = Guard(refresh_seconds=120, reconnect_grace=300)
        guard.feed(need_login("A"), 0.0)
        guard.feed(reconnecting(), 30.0)
        self.assertEqual(guard.state.next_refresh_at, 120.0)

    def test_zero_grace_alerts_immediately(self) -> None:
        """配置成 0 就退化成「第一次重连就报」。"""
        guard = Guard(reconnect_grace=0)
        self.assertEqual([a.kind for a in guard.feed(reconnecting(), 0.0)], ["offline"])

    def test_blip_recover_is_silent(self) -> None:
        """抖一下又好了、期间没提醒过用户，恢复也别单独报平安。"""
        guard = Guard(reconnect_grace=300)
        guard.feed(reconnecting(), 0.0)
        self.assertEqual(guard.feed(online(), 30.0), [])

    def test_recover_after_grace_alert(self) -> None:
        guard = Guard(reconnect_grace=300)
        guard.feed(reconnecting(), 0.0)
        self.assertEqual([a.kind for a in guard.feed(reconnecting(), 400.0)], ["offline"])
        self.assertEqual([a.kind for a in guard.feed(online(), 430.0)], ["recover"])


class UnreachableRecoverTest(unittest.TestCase):
    """不可达报过告警之后恢复，也要有收尾。"""

    def test_unknown_then_online_recover(self) -> None:
        guard = Guard(confirm_rounds=2)
        guard.feed(unknown(), 0.0)
        self.assertEqual([a.kind for a in guard.feed(unknown(), 30.0)], ["offline"])
        self.assertEqual([a.kind for a in guard.feed(online(), 60.0)], ["recover"])

    def test_single_unknown_then_online_silent(self) -> None:
        """只抖了一轮、没报过告警，恢复也不发。"""
        guard = Guard(confirm_rounds=2)
        guard.feed(unknown(), 0.0)
        self.assertEqual(guard.feed(online(), 30.0), [])


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
