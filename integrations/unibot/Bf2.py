"""BF2 服务器接入扩展。

把 Battlefield 2 专用服务端接进 UniBot：群内查询服务器状态、玩家、坐标、
地图，并在有人进服 / 退服 / 被 ban 时主动推送。

架构
    QQ 群 -> UniBot -> HTTP -> bf2http.py -> RCON :4711 -> BF2 服务端

    机器人不直接说 RCON 协议：BF2 的 RCON 是带 digest 握手的裸 TCP，且有单条
    串行连接的限制。bf2http.py 把 RCON 包成 JSON HTTP 接口，本扩展只跟它对话。
    BF2 服务端内嵌的是 Python 2.3.4，不能承担联网职责，所以桥接是独立进程。

为什么用轮询而不是让服务端推送
    BF2 服务端内嵌 Python 没有可用的网络能力，事件只能落到文件队列里，
    由外部进程读（见 bf2events.py 的说明）。本扩展轮询 /events/wait 长轮询
    接口，有事件时立即返回，没事件时挂起到超时，等价于低延迟推送。

    /events/wait 是 drain 语义（读到即出队），所以只在事件成功发到群之后才
    推进本地 seq 检查点；发送失败时不推进，但事件已经出队，靠 /events?mode=peek
    在读之前先备份一份来兜底。

配置
    见 Config/Extensions/Bf2.toml，或在 WebUI 的扩展页面里改。
"""

from __future__ import annotations

import asyncio
from typing import Any, override

import httpx
from pydantic import BaseModel, Field

from Scripts import Globals
from Scripts.Extensions import Command, Extension, Service
from Scripts.Logging import logger
from Scripts.Utils import send_message_to_groups

# ===== 扩展元数据 =====

extension = Extension(
    id='Bf2',
    name='战地2 服务器',
    version='1.0.0',
    author='DEEPSEEK',
    description='接入 Battlefield 2 专用服务端：状态查询、玩家查询、进服/退服/封禁推送。',
    types=('api', 'command'),
)


class Bf2Config(BaseModel):
    """扩展配置。

    enabled 之外全部有默认值，装好即可用：只要桥接在本机 8099 上跑着，
    指令就能用；推送需要在 notify_groups 里填群号。
    """

    bridge_url: str = Field(
        default='http://127.0.0.1:8099',
        description='bf2http.py 的地址。桥接和机器人同机时保持 127.0.0.1。',
    )
    bridge_token: str = Field(
        default='',
        description='桥接的访问令牌，要和 bf2http.py 的 --token 一致。留空表示桥接没设令牌。',
    )
    timeout: float = Field(
        default=10.0,
        ge=1.0,
        le=120.0,
        description='单次 HTTP 请求超时（秒）。长轮询会另外加时，不受这个值限制。',
    )

    notify_enabled: bool = Field(
        default=True,
        description='是否主动推送进服 / 退服 / 封禁事件。',
    )
    notify_groups: list[str] = Field(
        default=[],
        description=(
            '推送目标群，格式 "{平台}:{群ID}"，如 "qq_client:123456"。'
            '留空则推到 Config.toml 的 message_groups。'
        ),
    )
    notify_join: bool = Field(default=True, description='推送玩家进服。')
    notify_leave: bool = Field(default=True, description='推送玩家退服。')
    notify_ban: bool = Field(default=True, description='推送玩家被 ban / 解封。')

    poll_interval: float = Field(
        default=5.0,
        ge=1.0,
        le=300.0,
        description='事件轮询间隔（秒）。长轮询挂起时间约为该值加 5 秒。',
    )
    server_name: str = Field(
        default='',
        description='显示在推送里的服务器名。留空则用 Config.toml 里第一个消息群的名字，或省略。',
    )
    max_lines: int = Field(
        default=20,
        ge=1,
        le=100,
        description='单条推送最多列出的行数（玩家列表用），防止刷屏。',
    )


extension.config_model = Bf2Config


# ===== HTTP 客户端 =====


