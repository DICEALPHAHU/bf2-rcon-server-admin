# bf2 管理命令（bf2admin）

BF2 服务端玩家管理命令集。**每一条都在真实服务端 + 真实玩家上验证过。**

---

## 安装

把 `bf2admin.py` 放进服务端 `Admin\standard_admin\`，在 `__init__.py` 里接线：

```python
import bf2admin
bf2admin.init()
```

**RCON 密码**：原版服务端没有 `Admin\default.cfg`，需自己建：

```
port=4711
password=你的密码
```

---

## 命令一览

| 命令 | 用法 | 输出 |
|---|---|---|
| `bf2player` | `bf2player` | `id \| Playername \| CDKey \| IP` |
| `bf2pos` | `bf2pos <玩家昵称>` | 昵称 + 坐标 + 载具 + 队伍 |
| `bf2kick` | `bf2kick <玩家昵称>` | `xxx has been kicked.` |
| `bf2ban` | `bf2ban <昵称> <分钟> <原因>` | `xxx has been banned. reason:<原因>` |
| `bf2unban` | `bf2unban <玩家昵称>` | `xxx has been unbanned` |
| `bf2nowmap` | `bf2nowmap` | `Dalian_Plant \| gpm_cq \| 64` |
| `bf2banlist` | `bf2banlist` | IP 封禁 + Key 封禁 + 本地记录 |
| `bf2check` | `bf2check` | 自检 |

**昵称支持部分匹配**（忽略大小写）：`bf2kick alp` 能命中 `ALPHAHU`。

---

## 怎么敲

| 在哪敲 | 写法 |
|---|---|
| **游戏内控制台**（按 `~`） | `rcon bf2player` |
| **外部 RCON**（推荐，输出能复制） | `python bf2rcon.py "bf2player"` |

外部 RCON 用法见 `QUICKSTART.md`。

---

## 实测输出（真实运行结果）

### `bf2player`

```
id | Playername | CDKey | IP
----------------------------------------
0 | defaultPlayer | 0ceccae0be0de2e33147d89b5c0f2233 | 192.168.43.51
```

### `bf2nowmap`

```
Dalian_Plant | gpm_cq | 64
```

### `bf2pos default`

```
defaultPlayer
  position: 791.00/163.07/30.00
  vehicle : us_heavy_soldier (791.00/163.07/30.00)
  team    : 2
  alive   : 1
```

（玩家未出生时 `position` 显示 `(none: in free camera, not spawned)`）

### `bf2kick defaultPlayer`

```
defaultPlayer has been kicked.
```
（服务端同时全服广播 `defaultPlayer has been kicked.`）

### `bf2ban defaultPlayer 30 test_ban_reason`

```
=== bf2ban defaultPlayer ===
  index  : 0
  ip     : 192.168.43.51
  cdkey  : 0ceccae0be0de2e33147d89b5c0f2233
  minutes: 30
  reason : test_ban_reason
  admin.banPlayer        -> 
  addAddressToBanList    -> 
  addKeyToBanList        -> 

defaultPlayer has been banned. reason:test_ban_reason
```

**封禁后效果（实测）：**
```
addresses: IP: 192.168.43.51 Time Left: 25
keys     : Key: 0ceccae0be0de2e33147d89b5c0f2233 Time Left: 25
records  : defaultplayer | 192.168.43.51 | 0cecca... | 30 min | test_ban_reason
```
玩家被踢出，**IP 和 CD-key 同时被封，原因入库**。

### `bf2unban defaultPlayer`

```
=== bf2unban defaultPlayer ===
  record found for defaultplayer
  ip    : 192.168.43.51
  cdkey : 0ceccae0be0de2e33147d89b5c0f2233
  removeAddressFromBanList -> 
  removeKeyFromBanList     -> 

