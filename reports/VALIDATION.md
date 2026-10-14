# 验证报告

执行日期：2026-09-21。环境：Windows，CPython 3.11.16。实际依赖见uv.lock，MCP 1.30.0，A2A SDK 0.3.26，Gradio 5.50.0，LangGraph 1.2.11。

## 已完成验证

|项目|结果|证据|
|---|---|---|
|单元与真实协议集成测试|扩展相关测试后的首次记录 57项通过（本次运行67.07秒）；修复本地回环代理劫持后复跑 57项通过；修复规划服务重启崩溃并新增1条回归测试后复跑 58项通过；补充单服务故障降级2条测试后复跑 60项通过、0失败0错误、130.16秒|tests-after-proxy-fix.xml（57项）、tests-after-planner-fix.xml（58项）、tests-after-degradation.xml（60项）、cleanroom-tests.xml（60项）|
|语法与静态检查|compileall与ruff通过|执行命令见README|
|排程开发集|30/30通过|evaluation.json逐条计划及错误列表|
|模型写工具越权|主助手工具白名单拦截；规划只读凭据在服务端被拒绝|test_agent_guards、test_mcp_discovery_auth_and_readonly|
|A2A完整生命周期|发现、提交、补充信息、同任务继续、artifact、查询、取消均验证|test_protocols|
|保存与恢复|确认前不写入、重复确认无重复、旧版本拒绝、重启保留已保存事项|test_core、test_protocols|
|日历导出|标准icalendar解析器可读，时间以UTC保存避免固定偏移TZID兼容问题|test_atomic_idempotent_save_and_ics|
|既有计划保存页面（此前验证）|实际输入需求，显示计划和材料清单，点击确认后返回4项待办、4项日程保存成功|此前浏览器操作验证|
|新增外部工具界面与本地API|UI页面HTTP 200；Gradio客户端实际调用连接状态和非法路线回调；主助手经MCP查询状态|integrations-smoke.json；本次浏览器自动化不可用，未完成新版视觉检查|
|地图与天气适配|坐标、地点歧义、日期边界、鉴权/限额及畸形响应均有断言；无Key明确返回未配置|test_integrations；成功响应为HTTP MockTransport测试替身|
|日历接口与确认|幂等、重启恢复、部分失败续传、未知写结果查重契约、版本/账户/冲突检查；确认前不写、旧确认拒绝|test_integrations；具体平台为测试专用FakeCalendar|
|MCP新增工具权限|真实子进程发现新增工具，未连接错误可观察，只读同步请求被拒绝|test_protocols|
|Docker Compose|配置语法校验通过|docker compose config --quiet|
|单服务故障降级|规划 Agent 停机时应用健康状态转为 `degraded`（rag/campus 仍 true、planner false），知识问答与事项查询照常完成，排程请求返回可读失败话术而非中断；重启规划 Agent 后依赖恢复、排程正常|test_planner_down_keeps_chat_and_queries_working、test_planner_down_planning_request_fails_readably；真实模型下的端到端观察见正文「单服务故障降级实测」|
|净室复现（交付包独立验证）|交付包解到全新目录（无 venv/env/数据）后：依赖可复现、测试 60/60 通过、自动播种、四服务就绪、演示流程含幂等全部跑通|cleanroom-tests.xml、cleanroom-fixture-smoke.json|

所有协议集成测试都启动独立子进程，并通过官方SDK的网络传输调用；不是进程内模拟协议。fixture仅替代模型决策，数据库、协议、检索、排程和确认操作都实际执行。

**一处证据管理更正**：`reports/tests.xml` 因与复跑命令 `--junitxml=reports/tests.xml` 同名，已被后续运行覆盖，现内容为 57 项规模的复跑结果（与 `tests-after-proxy-fix.xml` 内容一致）；扩展前的更小规模运行（20 项）记录保留在 `runtime/test-results.xml`。此前文档中"28 项记录保留在 `reports/tests.xml`"的表述不成立，已更正——该规模运行的原始 XML 不再存在，其数字不再作为独立证据引用。为避免同类覆盖，README 的复跑命令已改为输出 `reports/tests-local.xml`。

## 单服务故障降级实测

在真实模型（DeepSeek）下逐个停掉服务，观察应用侧的降级行为。停掉规划 Agent（8020）后，应用 `/health` 返回 `status: degraded`，`dependencies` 为 `rag: true, campus: true, planner: false` —— 应用自身保持可用，只是标记依赖降级。此时：

