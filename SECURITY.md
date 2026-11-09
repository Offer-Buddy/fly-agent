# 安全策略

## 支持的版本

本仓库为模板仓库。安全修复仅应用于 `master` 分支。Fork 的维护者需自行负责保持其 fork 与上游同步。

## 报告漏洞

请**不要**为安全漏洞提交公开的 GitHub issue。

请通过 [GitHub Security Advisories](../../security/advisories/new) 私下报告，或直接向维护者发送邮件。报告中请包含：

- 漏洞描述及其潜在影响
- 复现步骤
- 任何建议的缓解措施

对于已确认的漏洞，你可在 48 小时内收到确认回复，并在 7 天内收到修复或缓解方案。

## 使用本模板时的安全注意事项

**部署到生产环境之前：**

- 设置强度足够的随机 `JWT_SECRET_KEY`（32 位以上字符）
- 轮换全部密钥——禁止沿用 `.env.example` 中的取值
- 设置 `DEBUG=false`
- 将 `ALLOWED_ORIGINS` 限制为实际的前端域名
- 如不希望将对话数据发送到 Langfuse，设置 `LANGFUSE_TRACING_ENABLED=false`
- 使用区分环境的 `.env` 文件——禁止将密钥提交到 git

**本模板为你提供的防护措施：**

- 密码使用 bcrypt 哈希（不存储明文）
- JWT token 包含 `jti` claim 以保证唯一性
- 所有用户输入在使用前均经过净化处理
- 认证与聊天端点已启用限流
- 通过 `detect-secrets` pre-commit hook 进行密钥检测
- 已配置 CORS（生产环境须限制 `ALLOWED_ORIGINS`）
