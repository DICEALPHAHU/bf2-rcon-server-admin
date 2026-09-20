# 接入 QQ 机器人（UniBot / NoneBot2）

把 BF2 服务端接进聊天机器人：群里能查状态、玩家、坐标、地图、封禁列表，
有人进服 / 退服 / 被 ban 时主动推送到群。

本文以 [Minecraft UniBot](https://github.com/MineJPGcraft/UniBot)（NoneBot2）为例，
但链路是通用的 —— 任何能发 HTTP 的机器人都能用同样的接口。

---

## 链路

```
QQ 群 ──> UniBot ──HTTP──> bf2http.py ──RCON:4711──> bf2_w32ded.exe
                    ▲                        │
                    │                        └─ bf2events.py（事件队列）
                    └── 长轮询 /events/wait
```

三个进程各自的位置：

| 进程 | 跑在哪 | 为什么 |
|---|---|---|
| `bf2_w32ded.exe` | 游戏服务端 | 内嵌 Python 2.3.4，**没有可用网络能力**，只能把事件写进文件队列 |
| `bf2http.py` | 和游戏服务端同机 | 普通 Python 3，负责 RCON ↔ HTTP 转换 |
| 机器人 | 和游戏服务端同机（推荐） | 只跟桥接讲话，不懂 RCON 协议 |

**为什么不让服务端直接推送**：BF2 内嵌的 Python 2.3.4 不能起线程、不能阻塞，
`socket` 虽然存在但一崩就会带走整个游戏进程。所以服务端只追加事件到
`bf2events.queue`，由外部进程读。轮询长连接在观感上等同于推送。

---

## 一、服务端侧

把两个模块放进服务端的 `Admin\standard_admin\`：

```
bf2server/bf2admin.py    bf2* 命令集 + ban/unban 事件上报
bf2server/bf2events.py   进服/退服/出生/击杀/回合事件队列
```

`standard_admin\__init__.py` 里加载（见本仓库 `docs/QUICKSTART.md`）。
装好后重启服务端，用 `bf2check` 确认：

```
rcon bf2check
```

**注意**：`bf2admin.py` 里的 `bf2banrecord` 命令和 ban 事件上报是后加的，
必须**重启游戏服务端**才会生效（BF2 不支持热重载 Python 模块）。

---

## 二、启动桥接

```bash
python tools/bf2http.py --port 8099 --token 你的令牌 --rcon-pass 你的RCON密码
```

* 默认绑 `127.0.0.1`。要和机器人分机部署时才用 `--host 0.0.0.0`，
  且**必须**同时给 `--token`，否则桥接会拒绝启动。
* 不建议加 `--allow-dangerous`：那会开放 `bf2kick` / `bf2ban` / `pmveh`
  给任何能访问桥接的人。群里的踢人/封人指令应当走机器人侧的权限校验，
  或者干脆不开放（本方案默认不开放）。

验证：

```bash
curl "http://127.0.0.1:8099/healthz"
curl "http://127.0.0.1:8099/status?token=你的令牌"
```

### 接口一览

| 接口 | 用途 |
|---|---|
| `GET /status` | 地图 + 模式 + 人数 + 玩家摘要 |
| `GET /players` | 玩家列表（含 IP 与 CDKey） |
| `GET /map` | 地图名 / 模式 / 规模 |
| `GET /player?name=X` | 单人详情，**坐标已解析成 {x,y,z}** |
| `GET /bans` | 封禁列表（引擎的 IP/Key + 自己的记录） |
| `GET /events` | 取走事件队列（drain） |
| `GET /events?mode=peek` | 看队列但不取走 |
| `GET /events/wait?timeout=25` | **长轮询**：有事件立刻返回，否则挂起到超时 |
| `GET /join` | 记住当前名单，返回自上次调用以来的**新进**玩家 |
| `GET /leave` | 返回自上次调用以来**离开**的玩家 |
| `GET /healthz` | 探活，不产生 RCON 流量 |

所有响应都是 JSON，令牌可放 `?token=` 或 `X-Token` 头。

`/join` 与 `/leave` 的存在理由：BF2 的 `PlayerDisconnect` 在玩家对象消失后
拿不到可信的名字，而按 5 秒间隔轮询玩家列表会漏掉"进来又马上退出"的人。
所以桥接自己记住了上一次的名单，用差集回答"谁走了"。

---

## 三、机器人侧（UniBot）

### 装扩展

把 `integrations/unibot/Bf2.py` 复制到 UniBot 的 `Extensions/` 目录：

```
UniBot/
  Extensions/Bf2.py          <- 复制到这里（文件名必须叫 Bf2.py，id 与文件名要一致）
  Config/Extensions/Bf2.toml <- 复制这里，或首次加载时自动生成
```

在 `Config/Extensions.toml` 里启用：

```toml
[Bf2]
enabled = true
```

### 配置

`Config/Extensions/Bf2.toml`：

```toml
bridge_url = "http://127.0.0.1:8099"
bridge_token = "你的令牌"      # 必须和 --token 一致

notify_enabled = true
notify_groups = []            # 留空则推到 Config.toml 的 message_groups
notify_join = true
notify_leave = true
notify_ban = true
poll_interval = 5.0
server_name = ""              # 推送开头的服务器名，留空只显示 【BF2】
max_lines = 20
```

也可以在 UniBot 的 WebUI 扩展页面里改，会自动生成表单。

### 指令

| 指令 | 作用 |
|---|---|
| `/bf2` 或 `/bf2 status` | 地图、模式、人数、在线名单 |
| `/bf2 players` | 在线玩家 + IP |
| `/bf2 pos <玩家名>` | 该玩家坐标、载具、是否已出生 |
| `/bf2 map` | 当前地图信息 |
| `/bf2 bans` | 封禁列表 |

别名 `/战地2`。指令只在 `Config.toml` 的 `command_groups` 列出的群里响应。

### 推送效果

```
【BF2】
✅ defaultPlayer 进入了服务器
❌ defaultPlayer 离开了服务器
🔨 cheater 被管理员封禁 时长 25 分钟，原因：aimbot
🔓 cheater 已被解封
```

---

## 四、已知限制

1. **`bf2banrecord` 与 ban 事件需要重启服务端**才生效，见上文。
2. **被 bot 之外的手段封禁**（游戏内控制台直接敲 `admin.addAddressToBanList`）
   不会有事件。`/bans` 接口能看到，但不会被主动推送。
3. **`/events/wait` 是 drain 语义**：读到即出队。扩展在播报成功后才推进本地
   检查点；如果推送失败，那一批事件无法重放（已出队）。这是为了让"服务端
   不用维护订阅状态"付出的代价。
4. **`/join` `/leave` 的名单记忆在桥接进程内存里**，桥接重启会丢。首轮
   `/join` 会把当前在场玩家当成"新进"报一次。
5. **`pmveh` 会崩服务端**，所以桥接的 `DANGEROUS_COMMANDS` 里它被标注了警告，
   默认不可达。
6. UniBot 的 `.venv` 是 uv 创建的，`python.exe` 是指向 uv 托管解释器的
   trampoline；如果那台机器的 uv Python 被清掉，机器人会起不来，
   需要 `uv sync` 重建。

---

## 五、无机器人时的最小验证

不用机器人也能验收整条链路：

```bash
# 1. 桥接活着？
curl "http://127.0.0.1:8099/healthz"

# 2. 服务端信息
curl "http://127.0.0.1:8099/status?token=T"

# 3. 某个玩家的坐标
curl "http://127.0.0.1:8099/player?name=defaultPlayer&token=T"

# 4. 进一次服，然后看事件（另开一个窗口）
curl "http://127.0.0.1:8099/events/wait?timeout=30&token=T"

# 5. 谁进谁出（连续两次调用对比）
curl "http://127.0.0.1:8099/join?token=T"
curl "http://127.0.0.1:8099/leave?token=T"
```
