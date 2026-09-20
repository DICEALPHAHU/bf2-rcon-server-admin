# pmadmin 快速上手（实战用法）

> 完整文档见 `USAGE.md`（含验证记录与崩溃风险）。
> 这份只讲**怎么用**。

---

## 一、先搞清一件事：命令有三种敲法

这是最容易踩的坑。**同一个命令，在不同地方敲，前缀不一样：**

| 敲命令的地方 | 写法 | 例子 |
|---|---|---|
| **游戏内控制台**（按 `~`） | `rcon ` + 命令 | `rcon pmplayers` |
| **外部 RCON 客户端**（本手册用的） | 直接写命令 | `python bf2rcon.py "pmplayers"` |
| **引擎自带命令**（外部 RCON 里） | 要加 `exec ` | `python bf2rcon.py "exec mapList.list"` |

**`pm*` 系列是本模组注册的命令，外部 RCON 里直接写就行。
原版引擎命令（`mapList.*`、`sv.*`、`admin.*`）才需要 `exec` 前缀。**

---

## 二、方式 A：游戏内控制台（最简单）

1. 启动服务端，进游戏
2. 按 `~` 调出控制台
3. 先登录一次（每次进服都要）：
   ```
   rcon login deepseek
   ```
   看到 `Authentication successful, rcon ready.` 就对了
4. 然后就能敲命令：
   ```
   rcon pmplayers
   rcon pmpos ALPHAHU
   rcon pmkill 捣乱的
   ```

**缺点**：输出在游戏控制台里，**没法复制**，长了还得滚动看。

---

## 三、方式 B：外部 RCON（推荐，能复制输出）

在服务器那台机器上开 PowerShell：

```powershell
cd "F:\Users\12875\Deepseek Harness\bf2-pmadmin"
```

### 基本用法

```powershell
python bf2rcon.py "pmplayers"
```

一条连接跑多条命令（更快）：

```powershell
python bf2rcon.py "pmplayers" "pmmap" "pmbanlist"
```

### 连到别的机器

```powershell
python bf2rcon.py --host 192.168.1.100 --port 4711 --pass 你的密码 "pmplayers"
```

> 密码在服务端的 `Admin\default.cfg` 里。当前测试服是 `deepseek`。

### 批量执行（适合脚本）

建一个文本文件 `cmds.txt`：

```
pmplayers
pmmap
pmbanlist
```

然后：

```powershell
Get-Content cmds.txt | python bf2rcon.py --stdin
```

---

## 四、命令逐个用法

### 📋 `pmplayers` —— 看谁在线

```powershell
python bf2rcon.py "pmplayers"
```

输出：
```
=== players online: 2 ===
idx    team  name
0      2     defaultPlayer
1      1     ALPHAHU

admin.listPlayers:
Id: 0 - defaultPlayer is remote ip: 192.168.43.51:56036
```

**`idx` 就是玩家索引**，后面 `pmban` 之类会用到（但本模组都支持用昵称，不用记索引）。

---

### 📍 `pmpos <昵称>` —— 查某人坐标

```powershell
python bf2rcon.py "pmpos ALPHAHU"
```

输出：
```
===  ALPHAHU (idx=1 team=1) ===
  soldier_pos   -> 791.00/163.07/30.00
  vehicle_pos   -> 791.00/163.07/30.00
  vehicle_tpl   -> us_heavy_soldier
  alive         -> 1
  valid         -> 1
  address       -> 192.168.43.51
  is_ai         -> 0
  commander     -> 0
```

**坐标格式是 `X / Y(高度) / Z`**，和 `StaticObjects.con` 里的一致 ——
可以直接拿去改地图。

**未出生时会显示**：
```
soldier_pos   -> (none: in free camera, not spawned)
```

**部分昵称也能匹配**：`pmpos alp` 也能命中 `ALPHAHU`。

---

### 🔪 `pmkill <昵称>` —— 杀死玩家

```powershell
python bf2rcon.py "pmkill 捣乱的"
```

输出：
```
pmkill  捣乱的 (idx=3): setDamage(0) on soldier (template us_heavy_soldier)
```

**⚠️ 判断是否成功别看 `alive`**：`isAlive()` 在客户端处理死亡前还会返回 `1`。
**正确的判据是**：玩家位置跳到别的出生点，或者 `getTimeToSpawn()` 变成非 0。

---

### 👢 `pmkick <昵称> [原因]` —— 踢人

```powershell
python bf2rcon.py "pmkick 捣乱的"
python bf2rcon.py "pmkick 捣乱的 使用外挂"
```

带原因时，原因会**全服广播**。

**被踢的客户端会自动重连**（BF2 的行为）。

---

### 🔨 `pmban <昵称> [分钟] [原因]` —— 封禁

```powershell
python bf2rcon.py "pmban 捣乱的"                    # 默认 60 分钟
python bf2rcon.py "pmban 捣乱的 30"                 # 30 分钟
python bf2rcon.py "pmban 捣乱的 30 使用外挂"         # 30 分钟 + 原因
```

输出：
```
pmban  捣乱的 (idx=3) for 30 min -> 
  reason: 使用外挂
```

**原因会存进 `banlist.log`**（BF2 自身不存原因，这是我们自己记的）。

