# demo：讲清「为什么这么写」的最小工程

这里放**教学用的独立 demo**，不是产品代码。每个子目录都能单独启动、单独删掉，
不依赖数据库、Redis、环境变量，也不参与 `app.main` 的路由挂载。

| 子目录 | 回答什么问题 | 入口命令 |
| --- | --- | --- |
| `jwt/` | 一个接口该不该验令牌、在哪一层决定、漏挂了怎么查出来 | `python -m app.api.demo.jwt.app`（8100） |
| `slowapi/` | 限流怎么配，九种写法各自解决什么问题 | `python -m app.api.demo.slowapi.01_minimal`（8001） |

## 三条约定

1. **生产代码不 import 这里**。反过来也尽量不依赖生产模块——目前只有 `jwt/` 借用了
   `app.utils.auth` 的签发与校验函数（就是仓库里真实跑的那两个），其余全部自包含。
2. **端口避开 8000**。每个 demo 占一个固定端口，可以同时开多个对照着看。
3. **一个子目录一个主题**。新增 demo 就在同级建目录，回来在这张表里加一行。

## 与生产代码的对应关系

| 想弄清的事 | demo 里看 | 真实实现在 |
| --- | --- | --- |
| 鉴权依赖怎么写、三档强度怎么分 | `jwt/deps.py` | `app/api/v1/auth.py` |
| 路由怎么挂载、能不能在端点取消 | `jwt/routers.py` | `app/api/v1/chatbot.py` |
| 漏挂依赖怎么自动查出来 | `jwt/audit.py` | ——（CI 用，可直接接） |
| 限流装饰器怎么加、键怎么选 | `slowapi/01_minimal.py`～`05_custom_key.py` | `app/core/limiter.py` |
| 限流存储怎么从内存换到 Redis | `slowapi/08_storage_backend.py` | `app/core/limiter.py` |

各子目录的细节看各自的 `README.md`，这里只做索引。
