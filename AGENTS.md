# astrbot_plugin_qq_login_guard

AstrBot 插件。QQ 掉登录时自动续二维码并推飞书。用中文交流。

## 编码契约

本仓库按「人话编码契约」（human-coding-contract）开发，默认生效，不需要每次再喊触发词。
该 skill 不随仓库分发，走全局 Skill，不在仓内放 `.agents/` 与 `.ohmyagent/` 副本。

- 缺背景就停，问用户。不要猜，不要发明协议字段或页面结构。
- 设计顺序：实体 → 业务 → 入口。一刀一个逻辑目标。
- 写完本刀、跑检查、**不要提交**。停一次，同一条消息给拟议提交、审阅路径（函数粒度，按层）、本次完成、检查、全部进度、当前下一步。不要在对话里贴完整 git diff。用户回「继续」才提交刚审过的改动。
- 等审时不要偷做下一刀。总体做完就停。
- 入口层只调业务层，不要在 `main.py` 里直接发 HTTP。
- 函数（不含空行和注释）≤ 50 行。普通语法，不要多余抽象 / DI / utils 藏逻辑。
- 每个文件、每个函数、每个 `if` 都要有短中文注释（判断目的和后果）。
- 提交前跑 `python3 -m unittest`。本仓还没有 ruff 配置时，不要假装跑过 ruff。
- 密钥、token、密码不得进代码、注释、日志、diff 展示和提交信息。
- 未经用户明确允许不要 push。

## 分层

| 层 | 目录 | 职责 |
| --- | --- | --- |
| 实体 | `entity/` | 插件名、状态枚举、探测结果与状态机变量 |
| 业务 | `business/` | NapCat 探测与续码、状态机、二维码渲染、飞书投递、单轮编排 |
| 入口 | `main.py` | 读配置装配对象，`initialize()` 起循环、`terminate()` 停 |

依赖单向，不反向引用：`main.py` → `business/` → `entity/`。

## 本项目的不变量（改之前先读）

- 插件目录被 AstrBot 以 `data.plugins.<name>.main` 形式导入，**包内一律相对导入**（`from .business...`、`from ..entity...`）。绝对导入 `from entity...` 在真机上会 ImportError，本地测试却看不出来。
- WebUI 口令不是直接发 token：`sha256(token + ".napcat")` 换 JWT，后续请求 `Authorization: Bearer <JWT>`。
- **403（连得上、token 错）≠ 连不上**。前者是配置问题，只记日志；后者才可能告警。
- `RefreshQRcode` 内部最多等 10 秒，客户端超时不要小于 12 秒，否则会自己打断续码。
- 推了新码就要把续码时钟从这一刻起算，否则下一轮会立刻再续一张、连推两次。
- 续码请求失败要吞掉只记日志，并且照样记一次续码时间，否则每个间隔都会打一遍协议端。
- 续码失败不能用 `except` 外的异常把循环带崩；`Monitor.run` 只让单轮失败。
- `tests/` 不能 import `astrbot`，否则本地 `python3 -m unittest` 跑不起来。

## 测试

```bash
python3 -m unittest
```

覆盖状态机（去重、确认轮数、退避、恢复）与二维码渲染；开发机缺 `qrcode` 时渲染用例自动跳过。
