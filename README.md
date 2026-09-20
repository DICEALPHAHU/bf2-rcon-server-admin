# bf2-rcon-server-admin

Battlefield 2 专用服务端的 **RCON 管理工具集**：玩家查询、踢人、封禁/解封、
地图信息、事件推送，以及给 QQ 群机器人用的 HTTP 接口。

**每一条命令都在真实服务端 + 真实玩家上验证过**，实测输出记录在 `docs/` 里。

> MIT 许可。随便用、随便改、随便闭源，只需保留版权声明。

---

## 这套东西解决什么

BF2 专用服务端（`bf2_w32ded.exe`）自带的管理能力很弱：踢人/封禁只有引擎命令，
没有"按昵称"这一层，也没有任何对外接口。这个项目补上这几块：

| 你需要 | 这个仓库提供 |
|---|---|
| 知道谁在线、他们的 IP 和 CD-key | `bf2player` |
| 查某人在哪 | `bf2pos` |
| 踢人 / 封人 / 解封 | `bf2kick` / `bf2ban` / `bf2unban` |
| 看当前地图模式人数 | `bf2nowmap` |
| 不用进游戏就能敲命令 | `tools/bf2rcon.py` |
| QQ 群里查服务器、收推送 | `tools/bf2http.py` + `bf2events` 模组 |

---

## 结构

```
bf2server/     放到服务端 Admin\standard_admin\ 的 Python 模组
  bf2admin.py    bf2* 命令集（玩家/踢/封/解封/地图）
  bf2events.py   事件钩子（进服/退服/出生/击杀/回合），供机器人推送
  pmadmin.py     pm* 命令集（诊断类，可选）
  evalpy.py      通过 RCON 执行任意 Python（调试用，可选）
tools/         跑在服务端同一台机器上的外部工具
  bf2rcon.py       独立的 RCON 客户端 —— 不进游戏就能敲命令
  bf2http.py       HTTP → RCON 桥接，给聊天机器人调用
  bf2test.py       批量命令
  crashprobe.py    崩溃隔离实验框架
  mockplayer_test.py  无真人时测试玩家相关代码路径
docs/
  QUICKSTART.md    命令怎么敲、日常用法（先看这个）
  BF2-COMMANDS.md  bf2* 命令详解 + 实测输出 + 重要发现
  USAGE.md         完整文档：验证记录、崩溃实验、技术约束
```

---

## 快速开始

### 1. 装服务端模组

把 `bf2server/*.py` 放进 `<服务端>\Admin\standard_admin\`，
在同目录 `__init__.py` 里接线：

```python
import bf2admin
bf2admin.init()

import bf2events
bf2events.init()
```

**RCON 密码**：原版服务端没有 `Admin\default.cfg`，需自己建：

```
port=4711
password=你的密码
```

重启服务端，看到 `bf2admin 1.1 loaded` 就成功了。

### 2. 敲命令

**游戏内控制台**（按 `~`）：

```
rcon login 你的密码
rcon bf2player
```

**或者用外部 RCON**（推荐，输出能复制）：

```bash
python tools/bf2rcon.py "bf2player"
python tools/bf2rcon.py --host 192.168.1.100 --pass 你的密码 "bf2nowmap"
```

### 3. 命令速查

```
bf2player                           id | Playername | CDKey | IP
bf2pos <昵称>                        某玩家坐标 / 载具 / 队伍
bf2kick <昵称>                       踢出（并全服广播）
bf2ban <昵称> <分钟> <原因>           封 IP + CD-key，广播，原因入库
bf2unban <昵称>                      解封（连带解 IP + CD-key），广播
bf2nowmap                          Dalian_Plant | gpm_cq | 64
bf2banlist                         封禁列表 + 本地记录
bf2check                           自检
```

昵称支持部分匹配（忽略大小写）：`bf2kick alp` 能命中 `ALPHAHU`。

---

## QQ 群机器人对接

### 架构

```
BF2 服务端                         同一台机器                       QQ 群
┌─────────────────────┐      ┌──────────────────┐      ┌────────────────┐
│ bf2admin.py         │      │                  │      │                │
│ bf2events.py        │◄────►│  bf2http.py      │◄────►│  机器人插件     │
│  （RCON :4711）      │ RCON │  HTTP :8099      │ HTTP │ （OneBot 等）   │
└─────────────────────┘      └──────────────────┘      └────────────────┘
```

**为什么分三层**：BF2 服务端内嵌 **Python 2.3.4**，`os` 模块还被阉割，
没有任何可用的网络能力 —— 不能让它对外提供 HTTP，更不能让它阻塞。
所以它只做两件事：执行命令、把事件写进队列。
外部工具（普通 Python 3）负责 HTTP，机器人只管调用。

### 查询类（机器人主动调）

启动桥接：

```bash
python tools/bf2http.py --port 8099 --token 你的密钥
```

| 端点 | 用途 |
|---|---|
| `GET /status` | 地图 + 模式 + 人数 + 玩家名单 |
| `GET /players` | 玩家列表（含 CD-key / IP） |
| `GET /player?name=X` | 某玩家坐标 |
| `GET /map` | 地图信息 |
| `POST /cmd` | `{"cmd":"bf2kick","arg":"某人"}` |

默认**只读**（踢人/封禁要 `--allow-dangerous`）、**只监听 127.0.0.1**、
**`/raw` 关闭**。绑非回环地址却没给 `--token` 会直接拒绝启动。

### 事件推送（机器人定时拉）

`bf2events.py` 把服务端事件写进队列，机器人轮询取走：

```bash
python tools/bf2rcon.py "bf2events"          # 取走并清空（drain）
python tools/bf2rcon.py "bf2events peek"     # 只看不清
python tools/bf2rcon.py "bf2events chat on"  # 把聊天也纳入
```

**事件行格式**（Tab 分隔，方便机器人切分）：

```
<序号>	<时间戳>	<类型>	<内容...>
```

实测输出：

```
1	1789875637	round	PreGame	map=
2	1789875640	round	Playing	map=dalian_plant
3	1789875713	join	defaultPlayer	players=1
```

支持的事件类型：

| 类型 | 触发 | 内容 |
|---|---|---|
| `join` | 玩家进服 | 昵称、当前人数 |
| `leave` | 玩家退服 | 昵称、当前人数 |
| `spawn` | 玩家出生 | 昵称、坐标 |
| `killed` | 玩家被杀 | 死者、凶手、武器 |
| `chat` | 聊天（默认关闭） | 昵称、频道、内容 |
| `round` | 回合状态变化 | 状态、地图 |

**为什么用轮询而不是 webhook**：BF2 端不能阻塞（会卡游戏），
而且多数 QQ 机器人框架改动核心风险高。轮询只需在插件里起一个后台任务，
机器人挂了队列也只是堆积，不影响游戏。

### 机器人插件伪代码

```python
# 任何基于 OneBot 的框架（go-cqhttp / aiocqhttp / NoneBot 等）都适用
import requests