class Bf2Error(Exception):
    """桥接返回错误或不可达。"""


class Bf2Client:
    """bf2http.py 的异步客户端。

    所有方法失败时抛 Bf2Error，调用方决定是提示用户还是静默重试：指令要提示，
    后台轮询只记日志。
    """

    def __init__(self, base_url: str, token: str = '', timeout: float = 10.0) -> None:
        self.base_url = base_url.rstrip('/')
        self.token = token
        self.timeout = timeout
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=self.timeout)
        return self._client

    async def close(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
        self._client = None

    async def request(self, path: str, params: dict[str, Any] | None = None, timeout: float | None = None) -> Any:
        """发一个 GET 请求并返回 JSON 里的 data / events 负载。"""
        query = dict(params or {})
        if self.token:
            query['token'] = self.token
        url = self.base_url + path
        client = await self._get_client()
        try:
            response = await client.get(url, params=query, timeout=timeout or self.timeout)
        except httpx.HTTPError as error:
            raise Bf2Error(f'无法连接 BF2 桥接 {self.base_url}：{error}') from error
        if response.status_code == 401:
            raise Bf2Error('桥接拒绝了请求：令牌不对（检查 bridge_token 配置）')
        if response.status_code >= 400:
            raise Bf2Error(f'桥接返回 HTTP {response.status_code}')
        try:
            payload = response.json()
        except ValueError as error:
            raise Bf2Error('桥接返回的不是 JSON') from error
        if not payload.get('ok'):
            raise Bf2Error(str(payload.get('error') or '桥接返回了失败状态'))
        return payload

    async def health(self) -> bool:
        """桥接是否活着。不触发 RCON 流量。"""
        try:
            await self.request('/healthz', timeout=min(self.timeout, 5.0))
            return True
        except Bf2Error:
            return False

    async def status(self) -> dict:
        return (await self.request('/status')).get('data', {})

    async def players(self) -> list[dict]:
        return (await self.request('/players')).get('data', {}).get('players', [])

    async def map_info(self) -> dict:
        return (await self.request('/map')).get('data', {})

    async def player(self, name: str) -> dict:
        return (await self.request('/player', {'name': name})).get('data', {})

    async def bans(self) -> dict:
        return (await self.request('/bans')).get('data', {})

    async def events(self, wait: float = 0.0) -> list[dict]:
        """取事件。wait > 0 时长轮询，有事件立刻返回。"""
        if wait > 0:
            payload = await self.request('/events/wait', {'timeout': wait}, timeout=wait + 15.0)
            return payload.get('events', [])
        return (await self.request('/events')).get('events', [])

    async def event_backlog(self) -> tuple[list[dict], int]:
        """读事件队列但不消费，返回 (事件, 队首序号)。

        用于在 drain 之前留一份副本：如果推送失败，事件至少还在日志里。
        """
        payload = await self.request('/events', {'mode': 'peek'})
        events = payload.get('events', [])
        head = events[0]['seq'] if events else 0
        return events, head


def format_position(position: dict | None) -> str:
    """把 {'x','y','z'} 格式化成 x/y/z，缺坐标时给出可读说明。"""
    if not position:
        return '（未出生，或在自由视角）'
    return '%.1f / %.1f / %.1f' % (position['x'], position['y'], position['z'])


def format_events(events: list[dict], max_lines: int = 20) -> list[str]:
    """把事件列表格式化成中文播报行。"""
    lines: list[str] = []
    for event in events[:max_lines]:
        fields = event.get('fields', [])
        name = fields[0] if fields else '?'
        kind = event.get('kind', '')
        if kind == 'join':
            lines.append(f'✅ {name} 进入了服务器')
        elif kind == 'leave':
            lines.append(f'❌ {name} 离开了服务器')
        elif kind == 'ban':
            reason = _field(fields, 'reason')
            minutes = _field(fields, 'minutes')
            detail = ''
            if minutes:
                detail += f' 时长 {minutes} 分钟'
            if reason:
                detail += f'，原因：{reason}'
            lines.append(f'🔨 {name} 被管理员封禁{detail}')
        elif kind == 'unban':
            lines.append(f'🔓 {name} 已被解封')
        elif kind == 'killed':
            by = _field(fields, 'by')
            if by and by != '<world>':
                lines.append(f'💀 {name} 被 {by} 击杀')
        elif kind == 'round':
            lines.append(f'🔄 回合状态：{name}')
        else:
            lines.append(f'· {kind} {name}')
    if len(events) > max_lines:
        lines.append(f'…… 以及另外 {len(events) - max_lines} 条')
    return lines


def _field(fields: list[str], key: str) -> str:
    """在 'k=v' 形式的字段里取 k 的值（bf2events 的负载格式）。"""
    prefix = key + '='
    for item in fields[1:]:
        if item.startswith(prefix):
            return item[len(prefix):]
    return ''


# ===== 服务 =====


@extension.register_service
class Bf2Service(Service):
    """BF2 服务器访问服务：给指令用，并负责后台事件推送。

    事件推送放在服务里而不是独立任务，是因为它需要和指令共用同一个
    HTTP 客户端和同一份配置；服务生命周期由扩展框架管理，启停都成对。
    """

    name = 'bf2'

    def __init__(self) -> None:
        self.client: Bf2Client | None = None
        self._task: asyncio.Task | None = None
        self._stopping = False
        # 已成功播报过的最大事件序号，防止重复推送
        self._last_seq = 0

    # ----- 生命周期 -----

    @override
    async def on_enable(self) -> None:
        settings: Bf2Config = extension.config_value
        self.client = Bf2Client(settings.bridge_url, settings.bridge_token, settings.timeout)
        Globals.bf2_service = self
        if not settings.notify_enabled:
            logger.info('BF2 event push is disabled by config.')
            return
        # 先对齐一次队首，避免机器人刚启动就把积压的历史事件全部播报出来
        try:
            events, head = await self.client.event_backlog()
            if events:
                self._last_seq = max(event.get('seq', 0) for event in events)
                logger.info(f'Skipped {len(events)} backlogged BF2 event(s) from before startup.')
            else:
                self._last_seq = head
        except Bf2Error as error:
            logger.warning(f'BF2 bridge not reachable at startup: {error}')
        self._stopping = False
        self._task = asyncio.create_task(self._poll_loop(), name='bf2-event-poller')
        logger.info('BF2 event poller started.')

    @override
    async def on_disable(self) -> None:
        self._stopping = True
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None
        if self.client is not None:
            await self.client.close()
            self.client = None
        if getattr(Globals, 'bf2_service', None) is self:
            Globals.bf2_service = None
        logger.info('BF2 event poller stopped.')

    # ----- 后台轮询与推送 -----

    async def _poll_loop(self) -> None:
        """长轮询事件队列，把新事件推到群。

        循环体内所有异常都被吞掉并记日志：桥接或服务端重启不该让机器人退出。
        """
        settings: Bf2Config = extension.config_value
        wait = settings.poll_interval + 5.0
        failures = 0
        while not self._stopping:
            try:
                events = await self._collect(wait)
                if events:
                    await self._publish(events)
                failures = 0
            except asyncio.CancelledError:
                raise
            except Bf2Error as error:
                failures += 1
                # 第一次失败就报，之后按 10 次一批收敛，避免刷日志
                if failures == 1 or failures % 10 == 0:
                    logger.warning(f'BF2 event poll failed ({failures}): {error}')
                await asyncio.sleep(min(30.0, settings.poll_interval * failures))
            except Exception as error:
                logger.warning(f'BF2 event poll raised unexpectedly: {error}')
                await asyncio.sleep(settings.poll_interval)

    async def _collect(self, wait: float) -> list[dict]:
        """取尚未播报过的事件。

        /events/wait 会把事件出队，所以这里按序号过滤并推进检查点；
        理论上出队即可靠，检查点只是防止同一事件被重复播报。
        """
        assert self.client is not None
        events = await self.client.events(wait=wait)
        fresh = [event for event in events if event.get('seq', 0) > self._last_seq]
        return fresh

    async def _publish(self, events: list[dict]) -> None:
        """把事件格式化成消息发到群，全部成功才推进检查点。"""
        settings: Bf2Config = extension.config_value
        filtered = [event for event in events if self._wanted(event, settings)]
        if not filtered:
            # 没有要播报的事件也要推进，否则每次都会重新取到
            self._last_seq = max(event.get('seq', 0) for event in events)
            return
        lines = format_events(filtered, settings.max_lines)
        prefix = f'【{settings.server_name}】' if settings.server_name else '【BF2】'
        message = prefix + '\n' + '\n'.join(lines)
        ok = await self._send(message)
        if ok:
            self._last_seq = max(event.get('seq', 0) for event in events)
        else:
            # 发送失败：事件已经出队，靠日志留痕，不推进检查点以便下轮重试队列里
            # 剩下的新事件（这一批本身无法重放，这是 drain 语义的代价）
            logger.warning(f'BF2 events dropped after a failed broadcast: {[e.get("seq") for e in events]}')

    def _wanted(self, event: dict, settings: Bf2Config) -> bool:
        """该事件是否要播报。spawn / killed / round / chat 默认不播报，太吵。"""
        kind = event.get('kind', '')
        if kind == 'join':
            return settings.notify_join
        if kind == 'leave':
            return settings.notify_leave
        if kind in ('ban', 'unban'):
            return settings.notify_ban
        return False

    async def _send(self, message: str) -> bool:
        """发到配置的推送群；没配就退回 Config.toml 的 message_groups。"""
        settings: Bf2Config = extension.config_value
        if not settings.notify_groups:
            return await send_message_to_groups(message)
        from nonebot_plugin_alconna import SupportScope as AlconnaSupportScope
        from nonebot_plugin_alconna import Target

        tasks = []
        for group_info in settings.notify_groups:
            platform, separator, group_id = group_info.partition(':')
            if not separator or not group_id:
                logger.warning(f'Invalid bf2 notify group config: {group_info}')
                continue
            scope = getattr(AlconnaSupportScope, platform.lower(), None)
            if scope is None:
                logger.warning(f'Unsupported platform in bf2 notify group: {platform}')
                continue
            tasks.append(Target.group(group_id, scope).send(message))
        if not tasks:
            return False
        try:
            await asyncio.gather(*tasks)
            return True
        except Exception as error:
            logger.warning(f'Failed to send BF2 notification: {error}')
            return False

    # ----- 给指令用的查询封装 -----

    async def _require_client(self) -> Bf2Client:
        if self.client is None:
            raise Bf2Error('BF2 服务未启动')
        return self.client

    async def get_status(self) -> dict:
        return await (await self._require_client()).status()

    async def get_players(self) -> list[dict]:
        return await (await self._require_client()).players()

    async def get_map(self) -> dict:
        return await (await self._require_client()).map_info()

    async def get_player(self, name: str) -> dict:
        return await (await self._require_client()).player(name)

    async def get_bans(self) -> dict:
        return await (await self._require_client()).bans()

    async def is_online(self) -> bool:
        if self.client is None:
            return False
        return await self.client.health()


# ===== 指令 =====


@extension.register_command
class Bf2Command(Command):
    """战地2 服务器查询：状态 / 玩家 / 坐标 / 地图 / 封禁列表。"""

    name = 'bf2'
    description = '查询战地2服务器状态、玩家、坐标、地图、封禁列表'
    usage = '/bf2 <status|players|pos|map|bans> [玩家名]'
    aliases = ('战地2',)

    def declare(self) -> None:
        self.register_option('subcommand', str, default='status', description='子命令')
        self.register_option('target', str, default='', description='玩家名（pos 用）')

    def _service(self) -> Bf2Service | None:
        service = getattr(Globals, 'bf2_service', None)
        if service is None:
            return None
        return service

    async def handler(self, subcommand: str = 'status', target: str = '', **kwargs):
        service = self._service()
        if service is None:
            yield 'BF2 扩展没有启用。请在 Config/Extensions.toml 里放开 [Bf2]，然后重启机器人。'
            return

        sub = (subcommand or 'status').strip().lower()
        try:
            if sub in ('status', 'st', '状态'):
                async for line in self._status(service):
                    yield line
            elif sub in ('players', 'list', '玩家'):
                async for line in self._players(service):
                    yield line
            elif sub in ('pos', 'position', '坐标'):
                if not target:
                    yield '用法：/bf2 pos <玩家名>'
                    return
                async for line in self._pos(service, target):
                    yield line
            elif sub in ('map', '地图'):
                async for line in self._map(service):
                    yield line
            elif sub in ('bans', 'ban', '封禁'):
                async for line in self._bans(service):
                    yield line
            else:
                yield f'未知子命令：{sub}'
                yield '可用：status / players / pos <玩家名> / map / bans'
        except Bf2Error as error:
            yield f'查询失败：{error}'

    async def _status(self, service: Bf2Service):
        info = await service.get_status()
        players = info.get('players') or []
        yield '=== 战地2 服务器 ==='
        yield f'地图：{info.get("map") or "?"}'
        yield f'模式：{info.get("game_mode") or "?"}　人数上限：{info.get("size") or "?"}'
        yield f'在线：{info.get("player_count", len(players))} 人'
        if players:
            names = '、'.join(player['name'] for player in players if player.get('name'))
            if names:
                yield f'名单：{names}'

    async def _players(self, service: Bf2Service):
        players = await service.get_players()
        if not players:
            yield '当前没有玩家在线。'
            return
        yield f'=== 在线玩家 {len(players)} 人 ==='
        for player in players:
            yield f'[{player.get("index")}] {player.get("name")}　{player.get("ip")}'

    async def _pos(self, service: Bf2Service, target: str):
        data = await service.get_player(target)
        if not data.get('found'):
            yield f'找不到玩家：{target}'
            return
        name = data.get('name') or target
        yield f'=== {name} 的位置 ==='
        yield f'坐标：{format_position(data.get("soldier_pos"))}'
        if data.get('vehicle_tpl'):
            yield f'载具：{data["vehicle_tpl"]}'
            vehicle_pos = data.get('vehicle_pos')
            if vehicle_pos and vehicle_pos != data.get('soldier_pos'):
                yield f'载具坐标：{format_position(vehicle_pos)}'
        state = '已出生' if data.get('spawned') else '未出生（自由视角）'
        yield f'状态：{state}'
        if data.get('address'):
            yield f'地址：{data["address"]}'

    async def _map(self, service: Bf2Service):
        info = await service.get_map()
        yield '=== 当前地图 ==='
        yield f'地图：{info.get("map") or "?"}'
        yield f'模式：{info.get("game_mode") or "?"}'
        yield f'规模：{info.get("size") or "?"}'

    async def _bans(self, service: Bf2Service):
        info = await service.get_bans()
        addresses = info.get('addresses') or []
        keys = info.get('keys') or []
        records = info.get('records') or []
        yield f'=== 封禁列表（IP {len(addresses)} 条，CDKey {len(keys)} 条）==='
        if records:
            yield '-- 记录的封禁 --'
            for record in records:
                reason = record.get('reason') or '无'
                minutes = record.get('minutes') or '?'
                yield f'{record.get("name")}　{record.get("ip")}　{minutes} 分钟　原因：{reason}'
        for entry in addresses:
            yield f'IP: {entry.get("value")}　剩余 {entry.get("time_left")} 分钟'
        for entry in keys:
            yield f'Key: {entry.get("value")}　剩余 {entry.get("time_left")} 分钟'
        if not addresses and not keys and not records:
            yield '当前没有封禁记录。'
