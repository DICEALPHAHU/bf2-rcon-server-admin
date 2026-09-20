# pmadmin 使用与验证手册

BF2 服务端玩家管理 / 远程控制模组。

---

## 一、安装

把 `pmadmin.py` 放进服务端 `Admin\standard_admin\`，并在同目录 `__init__.py` 里接线：

```python
import pmadmin
pmadmin.init()
```

**RCON 密码**：原版服务端默认没有 `Admin\default.cfg`，需要自己建：

```
port=4711
password=你的密码
```

---

## 二、命令一览

| 命令 | 用途 | 状态 |
|---|---|---|
| `pmmap` | 地图名 / 尺寸 / 模式 / 人数 / 服务器名 | ✅ 真人验证通过 |
| `pmplayers` | 玩家名单（索引 + 队伍 + 昵称） | ✅ 真人验证通过 |
| `pmpos <昵称>` | 某玩家坐标、载具、IP、是否活着 | ✅ 真人验证通过 |
| `pmkill <昵称>` | 杀死玩家 | ✅ **真人验证通过（玩家真的死了）** |
| `pmkick <昵称> [原因]` | 踢出玩家 | ✅ **真人验证通过（玩家被踢出，随后自动重连成功）** |
| `pmban <昵称> [分钟] [原因]` | ban 玩家（原因记录并广播） | ✅ **真人验证通过（ban 生效、原因入库、解 ban 后可重连）** |
| `pmbanlist` | 服务器 ban 列表 + 本模组记录的原因 | ✅ 验证通过 |
| `pmveh <昵称> [载具]` | 在玩家面前生成载具 | ❌ **确认会崩服务端，需 `confirm` 且仅限测试服** |
| `pmcheck` | 自检（不需要玩家） | ✅ 验证通过 |
| `pmtest` | 完整探针 | ✅ 可用 |

**昵称查找规则**：先精确匹配（忽略大小写），再部分匹配。
所以 `pmkill alp` 可以命中 `ALPHAHU`。

**载具模板**（原版 BF2 自带，客户端必然有）：

```
地面:  jep_nanjing  usjep_hmmwv  jep_vodnik  jep_mec_paratrooper  jeep_faav
       usapc_lav25  apc_btr90  apc_wz551
坦克:  ustnk_m1a2  rutnk_t90  tnk_type98
防空:  usaav_m6  aav_tunguska  aav_type95  usaas_stinger
直升机: ahe_ah1z  ahe_havoc  ahe_z10  usthe_uh60  the_mi17  chthe_z8
飞机:  usair_f15  usair_f18  air_f35b  ruair_mig29  air_j10
       ruair_su34  air_a10  air_su30mkk
其他:  boat_rib  uav_pred  ats_tow
```

---

## 三、引擎命令来源（原版服务端文件，非猜测）

这些命令的存在与参数格式，都是从原版服务端自带的文件里核出来的：

- `Admin\standard_admin\playerconnect.py:27` → `admin.banPlayer %d %d`
- `Admin\standard_admin\tk_punish.py:93` → `admin.banPlayerKey %d Round`
- `mods\bf2\Settings\AliasedCommands.con` → `admin.kickplayer` / `admin.banplayer` /
  `admin.listBannedAddresses` / `admin.changemap` / `admin.servermessage` 等
- 另外的 `admin.*` 全清单也来自 `AliasedCommands.con`（该文件是原版自带的别名表）

**注意**：`admin.*` 命令**不在**引擎控制台命令表里（bf2tech 的全表里搜不到 `admin`），
它们是 Python 层注册的别名。所以从外部 RCON 调用时必须加 `exec` 前缀
（`exec admin.kickPlayer 0`），而在 Python 内部用 `host.rcon_invoke` 时**不能加**
（加了会被当成一条名叫 `exec ...` 的命令，返回 `Wrong syntax!`）。这个坑我踩过。

---

## 四、验证结果

### 已用真实玩家验证通过 ✅

验证方式：用命令行启动一个真实客户端并自动连服，得到真实玩家后逐项测试。

```
启动: BF2.exe +playerName PMTestBot +joinServer 127.0.0.1 +port 16567 +fullscreen 0
      （客户端需窗口化，分辨率 1024x768@60Hz —— 120Hz 会导致不显示）
