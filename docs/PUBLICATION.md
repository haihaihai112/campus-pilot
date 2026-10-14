# 发布检查 / Publication checks

Date: 2026-10-14 (Asia/Shanghai)

| 检查 / Check | 结果 / Result |
| --- | --- |
| Environment | Python 3.11 · uv frozen lock; dependencies installed in an isolated publication copy |
| Existing tests | 104 passed |
| Static checks | Ruff: src tests scripts; passed |
| Smoke / reproduction | WHUT fixture, four HTTP processes, two accounts, confirmation/idempotency and identity isolation passed; no paid model calls. |

本次仅整理发布副本中的文档、忽略规则和可移植验证材料，未修改业务逻辑。历史验证报告保留原始条件和失败记录；其中的成绩不代表当前真实模型效果或生产能力。原始本机日志及旧会话数据库不发布；部分历史 XML 中的本机路径已替换为 `[local-path]`。

This publication changes documentation, ignore rules and portable evidence only. Business logic is unchanged. Historical reports retain their original conditions and failure records. Their metrics do not establish current live model quality or production readiness. Local logs and old session databases are excluded; local paths in selected historical XML are redacted.

尚未在本次发布中验证付费模型调用、公网部署或外部数据库联调。Third-party license files and source attribution are preserved; no new project license was assigned.
