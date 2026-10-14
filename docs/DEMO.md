# 演示与复现

## 零密钥演示

使用独立运行目录以免已有日历改变演示计划：

```powershell
$env:CAMPUS_RUNTIME = "$PWD\runtime\fresh-demo"
uv run --no-sync campus all --fixture
```

另一个终端执行：

```powershell
uv run --no-sync python scripts/smoke.py --save --output reports/my-smoke.json
```

预期：第一轮问答只有MCP检索；第二轮A2A进入input-required；第三轮沿用taskId补充条件，接收计划artifact。确认前不会创建新待办，重复确认返回相同操作结果。

界面验证步骤：打开`/ui/`，发送“周六下午两点到明德校区，还没缴费，帮我安排报到和入住”；查看计划后点击“确认执行本轮操作”；切到个人事项并刷新；选择事项标记完成；导出日历。运行详情展示真实工具与任务状态，不展示内部思维链。

## 真实模型验证

1. 配置有效的兼容模型凭据，在本地`.env`或环境变量中保存，不提交到仓库。
2. `uv run --no-sync python scripts/check_live.py`，确认返回`ok: true`和原生工具名ping。
3. `MODEL_MODE=live`启动四服务，使用新的运行目录。
4. 重跑smoke与上述界面流程，保存到独立的`reports/live-smoke.json`。
5. 检查工具参数、事实引用、计划约束与写入结果。不能只看模型是否给出流畅回答。

本次已有预检记录为AuthenticationError，因此步骤3—5没有通过，尚无真实模型成功率。

## 两个消融实验的解释

- 确定性校验：向有效候选计划注入凌晨办理等错误，比较有校验与关闭校验时是否放行。它测量防线，不测量模型生成错误的概率。
- A2A：评测专用代码运行同一规划流程的本地路径与远程A2A路径。生产主助手始终通过A2A调用；本地路径仅用于对照。单次测量不能构成稳定性能结论。

## 排查

- `/health`显示degraded：查看相应服务的`/health`与端口配置。
- 计划失败：检查规划服务运行目录的planner.sqlite中errors记录；对外只返回错误类型，避免泄露服务细节。
- 默认端口被占用：使用`--base-port 8110`并修改访问地址。
- 计划被顺延：先检查已保存日程，演示数据默认周日财务、报到和校园卡窗口关闭。
- 真实模型AuthenticationError：修正本人有效凭据，不要通过fixture模式掩盖该问题。