```

| 命令 | 实测结果 |
|---|---|
| `pmplayers` | ✅ 正确列出：`idx=0 team=2 name=defaultPlayer` + IP + CD-key |
| `pmpos <全名>` | ✅ `soldier_pos -> 791.00/163.07/30.00`，`vehicle_tpl -> us_heavy_soldier`，`alive -> 1` |
| `pmpos <部分名>` | ✅ `pmpos default` 成功匹配 `defaultPlayer`（模糊查找可用） |
| **`pmkill`** | ✅ **玩家真的死了**（用户亲眼确认）。判据见下方"重要教训" |
| `pmmap` | ✅ `dalian_plant` / `(2048,2048)` / `gpm_cq` / `64` |
| `pmbanlist` | ✅ 返回服务器 ban 列表 |
| `pmcheck` | ✅ 自检全绿 |

### ⚠️ 重要教训：怎么判断"击杀成功"

最初我判定 `pmkill` **无效**，因为：

```
击杀后立刻查询:  alive=1  dmg=0.0     ← 看起来没死
```

**这是误判。** `isAlive()` 在客户端处理死亡之前**还会返回 1**。
正确的判据是：

- **位置跳到另一个出生点**（实测：`791/163/30` → `792/163/-6`）
- **`getTimeToSpawn()` 返回非 0**（实测出现过 `0.0667`，那是重生等待时间）

### 确认**无效**的击杀方法（都实测过）

| 方法 | 结果 |
|---|---|
| `soldier.setDamage(0)` | ✅ **有效**（这才是正确方法） |
| `soldier.setDamage(0.1)` | ❌ 伤害停在 0.1，不死 |
| `player.setSuicide(1)` | ❌ 标志置 1，不死 |
| 改模板 `criticalDamage`/`hpLostWhileCriticalDamage` | ❌ 不可靠 |
| 传送到地图外（y=5000） | ❌ `damageForBeingOutSideWorld` 为 0 |
| 抬到 400 米高空摔下 | ❌ 服务端改位置不触发物理 |
| `admin.killPlayer` | ❌ 该命令不存在 |

### ⚠️ 尚未验证 + 崩溃风险

| 项目 | 状态 |
|---|---|
| `pmveh` 生成的载具**客户端能否看见** | ❌ 未验证 |
| `pmkick` / `pmban` 对真人执行 | ❌ 未验证（命令通路已用无效索引验证过） |

**⚠️ 服务端崩溃记录**：调查过程中服务端**崩溃过三次**，全都在
"涉及载具生成"或"击杀之后"的场景。崩溃是**静默的**（无日志、无转储、
`server-console.log` 为 0 字节）。

已做隔离实验（每组重启单独测试）：

```
只 eval                             存活
只 Object.create 载具                存活
Object.create 载具 + 设坐标          存活
Object.create 静态物件 + 设坐标       存活
Object.list                         存活
```

**单独每个操作都不崩**，但真实玩家在场时出现过崩溃。
**所以 `pmveh` 在正式服使用前，务必先在测试服验证。**

---

## 五、待验证项的测试步骤

### `pmveh` —— ⚠️ 已确认会崩服务端，现在需要 `confirm` 参数

**实测结论（3 次尝试，3 次崩溃）：**

| 尝试 | 场景 | 结果 |
|---|---|---|
| 1 | 无玩家，`Object.create jep_nanjing` | ✅ 存活 |
| 2 | 有玩家，`Object.create` + 设坐标 | 💥 崩 |
| 3 | 有玩家，完整沙盒顺序 | 💥 崩 |
| 4 | 有玩家，去掉 `Object.team` 后重试 | 💥 崩 |

**崩溃是静默的**：无日志、无转储、`server-console.log` 为 0 字节，
RCON 端口直接消失。

**另一个实测发现**：沙盒代码里的 `Object.team 2` 在这台服务端上返回
**`Unknown object or method!`** —— **该命令在此版本不存在**，尽管沙盒的
`sbxCore.createObject` 调用了它。这也说明沙盒是在别的 BF2 版本上开发的。

**因此 `pmveh` 现在默认拒绝执行**，必须显式加 `confirm`：

```
pmveh <玩家> <模板> confirm
```

（已从代码里去掉无效的 `Object.team` 调用。）

**用法建议**：只在**测试服**上、**无玩家或可承受崩溃**的情况下试。
正式服不要用 —— 有一个确定可用的替代方案在下面。

### 替代方案：`pmkill` + `pmkick`

既然生成载具会崩，实际运营中需要"处理玩家"时请用：

- `pmkill <昵称>` —— ✅ 已真人验证有效
- `pmkick <昵称> [原因]` —— ✅ 命令通路已验证

### `pmkick` / `pmban` —— ✅ 已真人验证

| 测试 | 结果 |
|---|---|
| `pmkick defaultPlayer 测试踢人功能` | ✅ 玩家被踢出（`players online: 1 → 0`），服务端存活 |
| 客户端自动重连 | ✅ BF2 客户端会自己重连，无需重新加载 |
| `pmban defaultPlayer 30 测试封禁原因` | ✅ 玩家被踢出，`Time Left: 1793`（≈30 分钟） |
| 原因记录 | ✅ 写入 `banlist.log`，`pmbanlist` 也能显示 |
| `exec admin.removeAddressFromBanList <IP>` + `clearbanlist` | ✅ 解 ban 成功，客户端随即重连上 |

**注意**：`pmbanlist` 读的是服务器自身的 ban 列表（按 IP）。
BF2 的 ban 机制**只针对 IP**，不针对昵称/CD-key。

**已知缺陷**：`banlist.log` 里如果记录了中文原因，服务端重启时
`load_ban_notes()` 用 `ascii` 读取会抛异常（Python 2.3 默认编码问题），
导致历史原因读不回来。写入本身没问题（文件内容正确）。
**改进办法**：改用英文记录原因，或把原因转成 `repr()` 再写。

### `pmveh` —— ❌ 已确认会崩服务端

见上一节的崩溃记录。**三次尝试三次崩溃**，已加 `confirm` 门禁。

---

## 六、日志

| 文件 | 内容 |
|---|---|
| `pmadmin.log` | 所有命令的输出与结果（**主要证据**） |
| `banlist.log` | ban 记录：时间 / 昵称 / 时长 / 索引 / 原因（BF2 自身不存原因） |
| `server-console.log` | 服务端 stdout（由 RUN-SERVER.bat 重定向，块缓冲，可能不完整） |

**为什么必须写文件**：游戏内控制台无法复制文字，stdout 又是块缓冲的，
只有文件是可靠记录。

---

## 七、技术约束（改这个模组前必读）

服务端内嵌 **Python 2.3.4**（2005 年），且环境残缺：

| 约束 | 说明 |
|---|---|
| 禁三元表达式 | `x if c else y` 直接 SyntaxError |
| 禁 `sorted()` / `set()` | 2.4+ 才有 |
| 禁装饰器 / `with` / `except X as e` | 更晚版本才有 |
| **源文件必须纯 ASCII、无 BOM** | 中文注释或 BOM 都会导致语法错误 |
| `os` 模块被阉割 | 无 `getcwd` / `path` / `listdir` |
| 语法错误是静默的 | 一个模块出错会让整个 `standard_admin` 包导入失败且无提示 |

**最后一条的代价**：调查中 `pmadmin.py` 曾因一个三元表达式和一个 BOM
两次加载失败。**所以配套的 `zzdoctor.py` 会逐个 `compile()` 检查同级模块，
把出错的模块名和行号写进 `doctor.log`。** 强烈建议一并安装。

---

## 八、配合的外部工具

`bf2rcon.py` —— 独立的 RCON 客户端，可以**不进游戏**就执行命令：

```bash
python bf2rcon.py "pmmap" "pmplayers"
python bf2rcon.py --stdin < commands.txt
```

**为什么需要它**：探针和测试原本必须由人进游戏、在游戏内控制台敲命令，
有了它就可以自动化验证。注意：

- 直接写 `pmmap` 即可（admin 模块注册的命令）
- **引擎控制台命令要加 `exec` 前缀**：`exec mapList.list`
- 认证方式与游戏内不同：TCP 客户端要发 `login <md5(seed+password)>`，
  其中 seed 来自连接时的欢迎消息（`### Digest seed: xxxx`）

