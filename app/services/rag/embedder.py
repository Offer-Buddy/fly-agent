"""向量化 —— 本地 bge-small-zh-v1.5，512 维。

三个决定值得写下来：

1. **用本地模型，不调 API**。法律问答含大量法条原文，走外部 embedding 等于把
   用户问题与检索片段发出去；本地模型还能离线跑，教学场景里这点很重要。
2. **query 加前缀，document 不加**。bge-zh 系列是这么训练的，加了前缀检索质量
   明显更好；反了（两个都加或都不加）会掉点。
3. **CPU 活必须扔进线程**。``model.encode`` 是同步阻塞调用，单条查询 20~40ms、
   批量建库时更久，直接在 async 函数里调用会卡住整个事件循环——单请求测不出来，
   一上并发就露馅。所以统一走 ``asyncio.to_thread``。
"""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from app.core.config import settings
from app.core.logging import logger

if TYPE_CHECKING:  # 只用于类型检查，运行时不导入，避免 2 秒的 import 开销
    from sentence_transformers import SentenceTransformer

# bge-zh 官方推荐的查询前缀。文档侧不加，这是非对称检索
_QUERY_PREFIX = "为这个句子生成表示以用于检索相关段落："

_model: Optional["SentenceTransformer"] = None
_model_lock = threading.Lock()


def _resolve_model_path() -> str:
    """把配置里的模型路径解析成绝对路径。

    相对路径按项目根目录（``pyproject.toml`` 所在目录）解析，
    这样从任何工作目录启动服务都能找到模型。

    Returns:
        str: 模型目录的绝对路径
    """
    configured = Path(settings.EMBEDDING_MODEL_PATH)
    if configured.is_absolute():
        return str(configured)

    project_root = Path(__file__).resolve().parents[3]
    return str((project_root / configured).resolve())


def model_available() -> bool:
    """检查本地模型目录是否存在且看起来完整。

    Returns:
        bool: 目录存在且含 ``config.json`` 时为 True
    """
    path = Path(_resolve_model_path())
    return (path / "config.json").exists()


def _get_model() -> "SentenceTransformer":
    """加载向量模型（进程内单例）。

    加载一次约 1~2 秒（CPU），所以只在首次使用时加载，之后复用。
    用双检锁保证并发首调时只加载一次——多加载几次不会错，但白吃几百 MB 内存。

    Returns:
        SentenceTransformer: 已加载的模型

    Raises:
        RuntimeError: 模型目录不存在或依赖未安装时抛出，并给出可执行的修复提示
    """
    global _model
    if _model is None:
        with _model_lock:
            if _model is None:
                if not model_available():
                    raise RuntimeError(
                        f"向量模型不存在：{_resolve_model_path()}。"
                        "请把 bge-small-zh-v1.5 放到该目录，或修改 EMBEDDING_MODEL_PATH。"
                    )

                from sentence_transformers import SentenceTransformer

                path = _resolve_model_path()
                _model = SentenceTransformer(path, device="cpu")
                logger.info("embedding_model_loaded", path=path, dimension=settings.EMBEDDING_DIM)
    return _model


def _encode_sync(texts: list[str], batch_size: int) -> list[list[float]]:
    """同步批量编码（内部函数，供线程池调用）。

    Args:
        texts: 待编码文本
        batch_size: 批大小，CPU 上 32 比较合适；太大会把内存吃满

    Returns:
        list[list[float]]: 归一化后的向量，与输入等长同序
    """
    model = _get_model()
    vectors = model.encode(
        texts,
        batch_size=batch_size,
        # 归一化后余弦相似度退化为点积，pgvector 的 <=> 直接可用
        normalize_embeddings=True,
        show_progress_bar=False,
    )
    return [vector.tolist() for vector in vectors]


async def embed_query(text: str) -> list[float]:
    """把查询编码成向量。

    Args:
        text: 用户查询（已做过条号归一化）

    Returns:
        list[float]: 512 维归一化向量
    """
    prefixed = f"{_QUERY_PREFIX}{text}"
    vectors = await asyncio.to_thread(_encode_sync, [prefixed], 1)
    return vectors[0]


async def embed_documents(texts: list[str], batch_size: int = 32) -> list[list[float]]:
    """把文档批量编码成向量。

    Args:
        texts: 文档正文列表
        batch_size: 批大小

    Returns:
        list[list[float]]: 与输入等长同序的向量列表
    """
    if not texts:
        return []
    return await asyncio.to_thread(_encode_sync, texts, batch_size)


def embed_documents_sync(texts: list[str], batch_size: int = 32) -> list[list[float]]:
    """同步版批量编码，给建库脚本用（脚本本身不在事件循环里）。

    Args:
        texts: 文档正文列表
        batch_size: 批大小

    Returns:
        list[list[float]]: 与输入等长同序的向量列表
    """
    return _encode_sync(texts, batch_size)


async def warmup() -> None:
    """预热：提前把模型加载进内存并跑一次短编码。

    不预热的话，第一个真实请求要额外等 1~2 秒，很容易被误判成「检索很慢」。
    """
    if not model_available():
        logger.warning("embedding_model_missing", path=_resolve_model_path())
        return
    await asyncio.to_thread(_encode_sync, ["预热"], 1)
    logger.info("embedding_model_warmed_up")
