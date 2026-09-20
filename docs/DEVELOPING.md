# 开发指南 —— 怎么继续加功能

> 这份是给"以后还要加命令"用的。改代码前请先读完第三节的约束，
> 那些坑我全踩过，每条都花过时间。

---

## 一、当前架构

服务端模组放在 `<服务端>\Admin\standard_admin\`，由同目录 `__init__.py` 接线：

```python
import zzdoctor          # 必须第一个：加载诊断 + 语法预检

# 以下是原版模块
import autobalance
import tk_punish
import playerconnect
autobalance.init(); tk_punish.init(); playerconnect.init()

# 以下是本项目模块
import bf2admin
bf2admin.init()

import bf2events
bf2events.init()

import pmadmin          # 可选：诊断类命令
pmadmin.init()

import evalpy           # 可选：通过 RCON 执行任意 Python（调试用）
evalpy.init()

zzdoctor.init()
```

### 每个模块干什么

| 模块 | 命令 | 职责 |
|---|---|---|
| **`bf2admin.py`** | `bf2player` `bf2pos` `bf2kick` `bf2ban` `bf2unban` `bf2nowmap` `bf2banlist` `bf2check` | 玩家管理主模块 |
| **`bf2events.py`** | `bf2events` | 事件钩子，写入队列供机器人拉取 |
| `pmadmin.py` | `pmplayers` `pmpos` `pmkill` `pmkick` `pmban` `pmbanlist` `pmmap` `pmveh` `pmcheck` `pmtest` | 早期诊断模块（部分与 bf2admin 重叠，保留作参考） |
| `zzdoctor.py` | —— | **加载诊断**：对每个同级模块做 `compile()` 预检，出错写 `doctor.log` |

**建议加新功能放 `bf2admin.py`**（命令前缀统一 `bf2*`）。
`pmadmin.py` 里的 `pmveh` 已确认会崩服务端，不要参考它。

### 外部工具（跑在服务端同一台机器）

| 文件 | 用途 |
|---|---|
| `tools/bf2rcon.py` | 独立 RCON 客户端 —— **不进游戏就能敲命令，开发时最好用** |
| `tools/bf2http.py` | HTTP → RCON 桥接，给聊天机器人用 |

---

## 二、加一条新命令（模板）

以"显示服务器运行时长"为例，在 `bf2admin.py` 里：

**第 1 步：写命令函数**

```python
def cmd_uptime(ctx, playerId):
    """bf2uptime -- how long the round has been running."""
    out(ctx, '=== uptime ===\n')
    try:
        val = rcon('gameLogic.timeLimit')     # 换成你真正要查的东西
    except:
        val = 'ERR'
    out(ctx, '  %s\n' % str(val)[:120])
    log('bf2uptime -> %s' % str(val)[:80])
```

**第 2 步：写注册包装（一行）**

```python
def rcmd_bf2uptime(self, ctx, cmd):
    cmd_uptime(ctx, caller(ctx))
```

**第 3 步：加进 `CMDS` 字典**

```python
CMDS = {
    'bf2player':  rcmd_bf2player,
    ...
    'bf2uptime':  rcmd_bf2uptime,      # ← 加这行
}
```

**第 4 步：重启服务端，然后测**

```powershell
# 重启服务端（改 Python 必须重启，只重启服务端即可，客户端会自动重连）
cd "D:\BF2ServerForDeepseek\Battlefield 2"
.\RUN-SERVER.bat

# 用外部 RCON 测，不用进游戏
python tools/bf2rcon.py "bf2uptime"
```

**`init()` 不用改** —— 它遍历 `CMDS` 自动注册。

### 有参数的命令

`cmd.split()` 已经把参数切好了：

```python
def cmd_foo(ctx, playerId, argv):
    if not argv:
        out(ctx, 'USAGE: bf2foo <参数>\n')
        return
    first = argv[0]
    rest = ' '.join(argv[1:])