`bf2test.py` / `crashprobe.py` —— 批量测试与崩溃隔离实验脚本。

---

## 九、QQ 群机器人接口（已实现）

文件：`bf2http.py` —— HTTP → RCON 桥接层。

### 架构

```
QQ 群 → 机器人 → HTTP 请求 → bf2http.py → RCON :4711 → BF2 服务端
```

**为什么单独一个进程**：BF2 服务端内嵌的是 Python 2.3.4，`os` 模块还被阉割，
没有任何可用的网络能力，不能让它对外提供 HTTP。桥接层是普通 Python 3，
跑在同一台机器上，同时够得到机器人侧和 RCON 侧。

### 启动

```bash
python bf2http.py                              # 127.0.0.1:8099，只读
python bf2http.py --token SECRET               # 要求 token
python bf2http.py --allow-dangerous --token S  # 允许 pmkick/pmban/pmkill/pmveh
python bf2http.py --allow-raw --token S        # 允许任意 RCON 命令（危险）
python bf2http.py --help
```

### 端点

| 端点 | 说明 |
|---|---|
| `GET /healthz` | 存活探测，不产生 RCON 流量 |
| `GET /status` | 地图 + 模式 + 人数 + 玩家名单（给机器人做状态卡） |
| `GET /map` | 地图名 / 尺寸 / 模式 / 服务器名 |
| `GET /players` | 玩家名单 |
| `GET /player?name=X` | 某玩家详情与坐标 |
| `POST /cmd` | `{"cmd":"pmmap"}` 或 `{"cmd":"pmkill","arg":"昵称"}` |
| `GET /raw?cmd=...` | 任意 RCON 命令，**需 `--allow-raw`** |

