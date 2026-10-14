# 接口与数据约定

## CampusPilot 邀请体验新增约定

`AUTH_MODE=pilot` 下，除 `/health` 与登录入口外，API 和 Gradio 均要求有效登录。
浏览器使用 HttpOnly Cookie；API 客户端可沿用登录返回的 Cookie，或将该登录令牌放入 Bearer 头。
用户身份由服务端解析，`POST /v1/chat` 不接受客户端 `user_id`。

|接口|行为|
|---|---|
|GET /login、POST /login|浏览器登录表单，成功跳转工作台|
|POST /v1/auth/login|输入 username/password，成功设置 Cookie 并返回 303|
|POST /v1/auth/logout|撤销当前登录令牌并清除 Cookie|
|GET /v1/me|自己的会话列表、提交数及配置限额|
|POST /v1/feedback|输入 turn_id、helpful/unhelpful 与可选 comment；每账号每轮保存最后一次反馈|

访问别人的会话、轮次、计划、确认操作或规划任务统一返回 404；未登录返回 401。
个人待办、日历和 ICS 自动按登录身份过滤，忽略查询参数中自行传入的 user_id。
429 表示达到配置限额，带 Retry-After。运行中对话的并发控制只适用于当前单进程部署。

## 主应用

|接口|行为|
|---|---|
|POST /v1/chat|输入message及可选session_id；202返回turn_id|
|GET /v1/turns/{id}|轮询status、reply、citations、evidence、trace、usage、pending_actions、plan_id|
|GET /v1/sessions/{id}|恢复历史轮次|
|GET /v1/plans/{id}|读取服务端计划快照|
|GET /v1/actions/{id}|读取待确认操作及真实执行结果|
|POST /v1/actions/{id}/confirm|无请求体，执行已保存快照|
|POST /v1/actions/{id}/reject|拒绝未执行操作|
|POST /v1/todos/propose-update|提交session_id、todo_id、status、expected_version，生成确认操作|
|POST /v1/planning-tasks/{id}/cancel|将取消传递给A2A服务|
|GET /v1/todos|MCP查询待办|
|GET /v1/calendar|MCP查询日程|
|GET /v1/calendar.ics|MCP生成并下载日历|
|GET /v1/evidence/{id}|按允许的证据ID查看原文|
|GET /v1/sources|入库来源、年份、快照日期、哈希、资料缺口与版本说明|
|GET /health|依赖可用性、模式、演示日期|

轮次状态：queued、working、completed、failed、interrupted。completed表示本轮交互完成，可能是追问或规划失败的解释，不能直接当成整个业务任务成功。业务成功需检查计划、A2A状态及确认结果。

工具结果统一为 `{"ok":true,"data":...,"error":null}` 或 `{"ok":false,"data":null,"error":{"code":...,"message":...}}`。错误类型包含forbidden、not_found、invalid_arguments、conflict、unavailable。

## MCP

RAG服务 `/mcp` 提供 search_knowledge，参数包括query、campus_id、as_of_date、top_k和可选school_id、student_type、cohort_year。校园服务提供get_service_info、get_travel_times、list_todos、list_calendar_events、apply_plan、update_todo_status、export_calendar。输入Schema来自官方tools/list，转为模型原生函数Schema。真实资料中的空时段和空通行时间表示未知，不采用合成默认值。

调用过程由官方SDK完成 initialize → tools/list或tools/call，传输为Streamable HTTP。使用Bearer演示凭据；读写授权在服务端执行。规划Agent及模型工具循环只有只读网关，确认操作使用独立写网关。

## A2A

新增地图、天气、外部日历和学校占位工具及 HTTP 入口见 [EXTERNAL_TOOLS.md](EXTERNAL_TOOLS.md)。同步日历沿用确认接口，模型只能提出操作，真实写入需写凭据。`calendar-sync.sqlite` 管理外部同步进度及远程事件映射。

Agent Card 位于 `/.well-known/agent-card.json`；JSON-RPC端点为Agent Card声明的配置URL。官方SDK的 `JsonRpcTransport` 执行 message/send、tasks/get、tasks/cancel。

输入通过Message的DataPart携带PlanRequest，使用 `blocking=false`。补充信息时携带原taskId和contextId。任务完成后artifact的DataPart包含plan、trace、usage及model_mode。提交、处理中、input-required、completed、failed和canceled均使用SDK定义。

主助手验证Agent Card URL与固定配置一致，不接受任意外部地址。SDK任务对象原样持久化在规划数据库。用户修改已完成计划时应生成新A2A任务，使用原plan_id与递增version；本地确认入口校验当前版本。

## 数据所有权与故障

- assistant.sqlite：sessions、turns、plans、actions、task_owners。
- planner.sqlite：官方A2A任务、规划输入、计划及故障记录。
- campus.sqlite：todos、events、plans、operations。
- 每个SQLite连接独立，WAL模式；保存计划和幂等记录在同一BEGIN IMMEDIATE事务提交。
- 重复操作编号+相同内容返回原结果，不同内容返回冲突。
- 同一计划新版本保存会替换该计划的旧日程，不影响其他计划；执行前重做冲突检查。
- 取消运行中任务停止协程；取消等待输入任务持久化canceled。
- 主助手轮询上限185秒；规划主动执行限180秒，等待用户输入不占用。

demo配置按demo-user本地演示；pilot配置采用邀请登录、身份注入、归属检查和跨站写请求限制。内部MCP/A2A依赖私有服务凭据及受控网络，不属于独立多租户授权系统。公网部署、数据库迁移和数据保留仍需按部署环境落实。

v0.3.0的PlanRequest增加student_type、cohort_year、funding_route及reminders。服务端设置reference_date。Plan增加data_profile，PlanItem支持checklist/personal_reminder，start/end可为空，due_date和timing_note用于截止节点与未知项。checklist只保存待办；personal_reminder保存待办和明确的个人日历提醒。参见WHUT_DATA.md。
