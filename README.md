# astrbot_plugin_qq_login_guard

AstrBot 插件。定时看 NapCat 的 QQ 登录态，掉登录时主动续一张二维码并推到飞书，手机直接扫码重登。

不用群、不用群自定义机器人 webhook：群机器人发图要求 `image_key`，而 `image_key` 只能由应用身份调 `im/v1/images` 上传，所以走飞书自建应用的机器人私聊。

## 它做什么

```text
每 probe_interval 秒：
  1. POST {napcat_webui_url}/api/auth/login   {"hash": sha256(token + ".napcat")}
     → JWT（内存缓存，401/403 时重取一次）
  2. POST /api/QQLogin/CheckLoginStatus
     → {isLogin, isOffline, loginPhase, coreReady, qrcodeurl}
  3. 需要扫码且这张码没推过 → 推码；到点（qr_refresh_seconds，失败/无人扫逐次翻倍封顶）→
     POST /api/QQLogin/RefreshQRcode 续一张新码，新码再推一次
  4. qrcodeurl 本地渲染 PNG → 飞书上传 → 私聊发「一句说明 + 一张图」
  5. 按 qrcodeurl 去重，同一张码只推一次；恢复登录后清空
```

判定顺序（`business/napcat.py`）：

| CheckLoginStatus | 判定 | 动作 |
| --- | --- | --- |
| `isLogin=true` | 在线 | 此前在等扫码/报过告警则发一次「已恢复」 |
| `loginPhase=waiting_qrcode` | 等扫码 | 有码推码，没码等下一轮续 |
| `loginPhase=reconnecting` | 重连中 | 宽限期（`reconnect_grace_seconds`）内只记状态不告警；超过才报一次 |
| `isOffline=true` | 掉线 | 连续 `offline_confirm_rounds` 轮后推文字 |
| 其它值 | 未知 | 同上按连不上处理 |
| token 错 / 地址没填 | 配置错 | **只记日志，不告警** |

WebUI 连不上时，若填了 `napcat_onebot_url`，会用 OneBot `get_status` 兜底判在线，少一次误报。

## 配置

都在 AstrBot 的插件配置页（`_conf_schema.json`），带 `secret: true` 的三项存下来也是脱敏的。

| 配置项 | 默认 | 说明 |
| --- | --- | --- |
| `napcat_webui_url` | `http://napcat:6099` | 与 AstrBot 同容器网络时用它 |
| `napcat_webui_token` | 空 | **密钥**。NapCat 服务端 `config/webui.json` 里的 `token` |
| `feishu_app_id` | 空 | 飞书自建应用 App ID |
| `feishu_app_secret` | 空 | **密钥**。飞书应用 App Secret |
| `feishu_receive_type` | `chat_id` | `chat_id` / `email` / `open_id` / `user_id` |
| `feishu_receive_id` | 空 | 接收者 ID，`chat_id` 形如 `oc_xxxxxxxx` |
| `probe_interval` | 30 | 探测间隔（秒），最小 10 |
| `probe_timeout` | 5 | 状态探测超时（秒） |
| `qr_timeout` | 15 | 续码请求超时（秒），**不要小于 12**，NapCat 内部最多等 10 秒 |
| `qr_refresh_seconds` | 120 | 推码后第一次续码的间隔（秒），最小 30 |
| `qr_push_backoff_max` | 900 | 一直没人扫码时续码间隔的上限（秒） |
| `offline_confirm_rounds` | 2 | 判「掉线/不可达」前的连续失败轮数（旧键 `unknown_confirm_rounds` 仍兼容） |
| `reconnect_grace_seconds` | 300 | 重连持续多久才告警一次，填 0 表示第一次重连就报 |
| `notify_recover` | true | 恢复登录时发一条（抖一下就好的重连不发） |
| `napcat_onebot_url` | 空 | 可选兜底，如 `http://napcat:3002` |
| `napcat_onebot_token` | 空 | **密钥**。OneBot HTTP 的 token |

飞书应用需要开通并发布这三个权限：`im:message`、`im:message:send_as_bot`、`im:resource`，且可用范围含接收人。

## 部署

1. 把仓库放进 `AstrBot/data/plugins/`，或用 AstrBot 的插件管理从仓库地址安装。
2. 首次启用会自动装 `requirements.txt` 里的 `aiohttp` / `qrcode` / `pillow`；装完重载一次插件。
3. 配置页填四项必填（NapCat 地址与 token、飞书 app_id / app_secret、接收者），保存后重载插件。
4. 看 AstrBot 日志里有没有 `QQ 登录守护已启动`；配置缺项会直接打出缺哪几个。

二维码临时图落在 `data/plugin_data/astrbot_plugin_qq_login_guard/login_qr.png`，每次覆盖，不用清理。

## 已知限制

- AstrBot 自己挂了就发不出通知，需要外部 uptime 兜底。
- 掉线 → 续码 → 推图这条链路只会在真掉线时触发：账号在线时 `RefreshQRcode` 直接返回 `QQ Is Logined`。
- NapCat 升级后 WebUI 路径或 `loginPhase` 取值可能变；未知取值按「连不上」处理，不会静默。
- 文字通知（掉线/恢复）失败只记日志，状态机不会重发；二维码推送失败会清去重标记，下一轮重推。
- 只守一个 QQ 号。
- NapCat 会话凭据（JWT）缓存在内存里，插件重载后重新换一次。

## 开发

```bash
python3 -m unittest            # 状态机 + 渲染用例，缺 qrcode 时渲染用例自动跳过
```

分层：`entity/`（固定值与数据形状）→ `business/`（探测、状态机、渲染、飞书）→ `main.py`（装配与起停循环）。
入口不直接发请求；`tests/` 只导入 `business/guard.py`、`business/qr.py`，不 import `astrbot`。

## License

MIT
