"""LLM 子包：对外只暴露注册表、服务类与全局服务单例。"""

from app.services.llm.registry import LLMRegistry
from app.services.llm.service import LLMService, llm_service

__all__ = ["LLMRegistry", "LLMService", "llm_service"]
