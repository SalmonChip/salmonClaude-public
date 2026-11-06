"""truncate：把超长工具输出截成「头 + 尾 + 标记」，只给模型看，完整内容留在记录里。"""

MAX_TOOL_OUTPUT_CHARS = 8000   # 工具输出进入模型视图前的默认字符上限


def truncate_for_model(content: str, max_chars: int = MAX_TOOL_OUTPUT_CHARS) -> str:
    """截断超长的工具输出；不超长原样返回。

    输入：content（工具输出的完整文本）、max_chars（字符上限，默认全局常量）
    输出：截断后的文本（超长时 = 头 + 截断标记 + 尾；否则原样）
    功能：只影响模型视图——完整内容仍留在工件/运行记录里，这里只保证
        不把超长文本整个塞给模型；标记带上原文总长度，模型能知道被截了
    """
    if len(content) <= max_chars:
        return content
    half = max_chars // 2
    head = content[:half]
    tail = content[-half:] if half else ""
    marker = f"\n...[已截断，原文共 {len(content)} 字符，仅保留首尾各 {half} 字符]...\n"
    return head + marker + tail
