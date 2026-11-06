"""skills：可复用技能——提示模板 + 工具白名单，把「怎么提示 + 能用什么工具」捆成一个对象。"""

from __future__ import annotations

from dataclasses import dataclass

from salmon_claude.tools.registry import ToolRegistry


@dataclass
class Skill:
    """一个可复用技能：提示模板 + 可选工具白名单。

    输入：name（技能名）、prompt_template（含 $ARGUMENTS 占位符的提示词）、
        tools（工具白名单；None=默认全部，[]=禁止全部，["read_file"]=只留这些）
    输出：无（纯数据）
    功能：把「怎么提示模型 + 能用哪些工具」捆成一个可复用的对象
    """

    name: str
    prompt_template: str
    tools: list[str] | None = None


def render_skill_prompt(skill: Skill, arguments: str) -> str:
    """把 skill.prompt_template 里的 $ARGUMENTS 替换成实际参数。

    输入：skill（技能）、arguments（用户给的真实参数/目标）
    输出：str（替换后的完整提示词）
    功能：字符串替换 $ARGUMENTS → arguments；这就是「参数替换进 Provider 请求」的源头
    """
    return skill.prompt_template.replace("$ARGUMENTS", arguments)


def apply_skill(skill: Skill, base_registry: ToolRegistry, arguments: str) -> tuple[str, ToolRegistry]:
    """应用一个技能：返回（渲染好的提示词, 按白名单过滤后的工具表）。

    输入：skill（技能）、base_registry（默认工具表）、arguments（真实参数）
    输出：(str, ToolRegistry) —— 前者当 goal 用，后者当 registry 用
    功能：render_skill_prompt + base_registry.filter(skill.tools) 两步合一，
        调用方（cli）拿到这俩就能照常装配 provider + runner
    """
    prompt = render_skill_prompt(skill, arguments)
    registry = base_registry.filter(skill.tools)
    return prompt, registry


REVIEW_SKILL = Skill(
    name="review",
    prompt_template="你是代码评审助手，请评审以下内容并给出问题与改进建议：\n$ARGUMENTS",
    tools=["read_file", "list_dir", "search_files"],   # 只读工具集
)

SKILLS: dict[str, Skill] = {"review": REVIEW_SKILL}