```

### 复用现成的辅助函数

`bf2admin.py` 里已有这些，直接调：

| 函数 | 作用 |
|---|---|
| `out(ctx, msg)` | 同时写游戏控制台**和** `bf2admin.log`（**必须用它，别用 `ctx.write`**） |
| `log(msg)` | 写服务端控制台和日志 |
| `rcon(cmd)` | 执行控制台命令，**返回值**，不会因出错崩服务端 |
| `all_players()` | `[(index, name, team), ...]`，**昵称已 strip** |
| `find_player(name)` | 按昵称查找（先精确后部分匹配，忽略大小写） |
| `resolve(ctx, name)` | 查找失败时自动报"未找到 + 在线名单" |
| `player_info()` | `admin.listPlayers` 解析结果（含 IP 和 CD-key），**并自动写入玩家历史** |
| `caller(ctx)` | 命令发起者的玩家索引（游戏内 rcon 才有，TCP rcon 为 None） |
| `v3(tuple)` | 坐标格式化 |

### 要发事件推送

在 `bf2events.py` 里调 `emit(kind, payload)`：

```python
emit('myevent', '某字段\t某字段2')
```

`payload` 用 **Tab 分隔**（`\t`），机器人那边按 Tab 切分。
事件会自动进队列、写 `bf2events.log`、持久化到 `bf2events.queue`。

---

## 三、⚠️ 约束（改代码前必读，每条都踩过）

### 服务端内嵌 Python 是 **2.3.4**（2005 年）

**以下全部不能用：**

| 禁用 | 说明 |
|---|---|
| `x if c else y` | 三元表达式，2.5+ |
| `str.partition()` / `rpartition()` / `rsplit()` | **2.4+** —— 我在这上面栽了好几轮 |
| `sorted()` / `set()` | 2.4+ |
| 生成器表达式 `(x for x in y)` | 2.4+ |
| 装饰器 `@xxx` | 2.4+ |
| `with` 语句 | 2.5+ |
| `except X as e` | 2.6+ |
| 相对导入 | 2.5+ |

**替代写法：**

```python
# 三元 → 用 if/else 或辅助函数
def nz(value, fallback):
    if value:
        return value
    return fallback

# partition → 用 find + 切片
dash = s.find('-')
if dash >= 0:
    head = s[0:dash]
    tail = s[dash+1:]

# sorted → 手动
names = d.keys()
names.sort()

# set → 用 dict 去重
seen = {}
for x in items:
    seen[x] = 1
```

### 源文件必须**纯 ASCII、无 BOM**

- 中文注释 → **直接 SyntaxError**
- UTF-8 BOM → **直接 SyntaxError**
- 用记事本另存为 UTF-8 会加 BOM，**改完务必检查**

**检查办法**（PowerShell）：

```powershell
$b=[IO.File]::ReadAllBytes('文件路径')
"非ASCII字节: $(($b | Where-Object { $_ -gt 127 }).Count)"
"有BOM: $($b[0] -eq 0xEF)"
```

**如果必须在输出里用中文**（比如给玩家看的提示），
在 Python 2.3 里要写成转义序列或用 `\xe4` 形式，**不能直接写中文字符**。

### `os` 模块被阉割

```
os.getcwd()    ✗  不存在
os.path        ✗  不存在
os.listdir()   ✗  不存在
```

需要定位自身目录时只能靠 `__file__` 字符串切分（见 `zzdoctor.py`）。

### 语法错误是**静默的**

一个模块有语法错误 → **整个 `standard_admin` 包导入失败** → 所有自定义命令消失，
**而且服务端可能不报错**。排查办法：看 `doctor.log`，它会逐个模块 `compile()`
并报告出错的模块名和行号。

### 玩家昵称带**前导空格**

```
bf2.playerManager 返回:  ' defaultPlayer'    ← 有空格
admin.listPlayers 解析:  'defaultPlayer'     ← 无空格
```

**比较前务必 `strip()`**（`all_players()` 里已经处理了）。

### `exec` 前缀的坑

| 场景 | 写法 |
|---|---|
| 游戏内控制台敲模组命令 | `rcon bf2player` |
| 外部 RCON 敲模组命令 | `bf2player` |
| 外部 RCON 敲**引擎命令** | `exec mapList.list` |
| **Python 内部** `host.rcon_invoke` | `rcon('mapList.list')` ← **不能加 exec** |

### ⚠️ 用 Python 2.3.4 校验语法时，必须用文本模式读文件

这是个**极易误判**的坑：这个内嵌的 2.3.4 构建（`MSC v.1310, May 26 2005`）
在 `compile()` 收到**原始字节**时**不认 CRLF 行尾**，会报
`invalid syntax (xxx.py, line 2)`，而报错行往往只是文件里的一个空行 ——
看起来像文件坏了，其实文件完全正常。

实测（同一份文件，三种读取模式）：

| 读取模式 | LF 文件 | CRLF 文件 |
|---|---|---|
| `open(p, 'r')` | OK | **OK** |
| `open(p, 'rU')` | OK | **OK** |
| `open(p, 'rb')` | OK | **FAIL: invalid syntax** |

原因是 Python 2.3 的 import 机制用**文本模式**读源码，universal newlines 会把
CRLF 规范化成 LF；用 `'rb'` 就把原始 `\r\n` 直接喂给了编译器。

**所以**：

* 校验服务端源码一律用 `open(p, 'rU').read()`，**不要用 `'rb'`**。
* 原版 BF2 自带的 `autobalance.py` / `tk_punish.py` / `playerconnect.py`
  全是 CRLF。用 `'rb'` 校验它们会全部"失败"，但它们实际上完全正常。
* 自己写的模块建议统一用 **LF**（仓库里也都是 LF），两种模式都不会有歧义。

一段可直接跑的服务端侧校验（经 RCON 触发，`_check.py` 放 standard_admin 下）：

```python
# 触发：eval execfile("D:\...\Admin\standard_admin\_check.py")
import sys
DIR = r'D:\BF2ServerForDeepseek\Battlefield 2\Admin\standard_admin'
out = []
for name in ['__init__.py', 'bf2admin.py', 'bf2events.py', 'pmadmin.py',
             'evalpy.py', 'zzdoctor.py', 'autobalance.py', 'tk_punish.py',
             'playerconnect.py']:
    p = DIR + chr(92) + name
    try:
        compile(open(p, 'rU').read(), p, 'exec')   # rU，不是 rb
        out.append(name + ' OK')
    except:
        out.append(name + ' FAIL: ' + str(sys.exc_info()[1]))