**鉴权**：`?token=XXX` 或请求头 `X-Token: XXX`。

### 安全设计（重要）

RCON 端口等于服务端完全控制权，所以桥接层默认是收紧的：

- **默认只绑定 `127.0.0.1`**
- **`/raw` 默认禁用**（否则等于把 RCON 直接暴露出去）
- **`/cmd` 默认只允许只读命令**：`pmmap` / `pmplayers` / `pmpos` /
  `pmbanlist` / `pmcheck`
- **破坏性命令**（`pmkick` / `pmban` / `pmkill` / `pmveh`）**需显式 `--allow-dangerous`**
- **绑定到非回环地址时若没给 `--token`，直接拒绝启动**（防止误暴露）

### 实测结果

```
GET  /healthz              -> 200 {"ok":true,"service":"bf2http"}
GET  /status               -> 200 地图/模式/人数/玩家名单，JSON 结构化
GET  /map                  -> 200
GET  /players              -> 200
GET  /player?name=nobody   -> 200 {"player not found":"nobody", ...}
GET  /player（缺参数）      -> 400
GET  /raw?cmd=pmmap        -> 403 "raw command execution is disabled; ..."
GET  /nope                 -> 404 带可用端点列表
POST /cmd {"cmd":"pmban"}  -> 403 "command not allowed" + allowed 列表
POST /cmd {"cmd":"pmmap"}  -> 200
POST /cmd {"cmd":"pmkill"} -> 200（仅在 --allow-dangerous 时）
token: 无/错 -> 401，正确 -> 200
```

### 机器人侧怎么接

机器人只需要发一个 HTTP 请求。例如「服务器什么图」：

```
GET http://127.0.0.1:8099/status?token=XXX
→ {"map":"dalian_plant","game_mode":"gpm_cq","player_count":0,...}
```

「查某人坐标」：

```
GET http://127.0.0.1:8099/player?name=ALPHAHU&token=XXX
→ {"soldier_pos":"-773.52/185.10/-121.81", ...}
```

「踢人」（需 `--allow-dangerous`）：

```
POST http://127.0.0.1:8099/cmd?token=XXX
{"cmd":"pmkick","arg":"某人 挂机"}
```

**注意**：`/player` 与 `/cmd` 返回的是服务端原始文本的解析结果，
数值都是字符串（BF2 输出格式如此）。机器人侧展示前建议做一次格式化。

### 崩溃风险提醒

`pmveh`（生成载具）在调查中曾两次导致服务端崩溃，触发条件未完全复现
（详见第五节）。**给机器人开放 `pmveh` 前，请先在测试服上确认稳定性。**
