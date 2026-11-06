import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class Config:
    """配置对象（不可变）。"""
    model: str
    base_url: str
    api_key: str | None
    max_steps: int
    workspace_root: Path
def get_config() -> Config:
    """从环境变量读配置并校验。"""
    load_dotenv()   
    model = os.getenv("SALMON_MODEL", "deepseek-chat")
    base_url = os.getenv("SALMON_BASE_URL", "https://api.deepseek.com")
    api_key = os.getenv("SALMON_API_KEY")
    max_steps = int(os.getenv("SALMON_MAX_STEPS", "20"))
    if max_steps <= 0:
        raise ValueError("max_steps 必须是正数")
    workspace_root = Path(os.getenv("SALMON_WORKSPACE", "."))    
    return Config(model=model, base_url=base_url, api_key=api_key,
                  max_steps=max_steps, workspace_root=workspace_root)