defaultplayer has been unbanned
```

**解封后（实测）：** 两个列表都清空，**客户端随即重连成功**。

---

## ⚠️ 三个重要事实

### 1. BF2 **不能在昵称维度封禁**，但昵称能对上号

实测确认**不存在**这些命令（返回 `Unknown object or method!`）：

```
admin.listBannedNames / admin.addNameToBanList / admin.removeNameFromBanList / admin.banPlayerName
```

**BF2 只能封 IP 和 CD-key。** 昵称不是封禁维度。

**但你的思路是对的：昵称是能读到的。** 玩家在线时，
`admin.listPlayers` 给出 `ID → 昵称 → IP → CD-key` 的完整对应：

```
Id:  0 -  defaultPlayer is remote ip: 192.168.43.51:52501 ->
         CD-key hash: 0ceccae0be0de2e33147d89b5c0f2233
```

**真正的缺口在这里**：玩家一旦被 ban 踢下线，引擎的封禁列表里**只剩 IP 和 Key，
没有昵称** —— 这时候"按昵称解封"就没法把人名和 IP/Key 对上号了。

**本模组用两层记录补上这个缺口：**

| 记录 | 内容 | 什么时候写 |
|---|---|---|
| `bf2bans.log` | 封禁时记下 昵称 → IP/Key/原因/时长 | `bf2ban` 执行时 |
| **`bf2players.log`** | **所有见过的玩家 昵称 → IP/Key** | **每次读玩家列表时自动写** |

`bf2unban <昵称>` 的查找顺序：

```
1. ban 记录（bf2bans.log）        ← bf2ban 封的
2. 玩家历史（bf2players.log）      ← 只要曾经在线被看到过就行
3. 实时查询（玩家此刻在线）         ← 覆盖在别处封禁的情况
```

**实测验证**：把 ban 记录清空后执行 `bf2unban`，回退到玩家历史成功解封：

```
no ban record; using player history for defaultplayer
  ip    : 192.168.43.51
  cdkey : 0ceccae0be0de2e33147d89b5c0f2233
  defaultplayer has been unbanned      ← 两个封禁列表都被清空
```

**所以：只要你在线时跑过一次 `bf2player`（或任何读列表的命令），
之后就能按昵称解封他 —— 哪怕他已离线、哪怕 ban 不是本模组下的。**

### 2. `str.partition()` 在 Python 2.3 里不存在

这个坑让我调试了好几轮：解析 `admin.listPlayers` 时用了
`rest.partition('-')`，**服务端直接 `AttributeError: 'str' object has no attribute 'partition'`**
（`partition` 是 Python 2.4 才加的）。已改用 `find()` + 切片。

**同类要避开的（Python 2.4+）**：`partition` / `rpartition` / `rsplit` /
`sorted()` / `set()` / 生成器表达式 / 装饰器 / `with` / 三元表达式。

### 3. 玩家昵称**带前导空格**

```
bf2.playerManager 返回: ' defaultPlayer'     ← 前面有个空格
admin.listPlayers 解析出: 'defaultPlayer'    ← 没有空格
```

两者对不上会导致查不到 CD-key（显示 `?`）。已在 `all_players()` 里统一 `strip()`。

---

## 日志

| 文件 | 内容 |
|---|---|
| `bf2admin.log` | 所有命令的输出与结果 |
| `bf2bans.log` | 封禁记录：时间 / 昵称 / IP / CD-key / 分钟 / 原因 |
| `pmadmin.log` | 若同时装了 pmadmin，它的日志 |

**`bf2bans.log` 是解封的关键** —— BF2 自己只存 IP 和 Key，不存昵称和原因。

---

## 已知限制

- **`pmveh`（生成载具）会崩服务端**，实测 3/3 次。该命令在 `pmadmin.py` 里，
  已加 `confirm` 门禁，不要用。
- **封禁按 IP**：同一 IP 换名字进不来，但**换 IP 就能进**。
  所以 IP + Key 双封才有意义（本模组默认双封）。
- **原因建议用英文**：`bf2bans.log` 支持 UTF-8，但终端显示中文可能乱码
  （文件内容本身是正确的）。