- **知识问答照常完成**：走 RAG MCP 服务，不经过规划服务；返回带稳定引用编号的答案。
- **事项查询照常完成**：走校园事务 MCP 服务，正确列出既有待办。
- **排程请求按设计失败但可读**：返回「规划服务调用失败（A2AClientHTTPError）；仍可咨询规定或查询已有事项。」会话状态为 `completed`（正常回合结束），不是中断或 500，也没有生成或写入任何计划。

重新启动规划 Agent 后，依赖状态恢复为全 `true`，同一排程请求正常产出计划。**需要注意的观测细节**：应用侧在调用规划服务前会先跑一轮 MCP 前置查询（服务信息、通行时间、已有日程与待办），这几步在规划服务不可用时仍然会执行，属于可继续优化的浪费——更理想的做法是在委派前先探测规划服务可用性。

另有一条易误判的观察：真实模型下"周六14:00 到达"的请求曾返回**零安排**计划（所有事项均列为未安排）。核查后确认这不是缺陷：`campus.sqlite` 中已有 09-05 14:15–14:45 等既有日程，恰好占满当天可用窗口，排程器拒绝重复占用是「已有日程」硬约束的正确表现。同一请求在后续运行中产出 09-07（周一）顺延计划，正是绕开这些既定日程的结果。反过来说明：模型侧的条件抽取（如把"周六下午完成"转成硬性空闲窗口）会改变可安排结果，这是模型非确定性带来的方差，属于没有标注评测集时不能量化、也不应写进简历的部分。

## 实测对照

确定性测试模式下，评测专用本地规划流程与A2A规划流程生成同一份安排。单次耗时分别为563.54ms与747.02ms。A2A路径包含网络传输和轮询；样本量不足以证明稳定性能差异，更不能据此宣称准确率提升。

针对有安排结果的20条排程用例，向第一项安排注入凌晨时间错误，确定性校验全部发现；关闭校验将放行这些候选。另10条用例本来就没有可安排事项，不计入注入错误检出率。这是故障注入实验，不是模型错误率实验。

30项任务场景清单复用原基础28个自动测试中的不同断言，不能将其作为30次额外独立模型测试。旧评测集按文件审计并保留；旧标签与新知识库不一致，因此没有迁移旧RAGAS分数。当前60项测试（基础回归与外部工具验证，加1项规划服务重启崩溃回归测试、2项单服务故障降级测试）未重新计量旧消融实验的耗时。

## 本次修复：系统代理劫持本地回环调用

现象：在配置了系统代理（HTTP_PROXY）的环境下启动全栈后，所有 MCP 工具调用返回 `unavailable`，冒烟脚本首轮即因响应缺少 `session_id` 失败。

定位：同一 httpx 连接连发两次请求，第一次 202、第二次 404；先 GET `/health` 再 POST 同样 404；`trust_env=False` 或 `NO_PROXY=127.0.0.1,localhost` 后全部正常。根因是 httpx 默认读取环境代理变量，把应用访问 `127.0.0.1:8030/8040`（MCP）与 `:8020`（A2A）的请求送进代理，连接复用后请求路径异常。

修复：`config.py` 新增 `_bypass_proxy_for_loopback()`，把 `127.0.0.1,localhost,::1` 并入 `NO_PROXY`（与既有值取并集，不覆盖），一处覆盖 MCP/A2A/健康探针及官方 SDK 内部客户端；`scripts/smoke.py`、`scripts/check_integrations.py` 的本地客户端显式 `trust_env=False`。外部模型请求仍按环境变量走代理。修复后不设额外环境变量从零启动，链路全部通过，57项测试无回归。

## 本次修复：规划服务重启时对中断任务的恢复崩溃

现象：应用侧中断恢复测试跑完后，规划 Agent（8020 端口）再也无法启动；进程退出，日志结尾为 `TypeError: 'NoneType' object does not support item assignment`，指向 `planner.py` 的 `SQLiteTaskStore.__init__`。全栈启动时该端口持续不可用，应用 `/health` 显示 `planner: false`。

定位：`SQLiteTaskStore.__init__` 在启动时扫描残留任务，把 `submitted`/`working` 状态就地改为 `failed`，再写 `value.setdefault('metadata', {})['interrupted'] = True`。但 A2A SDK 序列化 `Task` 时，未设置的 `metadata` 会落库为 JSON `null` —— 键**存在**、值为 `None`。`setdefault` 只在键缺失时兜底，键存在则原样返回 `None`，于是对 `None` 做下标赋值抛 `TypeError`。审计 `planner.sqlite` 确认 7 条任务全部是 `"metadata": null`，其中 1 条状态仍为 `working`（前一次中断测试的残留），因此触发该分支。

