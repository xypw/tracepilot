"""只从 TracePilot 自身安装目录加载受信任的调查规程。"""

from __future__ import annotations

from pathlib import Path
import stat


_PACKAGE_ROOT = Path(__file__).resolve().parent
_SKILL_RELATIVE_PATH = Path("skills/java-test-triage/SKILL.md")


class SkillConfigurationError(RuntimeError):
    """随应用发布的 Skill 缺失或损坏，不能继续模型调查。"""


def load_java_test_triage_skill() -> str:
    """返回固定 Skill 的 Markdown 正文；不接受工作区或模型指定路径。"""
    skill_path = _PACKAGE_ROOT / _SKILL_RELATIVE_PATH

    def invalid(reason: str) -> SkillConfigurationError:
        return SkillConfigurationError(
            f"java-test-triage Skill 配置错误（{skill_path}）：{reason}"
        )

    try:
        # 安装目录中的链接也不能把受信任规程重定向到其他位置。
        current = _PACKAGE_ROOT
        for part in _SKILL_RELATIVE_PATH.parts:
            current = current / part
            metadata = current.lstat()
            if stat.S_ISLNK(metadata.st_mode) or (
                getattr(metadata, "st_file_attributes", 0)
                & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 1024)
            ):
                raise invalid("Skill 路径不能包含符号链接或重解析点")
        content = skill_path.read_text(encoding="utf-8")
    except UnicodeError as error:
        raise invalid("文件必须是有效 UTF-8 文本") from error
    except OSError as error:
        raise invalid("无法读取随应用安装的 SKILL.md") from error

    if not content.strip():
        raise invalid("文件为空")
    if any(ord(character) < 32 and character not in "\n\r\t" for character in content):
        raise invalid("文件包含非法控制字符")

    lines = content.splitlines()
    if lines[0] != "---" or "---" not in lines[1:]:
        raise invalid("缺少完整的 Skill 元数据块")
    header_end = lines.index("---", 1)
    fields: dict[str, str] = {}
    # 随包发布的 Skill 使用单行 name/description；不解析或执行扩展配置。
    for line in lines[1:header_end]:
        if not line.strip():
            continue
        key, separator, value = line.partition(":")
        key, value = key.strip(), value.strip()
        if not separator or not key or not value or key in fields:
            raise invalid("Skill 元数据格式无效")
        fields[key] = value
    if fields.get("name") != "java-test-triage" or not fields.get("description"):
        raise invalid("Skill 元数据必须包含正确的 name 和非空 description")
    body = "\n".join(lines[header_end + 1:]).strip()
    if not body:
        raise invalid("Skill 正文为空")
    return body
