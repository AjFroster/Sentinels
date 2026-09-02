from .config import ollama_host
from .brief import Brief, BriefRecord, Classification, build_chairman_prompt

__all__ = ["Brief", "BriefRecord", "Classification", "build_chairman_prompt", "ollama_host"]
