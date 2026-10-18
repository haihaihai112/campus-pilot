# 本地运营与验证

所有命令在项目根目录用PowerShell 7（pwsh）执行。沿用现有.venv，不输出密钥。每次使用新的报告和临时目录，避免覆盖历史证据。

## 账号与统计

沿用campus init-pilot创建邀请配置、campus accounts add创建账号。登记分类后新开会话，分类不会追溯修改旧会话或旧轮次。开发者/test/historical_demo分别登记，不混入participant/current_need。

```powershell
& .\.venv\Scripts\campus.exe enroll --username student01 --cohort participant --purpose current_need
& .\.venv\Scripts\campus.exe stats
& .\.venv\Scripts\campus.exe review-turn --turn-id '实际轮次编号' --outcome resolved
```

resolved是组织者依据具体任务完成标准进行的人工复核，须实际查看结果；不是独立标注、用户反馈或自动正确率。追问、失败和中断不可标成resolved。若任务目标是保存，仅预览不算完成，必须先核对成功写入。

adoption仅计WHUT配置、live、已登记participant/current_need且非演示身份的轮次；账号开通数另列。成功写入按distinct action ID计数，重复确认不累加。保存只是项目内待办/个人提醒，不是校方事务办结。reported_tokens是兼容的供应商已报告Token合计，缺失usage不代表免费，费用看逐调用明细。

workflow_outcome区分response_generated、needs_input、preview_ready、action_completed、action_rejected、tool_failed、guard_rejected、budget_exhausted、failed和interrupted。action_completed表示确认操作成功，重复确认仍按同一action ID统计；action_rejected表示明确拒绝。status=completed只表示轮次处理结束。queue_wait_ms与处理latency_ms分开，total_latency_ms包含等待；旧数据缺字段不补猜。分类在会话创建时固定，operator登记日志与复核日志保存在本地数据库。

## 首轮真实调用与费用

本轮工具不发起真实模型调用。先确认供应商、endpoint、模型、有效价格及预算，再做5条案例。Model逐调用记录请求/响应模型、endpoint主机、usage是否返回、Token、耗时及错误类型，不保存提示词或密钥。SDK隐式重试关闭，防止成功重试掩盖未知计费；失败由组织者核对再决定是否重试。

```powershell
& .\.venv\Scripts\python.exe scripts/pilot_readiness.py export-usage --runtime runtime-whut-2026 --turn-id '实际轮次编号' --output reports/usage-new.json
```

导出标记sample_unit=turn。按同一实际任务将追问、继续和失败尝试合并到一个tasks元素，保留所有model_calls与incomplete标记，再把sample_unit改为task。不得删除失败调用以压低费用。案例/任务筛选与分组依据另存本地，导出本身不带账号或对话正文。

复制pricing-template.json到被忽略的runtime下填写：currency、verified_at、source_url、endpoint_host、响应中实际模型名、每百万输入/输出Token价格。价格从供应商有效页面人工核对，缓存命中/阶梯收费无法拆分时先用保守上界，明确不是账单精算。模板空值会阻止估算，不能填未经验证的数字。

```powershell
& .\.venv\Scripts\python.exe scripts/pilot_readiness.py budget --sample runtime-whut-2026/task-sample.json --pricing runtime-whut-2026/pricing.json --participants 70 --tasks-per-person 2 --server-cost 0 --output reports/budget-new.json
```

上述70、2、0仅演示参数，0服务器费不能当实际报价。应填试用窗口预期任务数与已取得的同币种服务器报价，再分别估5、20、70人。费用=实测平均单任务模型费用×预计任务数+窗口服务器费用；还需检查税费、网络/域名等支出和额度余量。没有样本、未报告usage、实际模型未匹配价格或失败调用时，完整预测为null，已定价小计仅供参考。先确认总预算，再发生支出。

## 无模型费用的集中提交检查

```powershell
& .\.venv\Scripts\python.exe scripts/pilot_readiness.py burst --runtime work/burst-five-new --users 5 --output reports/burst-five-new.json
& .\.venv\Scripts\python.exe scripts/pilot_readiness.py burst --runtime work/burst-ten-new --users 10 --output reports/burst-ten-new.json
& .\.venv\Scripts\python.exe scripts/pilot_readiness.py burst --runtime work/burst-overflow-new --users 10 --pending-limit 3 --output reports/burst-overflow-new.json
```

脚本使用真实ASGI登录、身份和chat限额逻辑及延迟本地模型桩，账号归test；不调用模型或内部网络工具。记录接收、完成、429/Retry-After、排队和最大活动桩数。它只验证进程内机制，不证明socket、HTTPS、浏览器、MCP/A2A、真实模型或服务器承载能力；默认100ms不是实际模型耗时。四服务协议单独用现有verify_pilot.py验证。

## 备份与恢复

由组织者先停止四服务。--services-stopped是操作声明，脚本不会检查或强行关闭未知进程。SQLite backup API包含已提交WAL，但跨服务一致性依赖确已停机。备份允许的5个SQLite和data下JSON/Markdown/TXT，不含.env、日志、访谈记录或自定义连接配置；敏感配置与访谈需另行受控保存，备份自身含账号/个人事项，不提交Git。

```powershell
& .\.venv\Scripts\python.exe scripts/pilot_readiness.py backup --runtime runtime-whut-2026 --destination work/backup-new --services-stopped --output reports/backup-new.json
& .\.venv\Scripts\python.exe scripts/pilot_readiness.py restore --backup work/backup-new --destination work/restored-new --output reports/restore-new.json
```

哈希与quick_check通过不等于业务恢复完成。恢复到新目录后人工核对账号、个人事项、归属、已确认操作幂等及登录；准备好独立配置再切换CAMPUS_RUNTIME。Compose独立卷不能直接套用单目录命令，应停各服务、逐卷备份并记录同一停机窗口；容器及远程恢复仍待实际验证。

## 远程部署前

保留单进程主应用与三个内部服务，禁止直接加多个worker。先选择资源并确认报价；HTTPS反向代理、COOKIE_SECURE=true、私有服务凭据、内部端口不公网开放、受控备份与恢复均需真实验证。复测5/10人集中提交和少量真实端到端调用，根据等待、限额和预算调整；本地模拟结果不能直接指定服务器容量。反馈入口沿用现有页面/API，不新增公开统计或运营API。
