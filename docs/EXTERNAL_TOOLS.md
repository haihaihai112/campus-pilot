# 地图、天气与个人日历扩展

## 能力与边界

|能力|本次实现|当前连接状态|
|---|---|---|
|地图|高德城市内地点候选查询、步行/驾车路线、距离和耗时；真实 MCP 工具|需要本地配置 Web 服务 Key，未完成有效凭据下的实测|
|天气|高德天气预报、目标日期过滤、发布时间和服务商来源|与地图共用 Key；不能查询服务未提供的日期|
|个人日历|平台无关适配接口、忙闲查询入口、同步确认、版本绑定、幂等与部分失败恢复|按用户要求暂不绑定任何平台，不含 OAuth 实现|
|学校通知|数据结构、抽象接口、MCP 工具占位|not_connected，不返回模拟通知|
|个人课表|数据结构、抽象接口、MCP 工具占位|not_connected，尚未实现校方 API 或文件导入|

地图、天气属于主助手可调用的工具；工作台也可独立查询。校内排程仍采用原有合成通行时间。此版本不自动将地图路线、天气或外部忙闲结果注入 A2A 确定性排程，不会因一次天气预报修改已确认的计划。实际到校时刻需由用户确认后再提交规划。

## 配置与启动

将 `.env.example` 复制为 `.env`，仅在本地填写 `AMAP_API_KEY`。Key 应是高德 Web 服务类型，不是浏览器 JS Key。无需在聊天中发送。未配置时无外部网络请求，工具明确返回 `not_configured`；有配置仅标记 `configured_unverified`，调用结果才代表本次服务是否可用。

```powershell
uv sync --frozen
uv run --no-sync campus all --fixture
```

打开 `/ui/` 的“外部工具”页：检查状态 → 按城市查询地点 → 确认具体候选 → 填入起终点坐标查询路线。地点表同时返回城市行政区代码，可用于天气查询。天气日期留空时显示服务商当前预报；演示日期 `2026-09-05` 不代表服务商当前日期。

`fixture` 仅替代语言模型。配置有效高德 Key 后，外部工具仍请求真实高德 API，并可能消耗调用额度。自动测试使用 HTTP 测试替身，不消耗高德额度。

合成“明德/致远”没有真实地理坐标。只有明确要映射到本人选定的现实地点时，才创建本地 `config/campus-locations.json`，并设置 `CAMPUS_LOCATIONS_FILE` 为其绝对路径。格式为：顶层校区 ID 对应一个对象，其中 `display_name` 为真实地点名称、`gate_coordinate` 为已经核对的 GCJ-02 经度和纬度字符串、`city_adcode` 为六位行政区代码。发布模板是空对象 `{}`，不预填真实高校坐标。

Docker Compose 将 `./config` 只读挂载到工具服务 `/config`，按上述名称读取配置。其他服务不需要高德 Key。校区映射文件不进入源代码交付压缩包。

## MCP 与 HTTP 入口

新增工具均由校园 MCP 服务暴露，仍通过官方 SDK Streamable HTTP 调用：

|工具|参数|行为|
|---|---|---|
|get_integration_status|无|报告配置状态，不声称连通性已验证|
|search_places|keywords, city, limit=5|返回多个候选；不默认选择同名地点|
|get_route|origin, destination, mode=walking|GCJ-02坐标，支持 walking / driving|
|get_weather_forecast|city_adcode, target_date=null|返回预报或 out_of_forecast_range|
|get_campus_location|campus_id|读取管理员显式配置的真实校门位置|
|list_external_calendar_events|start, end|ISO8601带时区，查询范围不超过31天|
|sync_plan_calendar|plan_id, plan_version, provider_binding, reminder_minutes, operation_id, user_id|写凭据限定，从服务端已保存计划读取内容|
|search_school_notices|query, campus_id=null|未连接占位|
|get_timetable|start_date, end_date|未连接占位|

主 Agent 能调用新增只读工具以及 `request_calendar_sync` 提议工具。模型工具列表不暴露实际写工具；规划服务只读凭据尝试同步日历会被校园服务拒绝。

主应用增加 `GET /v1/integrations`、`POST /v1/maps/places`、`POST /v1/maps/route`、`POST /v1/weather`、`GET /v1/calendar/external?start=...&end=...` 和 `POST /v1/calendar/propose-sync`。

```python
# 运行四服务后，从项目环境调用；无Key会真实返回not_configured。
from campus_agent.config import Settings
from campus_agent.protocols import MCPGateway

async def check():
    gateway = MCPGateway(Settings(mode='fixture'))
    return await gateway.call('get_weather_forecast', {'city_adcode': '110101'})
```

所有上游错误均转为结构化结果。日志轨迹隐藏精确路线坐标、搜索关键词和日历绑定标识；不记录高德 Key 或包含 Key 的错误 URL。用户主动提供的地点仍可能存在于本地会话历史中。

## 日历适配约定

`integrations/calendar.py` 定义 `CalendarProvider`，注册表默认空，`CALENDAR_PROVIDER=none`。后续在可信代码中实现并注册平台适配器，不允许模型传入任意服务地址或导入路径。

- `name` 与 `binding_id`：描述平台与账户/日历绑定；绑定标识不包含令牌。
- `list_events(start,end)`：返回完整重叠事件，平台实现必须展开重复事件、处理分页和时区；失败必须抛出异常，不能伪装为空列表。
- `upsert_event(event,stable_key,idempotency_key,existing_event_id)`：按稳定标识更新或创建；网络结果不确定时，须先查重再创建。认证、刷新令牌与提醒字段映射由具体适配器负责。

执行流程：保存本地计划 → 生成外部同步预览 → 用户确认操作 ID → 写凭据调用 MCP → 服务读取本地已保存快照、校验版本与目标绑定 → 检查重叠 → 逐项 upsert 并记录进度。未绑定平台时不生成虚假同步成功结果。本地保存不会自动触发同步。

`calendar-sync.sqlite` 存储幂等操作及每项远程事件映射。重复确认沿用原操作编号；已确认成功的事项在重试时跳过。外部平台不提供本地数据库事务，因此部分写入不能承诺整体回滚；界面报告已知成功项数。旧版本移除的远程事项不会自动删除，会列出保留项供检查。

当前按单个校园工具服务进程运行，同步使用进程内串行锁；不支持多实例分布式同步锁。外部日历在查询后仍可能变化，当前冲突检查不构成远程事务锁。任何后续真实平台适配都必须重新验证分页、重复事件、未知写入结果恢复及账户授权。

本地待办、日程和 ICS 导出不依赖上述平台。此版本不发送主动消息，不声称手机一定收到提醒。

## 是否需要学校合作

- 地图和天气通常只需服务商开发者配置与合法数据使用，不需要学校提供接口。
- 个人日历通常由用户授权相应平台，不需要学校参与；校园统一身份平台可能另有组织策略。
- 学校公开通知可在许可条件下使用官方公开页面或订阅源，不一定需要正式合作。
- 直接读取个人课表、迎新状态等非公开数据，通常需要学校或系统运营方提供授权接口。用户自行导入文件是另一条路径，但本版未实现。

## 验证口径

`tests/test_integrations.py` 使用 HTTP MockTransport 和仅存在于测试文件中的 FakeCalendar 验证适配边界。`tests/test_protocols.py` 启动独立 MCP/A2A 进程，验证新增工具发现、无配置错误、只读拒写与主助手天气调用。二者不能当作真实地图、天气、日历或真实模型能力实测。