**解封**：
```powershell
python bf2rcon.py "exec admin.removeAddressFromBanList 192.168.43.51"
python bf2rcon.py "exec admin.clearbanlist"
```

> **重要**：BF2 的 ban **是按 IP 封的**，不按昵称也不按 CD-key。
> 所以同一个 IP 换名字照样进不来，但换个 IP 就能进。

---

### 📜 `pmbanlist` —— 看封禁列表 + 原因

```powershell
python bf2rcon.py "pmbanlist"
```

输出：
```
=== server ban list ===
IP: 192.168.43.51 Time Left: 1793

=== reasons recorded by pmadmin this session ===
  捣乱的            30 min  使用外挂
```

---

### 🗺️ `pmmap` —— 服务器信息

```powershell
python bf2rcon.py "pmmap"
```

```
=== map ===
  name           : dalian_plant
  world_size     : (2048, 2048)
  game_mode      : gpm_cq
  max_players    : 64
  players_now    : 2
  map_list       : 0: "dalian_plant" gpm_cq 64
  current_index  : 0
  server_name    : Default Server Name
```

---

### 🩺 `pmcheck` —— 自检（不用玩家）

```powershell
python bf2rcon.py "pmcheck"
```

返回 Python 版本、玩家查找测试、kick/ban 命令通路测试。
**出问题时先跑这个。**

---

### 🚗 `pmveh` —— ❌ 别用

**已实测 3 次尝试 3 次崩服务端**（有玩家在线时）。现在必须加 `confirm` 才会执行：

```powershell
python bf2rcon.py "pmveh ALPHAHU jep_nanjing"            # 只会打印警告
python bf2rcon.py "pmveh ALPHAHU jep_nanjing confirm"    # 真的执行（会崩，仅测试服）
```

**正式服不要用。** 需要"处理某个玩家"就用 `pmkill` 或 `pmkick`。

---

## 五、常用组合（管理员日常）

```powershell
# 上线先看情况
python bf2rcon.py "pmmap" "pmplayers"

# 看某人在哪
python bf2rcon.py "pmpos 某人"

# 踢人并广播原因
python bf2rcon.py "pmkick 某人 挂机"

# 封禁 1 小时
python bf2rcon.py "pmban 某人 60 恶意TK"

# 查看和解除封禁
python bf2rcon.py "pmbanlist"
python bf2rcon.py "exec admin.removeAddressFromBanList <IP>"
```

---

## 六、给 QQ 机器人用（HTTP 接口）

启动桥接（在服务器上另开一个窗口）：

```powershell
cd "F:\Users\12875\Deepseek Harness\bf2-pmadmin"
python bf2http.py --port 8099 --token 你的密钥
```

**默认只读**，能查不能改。要允许踢人/封禁，加 `--allow-dangerous`：

```powershell
python bf2http.py --port 8099 --token 你的密钥 --allow-dangerous
```

### 机器人侧怎么调

```powershell
# 服务器状态（地图、人数、名单）
Invoke-RestMethod "http://127.0.0.1:8099/status?token=你的密钥"

# 查某人坐标
Invoke-RestMethod "http://127.0.0.1:8099/player?name=ALPHAHU&token=你的密钥"

# 踢人（需 --allow-dangerous）
Invoke-RestMethod "http://127.0.0.1:8099/cmd?token=你的密钥" -Method POST `
  -ContentType "application/json" -Body '{"cmd":"pmkick","arg":"某人 挂机"}'
```

返回的都是 JSON：

```json
{
  "ok": true,
  "data": {
    "map": "dalian_plant",
    "game_mode": "gpm_cq",
    "player_count": 2,
    "players": [{"index":0,"team":2,"name":"defaultPlayer"}]
  }
}
```

### 安全提醒

- **默认只监听 `127.0.0.1`**，外部访问不到
- **`/raw`（任意命令执行）默认关闭**
- **踢人/封禁默认关闭**，要 `--allow-dangerous`
- **如果绑到非回环地址又没给 `--token`，程序会直接拒绝启动**
- 机器人若在别的机器上，用端口转发或 SSH 隧道，**别直接 `--host 0.0.0.0` 暴露**

---

## 七、出问题怎么办

| 现象 | 处理 |
|---|---|
| `unknown command: 'pmplayers'` | 模组没加载。看 `doctor.log` 和 `pmadmin.log` |
| `error: not authenticated` | 外部 RCON 会自动认证；游戏内要先 `rcon login 密码` |
| `Wrong syntax!` | 你可能给引擎命令漏了 `exec` 前缀，或者给 `pm*` 命令多加/少加了 |
| 命令没反应 | 先跑 `pmcheck`，再看 `pmadmin.log` |
| 服务端崩了 | `pmveh` 是已知崩溃源；其他情况查 `doctor.log` |

**日志位置**（服务端根目录）：
```
pmadmin.log     ← 所有命令的输出（主要证据）
banlist.log     ← 封禁记录（时间/昵称/时长/原因）
doctor.log      ← 模组加载诊断
```

**为什么强调看文件**：游戏内控制台没法复制，服务端 stdout 又是块缓冲的，
**只有这些文件是可靠记录**。
