# 发布检查 / Publication checks

Date: 2026-10-18 (Asia/Shanghai)

This publication copy starts from the existing remote main commit `e5670d6fdd421c7a44281b432fe5952beba58e88` and incorporates implementation from local `bf564db0f9ed01de030589003abb92097a7bced5`. Remote history, bilingual documentation, historical published evidence, source attribution, and license status are preserved. No new repository-wide license is assigned.

| 本轮检查 / Current check | 结果 / Result |
| --- | --- |
| Offline regression | 162 passed, 0 failures/errors/skips; 169.40 seconds |
| Ruff | `src tests scripts`: passed |
| Four-process fixture smoke | Official-data profile, 2026-10-18 reference date, two generated test accounts; QA, A2A clarification, confirmation, duplicate-save protection and ownership isolation passed |
| Paid model calls | 0 |
| Sensitive artifact check | No provider key/private key/concrete tunnel URL/personal local path detected; no private runtime/configuration artifact in publication candidates |
| Diff formatting | Passed |

本轮使用原项目虚拟环境中的依赖，但工作目录和 `PYTHONPATH` 均指向发布副本；已验证导入位置。fixture、独立临时目录、不存在的专用 env 路径和清空模型密钥用于隔离真实服务。原始回归 XML 与冒烟报告仅保留本机，新公开[机器摘要](PUBLICATION_2026-10-18.json)不包含账号、会话、Token凭据或本地绝对路径。

Source-workspace reports describe earlier live DeepSeek developer checks, temporary HTTPS access and test-account preparation. This publication did not repeat paid calls or redeploy that tunnel. These historical checks are separate from the fresh offline results and do not establish independent model accuracy, student adoption, university-system connectivity, permanent hosting, or 50–70 participant capacity. The timetable and housing code defines read-only contracts and defaults to not connected. The student count remains a staged rollout target.

Not rerun: paid models, public HTTPS/tunnel access, university APIs/SSO, real student trials, Docker containers, Milvus/BGE, and live maps/weather. Raw private reports, environment files, runtime databases, filled interviews, caches and handoff notes are excluded. The README documents a generic operator-owned HTTPS setup instead of a private bootstrap or temporary live address.

---

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