影响：**只要有任意一次规划任务在 `working` 状态被打断，规划 Agent 就永久无法启动**，且每次都崩（残留数据不会被清理）。这是"恢复代码本身成为故障源"的自伤型缺陷；应用侧只降级为 `planner: false`，不会报错，因此容易长期潜伏。

修复：改为显式合并 —— `value['metadata'] = {**(value.get('metadata') or {}), 'interrupted': True}`。该写法与 `assistant.py` 中既有的 `or {}` 防御风格一致，说明原写法属遗漏而非设计。

回归测试：新增 `test_planner_restart_survives_in_flight_task_with_null_metadata`，构造 `metadata: None` 的 `working` 任务后重启规划服务，断言状态转为 `failed` 且写入 `interrupted` 标记。已做反证：还原缺陷写法后该测试稳定失败并复现同一 `TypeError`，修复后通过。此前的 `test_interrupted_turn_is_not_silently_reexecuted` 只覆盖应用侧，规划侧无对应测试，是该缺陷漏网的原因。

## 净室复现（交付包独立验证）

上述验证都在开发工作目录中进行。为排除"本机环境有特殊配置"的可能，又做了一次净室复现：把交付压缩包解到一个全新目录（**无 `.venv`、无 `.env`、无 `runtime/` 数据、无任何预热缓存**），完全按 README 的步骤从零开始。

| 步骤 | 命令 | 结果 |
|---|---|---|
| 建环境 | `uv sync --frozen` | 成功，依赖按锁文件安装 |
| 静态检查 | `python -m compileall -q src` / `ruff check src` | 通过，`All checks passed!` |
| 全套测试 | `pytest -q` | **60 passed / 0 failed / 0 error / 200.7 秒**，见 `cleanroom-tests.xml` |
| 数据播种 | 无需手工执行 | `load_seed` 在 `runtime/data/campus.json` 缺失时自动生成 |
| 启动 | `campus all --fixture` | 四个服务全部就绪，`/health` 返回 `status: ok`，依赖全 `true`，`model_mode: fixture` |
| UI 与文档页 | `GET /ui/`、`GET /docs` | HTTP 200 |
| 演示流程重放 | `scripts/smoke.py --save` | 三轮对话全部 `completed`；走完 `submitted → input-required → completed` 生命周期，artifact 交付 3482ms |

其中确认落库的幂等性经数据库核对，而不只是看接口回显：

```
确认一次   →  todos_created: 4, events_created: 4, plan_id: eb5b4757…
重复确认   →  plan_id 相同
数据库实况 →  todos 4 条、events 4 条、plans 1 个、operations 1 条
```

即重复确认没有产生任何多余记录。证据文件：`cleanroom-tests.xml`、`cleanroom-fixture-smoke.json`。

这意味着交付包本身是自洽的：不依赖开发者的机器状态、不需要手工播种、`uv.lock` 能完整还原依赖。真实模型模式不在净室中验证（需要密钥），但 `.env.example` 与预检脚本 `scripts/check_live.py` 已随包提供。

## 尚未验证

- **高德真实调用：**当前未配置有效AMAP_API_KEY，没有真实地点、路线或天气成功调用记录；测试替身成功不代表服务商实测成功。
- **外部日历与学校系统：**按用户要求，日历只实现平台无关适配接口，没有绑定账户；学校通知和课表只预留接口。没有真实日历同步、提醒送达、通知或课表读取记录。
- **新增工具的排程联动：**地图路线、天气和外部忙闲查询目前为主助手查询能力，不会自动注入A2A排程；校内通行数据仍为合成数据。

- **真实模型（已部分完成）：**已用 DeepSeek `deepseek-chat` 完成端到端验证（2026-09-21）：预检返回原生 `tool_calls`；三轮对话、A2A 计划交付（artifact 9753ms）、确认落库（3待办+3日程）、重复确认幂等返回同一 plan_id 全部成功，见 `runtime/live-preflight.json` 与 `reports/live-smoke-deepseek.json`。**仍未验证：**标注评测集下的人工事实支持率与模型准确率；Qwen 路径（原 qwen3.5-flash 预检 AuthenticationError 记录保留）；仅单模型单路径，不构成模型选型结论。
- **Milvus/BGE：**代码与可选依赖保留，但没有可用BGE模型路径，Docker引擎未运行；未验证加载、向量入库、召回和重排。
- **容器运行：**Docker Compose通过配置校验，Docker daemon不可用，未构建镜像或跑容器测试。
- **真实业务与负载：**没有真实高校数据、真实用户、并发压测或生产部署。

这些限制需要在项目经历中保留；测试替身数据不得转写成真实模型效果。
