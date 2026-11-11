"""slowapi 限流示例集合。

每个模块都是一个**可独立启动**的最小应用，互不依赖，也不 import ``app.core.*``
（避免被主项目的 JWT / 数据库 / 环境配置拖住）。

运行方式见同目录 ``README.md``，统一用 ``uv run python -m app.api.demo.slowapi.<模块名>``。
"""
