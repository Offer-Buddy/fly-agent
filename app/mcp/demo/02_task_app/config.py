"""③ 配置 —— 一屏看完，真实项目里这些从 .env 读。

对应课件第四节。
"""

# 演示账号：真实项目查数据库，这里用字典省掉一层
USERS = {"alice": "alice-pwd", "bob": "bob-pwd"}

# JWT：密钥写成常量只够演示，上线必须走环境变量
SECRET_KEY = "dev-only-secret-change-me-32-chars"
ALGORITHM = "HS256"
EXPIRE_MINUTES = 30

# 限流：10 秒滑动窗口内允许几次请求
# MCP 比 REST 宽 10 倍，原因见 ⑤ security.py 结尾的说明
WINDOW = 10.0
QUOTA = {"/api": 6, "/mcp": 60}