open(DIR + chr(92) + '_check.out', 'w').write(chr(10).join(out))
```

另外记住两件在这个 Python 里做不到的事：

* **`binascii` 不存在** —— `base64` 依赖它，`import base64` 会直接
  `ImportError: No module named binascii`。想从 RCON 传多行脚本进去，
  别指望 base64 编码。
* **`os` 被阉割** —— 别用 `os.path` 做路径拼接，直接字符串拼 `chr(92)`。

经 RCON 传参时还有个行为要知道：引擎会把命令按空格切分，但**引号内的空格是
安全的**（`eval ctx.write("a b c")` 正常）。多词参数请用引号包住。

## 加完后的自检

```powershell
# 1) 语法预检（本地 Python 3 能查括号/引号结构；print 报错是正常的）
python -c "import ast; ast.parse(open('bf2admin.py','rb').read())"

# 2) 2.3 禁用语法扫描（剥掉注释和字符串后查）
#    见仓库 tools/ 或手动 grep：partition / sorted( / set( / 三元

# 3) ASCII + BOM 检查（见上）

# 4) 重启服务端，看 doctor.log 有没有报错
Get-Content "D:\BF2ServerForDeepseek\Battlefield 2\doctor.log" | Select-Object -Last 10

# 5) 跑自检命令
python tools/bf2rcon.py "bf2check"

# 6) 测你的新命令
python tools/bf2rcon.py "bf2uptime"
```

---

## 五、外部 RCON 客户端（开发时最有用）

```powershell
# 单条
python tools/bf2rcon.py "bf2player"

# 一次连一条连接跑多条（更快）
python tools/bf2rcon.py "bf2player" "bf2nowmap" "bf2check"

# 连别的机器
python tools/bf2rcon.py --host 192.168.1.100 --port 4711 --pass 密码 "bf2player"
```

**为什么开发时用它**：不用启动客户端、不用进游戏、输出能直接复制。

**调试复杂问题**可以直接执行 Python（需要装了 `evalpy.py`）：

```powershell
python tools/bf2rcon.py "eval ctx.write(str(bf2.playerManager.getPlayers()))"
python tools/bf2rcon.py "eval import bf2admin; ctx.write(str(bf2admin.PLAYER_HISTORY))"
```

---

## 六、日志文件（都在服务端根目录）

| 文件 | 内容 |
|---|---|
| `bf2admin.log` | bf2* 命令的所有输出 |
| `bf2events.log` | 所有事件（进服/退服/出生/击杀/回合） |
| `bf2events.queue` | 未取走的事件（持久化，重启不丢） |
| `bf2bans.log` | 封禁记录：时间/昵称/IP/CD-key/分钟/原因 |
| `bf2players.log` | 见过的玩家：昵称/IP/CD-key（供按昵称解封） |
| `doctor.log` | **加载诊断** —— 模块加载失败先看它 |
| `pmadmin.log` | pm* 命令的输出 |
| `server-console.log` | 服务端 stdout（块缓冲，可能不完整） |

**为什么强调写文件**：游戏内控制台无法复制文字，服务端 stdout 又是块缓冲的，
**只有文件是可靠记录**。所以新命令一律用 `out()` 而不是 `ctx.write()`。
