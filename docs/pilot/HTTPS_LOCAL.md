# 本机 HTTPS 邀请入口

2026-10-18，按用户授权完成本机主站＋localhost.run 免费 SSH 隧道。无需购买域名或云主机；真实模型沿用本机 DeepSeek 配置。此入口是临时试用部署，不是云主机部署或可用性承诺。

## 当前启动与停止

在项目根目录，通过 PowerShell 7 执行：

```powershell
pwsh -NoProfile -File ./scripts/start-https.ps1
```

上述公开复现命令读取自行创建的 `.env.pilot`；先按 README 初始化，配置邀请账号、模型和三个私有服务凭据。启动前检查四个端口未占用，已有服务时不要重复启动。可选 `-BootstrapScript` 仅供运营者自己的本地配置，不包含在仓库中。

当前地址与进程记录位于被 Git 忽略的 `work/https-current.json`。只读取地址：

```powershell
(Get-Content ./work/https-current.json -Raw | ConvertFrom-Json).url
```

把该地址后加 `/login` 发给体验者，通过其他私下渠道分别分发独立账号密码。既有账号及新增26个独立账号均登记为测试用途；账号准备数不代表实际参与人数，不开放注册。新批次完成26个密码哈希与分类核对、2个公网登录抽查，账密仅存被Git忽略的运行目录。

停止公网入口及该入口启动的四个服务：

```powershell
pwsh -NoProfile -File ./scripts/stop-https.ps1
```

停止脚本核对 PID 与创建时间，只停止记录的隧道和应用进程树；保留数据库、备份和日志。先等正在处理的任务结束再停机。启动异常也先运行停止脚本，再看日志修复后重启。不自动断线重连，以免地址变化后继续公布旧地址。

## 运行条件和边界

- 电脑保持开机、联网、不休眠，服务与 SSH 进程保持运行。关机、睡眠、断网或隧道断开均会中断访问。
- 免费域名会变化，且服务商限速；本次曾在约十几分钟内观察到换址，旧地址返回502，而本机服务仍正常，不应承诺可使用数小时。首次加载Gradio页面可能较慢。失效后停止并重启，重新读取并分发地址。当前未提供固定域名、全天在线或大陆各网络可达性保证，应先请少量同学用手机移动网络核对。
- 必须分享完整 HTTPS 地址。服务商同时提供 HTTP；项目登录表单固定提交到 HTTPS，Cookie 为 Secure/HttpOnly/SameSite=Strict，HTTPS 响应附 HSTS。未声称服务商 HTTP 入口已关闭或提供自动重定向。
- 主站与三个内部服务均监听回环地址，只转发8010。内部 MCP/A2A 凭据不发布；该隧道提供商负责 TLS 终止，因此会处理站点流量。私人配置、对话原文和日志仍只留本地。
- 隧道实测缺失或错误报告 HTTPS 转发头，因此 `CAMPUS_PUBLIC_ORIGIN` 固定允许的 HTTPS 来源及 Host，拒绝其他 Host；保留 Origin 和 Sec-Fetch-Site 跨站检查。回环健康探针例外。该设置只适用于受控 TLS 终止代理，不能用于裸 HTTP 公网服务。域名变化需要重新启动；本机普通 HTTP 登录入口不再用于体验。
- 默认每账号每小时20次提交、最多2个待处理请求，全局4个处理、32个等待。日/周 Token 硬额度和总费用封顶尚未实现，小时限流不能保证总费封顶。累计70人目标仍需分批实测，不能当作已测并发能力。

## 验证与日志

2026-10-18全量162项测试通过，Ruff通过。公网实测：有效证书 HTTPS 登录页、邀请账号登录、Secure Cookie、未登录个人接口401、跨账号会话404、跨站写入403；浏览器工作台通过 Gradio SSE 完成“查看我的待办”，实际调用内部 MCP，新增模型调用0、报告 Token0。此开发者验证不计真实学生使用或模型质量评测。

启停脚本、私有运行目录和5个 SQLite备份已保留。日志为每次新建的 `work/https-日期时间/`，其中包含 `app.stdout.log`、`app.stderr.log`、`tunnel.stdout.log`、`tunnel.stderr.log`；当前验证结果另存 `remote-checks.json`。完整测试报告为 `reports/tests-https-20261018.xml`，不提交原始记录。截图在 `reports/https-20261018/`。

官方说明：[HTTP代理](https://localhost.run/docs/http-tunnels/)、[免费计划](https://localhost.run/docs/forever-free/)、[连接与域名变化](https://localhost.run/docs/faq/)。本轮没有采购、推送 Git、联系学生或新增付费模型调用。