BRIDGE = "http://127.0.0.1:8099"
TOKEN  = "你的密钥"

def on_group_message(event):
    text = event.raw_message.strip()
    if not text.startswith("bf2"): return

    cmd = text[3:].strip()          # "player" / "pos ALPHAHU" / "nowmap"
    if cmd == "player":
        r = requests.get(BRIDGE + "/players", params={"token": TOKEN}, timeout=5)
        data = r.json()
        lines = ["id | Playername | CDKey | IP"]
        for p in data["data"]["players"]:
            lines.append("%s | %s | %s | %s" % (p["index"], p["name"], p["key"], p["ip"]))
        reply("\n".join(lines))
    elif cmd.startswith("pos "):
        name = cmd[4:]
        r = requests.get(BRIDGE + "/player", params={"name": name, "token": TOKEN}, timeout=5)
        reply(str(r.json()))

# 事件推送：后台任务定时 drain
def push_loop():
    while True:
        r = requests.get(BRIDGE + "/raw", params={"cmd": "bf2events", "token": TOKEN}, timeout=5)
        for line in r.json().get("raw", "").split("\n"):
            parts = line.split("\t")
            if len(parts) >= 3:
                kind, body = parts[2], "\t".join(parts[3:])
                if kind == "join":  send_group("[BF2] %s 加入了服务器" % body.split("\t")[0])
                if kind == "leave": send_group("[BF2] %s 离开了服务器" % body.split("\t")[0])
        time.sleep(10)
```

> `/raw` 需要桥接加 `--allow-raw`，或者给 `bf2events` 加个专用端点（见下）。

---

## 命令的三种敲法（最容易踩的坑）

| 在哪敲 | 写法 | 例子 |
|---|---|---|
| **游戏内控制台** | `rcon ` 前缀 | `rcon bf2player` |
| **外部 RCON** | 直接写 | `python tools/bf2rcon.py "bf2player"` |
| **原版引擎命令**（外部 RCON 里） | 要加 `exec ` | `... "exec mapList.list"` |

`bf2*` / `pm*` 是本项目的模组命令，外部 RCON 里直接写；
原版命令（`mapList.*`、`sv.*`、`admin.*`）才需要 `exec`。
**反过来，在 Python 内部用 `host.rcon_invoke` 时不能加 `exec`** ——
加了会被当成一条名叫 `exec ...` 的命令，返回 `Wrong syntax!`。

---

## 技术约束（改代码前必读）

服务端内嵌 **Python 2.3.4**（2005 年），且环境残缺。以下全部踩过：

| 约束 | 说明 |
|---|---|
| 禁三元表达式 | `x if c else y` 直接 SyntaxError |
| 禁 `str.partition()` / `rpartition()` / `rsplit()` | **Python 2.4+ 才有**，服务端会 `AttributeError` |
| 禁 `sorted()` / `set()` / 生成器表达式 / 装饰器 / `with` / `except X as e` | 更晚版本才有 |
| **源文件必须纯 ASCII、无 BOM** | 中文注释或 UTF-8 BOM 都会导致语法错误 |
| `os` 模块被阉割 | 无 `getcwd` / `path` / `listdir` |
| **语法错误是静默的** | 一个模块出错会让整个 `standard_admin` 包导入失败且无提示 |
| 玩家昵称带**前导空格** | `bf2.playerManager` 返回 `' name'`，与 `admin.listPlayers` 对不上 |
| BF2 **没有昵称维度的封禁** | 只有 `addAddressToBanList` / `addKeyToBanList` |

配套的 `zzdoctor.py` 会对每个同级模块做 `compile()` 预检，
把出错的模块名和行号写进 `doctor.log` —— 建议一并安装（见原仓库）。

---

## 已验证 / 未验证

**真实玩家验证通过：**
`bf2player`、`bf2pos`、`bf2kick`、`bf2ban`（IP+Key 双封，原因入库）、
`bf2unban`（离线也能按昵称解封）、`bf2nowmap`、HTTP 全部端点、
事件钩子（`join` / `round` 已实测）。

**⚠️ 已知会崩服务端：**
`pmadmin.py` 里的 `pmveh`（运行时生成载具）—— 实测 3 次尝试 3 次崩溃，
已加 `confirm` 门禁。**不要用。**

---

## 许可

MIT
