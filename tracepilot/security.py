"""仓库边界与文件指纹校验。"""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path


class WorkspaceViolation(ValueError):
    """文件访问越过受控仓库边界。"""


class WorkspacePolicy:
    def __init__(
        self,
        root: str | Path,
        *,
        max_file_bytes: int = 200_000,
    ) -> None:
        self.root = Path(root).resolve()
        if not self.root.is_dir():
            raise ValueError("workspace root 必须是已存在目录")
        self.max_file_bytes = max_file_bytes
        self.read_suffixes = {".java", ".xml", ".properties", ".yml", ".yaml", ".md"}
        self.write_suffixes = {".java"}

    def resolve_file(self, relative_path: str, *, writable: bool = False) -> Path:
        if not isinstance(relative_path, str) or not relative_path.strip():
            raise WorkspaceViolation("文件路径不能为空")
        raw = Path(relative_path)
        if raw.is_absolute() or '..' in raw.parts or ':' in relative_path:
            raise WorkspaceViolation("只接受仓库内相对路径")
        candidate = self.root / raw
        for part in [candidate, *candidate.parents]:
            if part == self.root:
                break
            if part.exists() and (part.is_symlink() or getattr(part.lstat(), 'st_file_attributes', 0) & 1024):
                raise WorkspaceViolation("禁止访问符号链接或重解析点")
        candidate = candidate.resolve()
        try:
            candidate.relative_to(self.root)
        except ValueError as error:
            raise WorkspaceViolation("文件路径越过仓库边界") from error
        if not candidate.is_file():
            raise FileNotFoundError(relative_path)
        allowed = self.write_suffixes if writable else self.read_suffixes
        if candidate.suffix.casefold() not in allowed:
            raise WorkspaceViolation("文件类型不在允许列表中")
        relative = candidate.relative_to(self.root).as_posix()
        if writable and not relative.startswith('src/main/java/'):
            raise WorkspaceViolation("仅允许修改src/main/java下的生产源码")
        if candidate.stat().st_size > self.max_file_bytes:
            raise WorkspaceViolation("文件超过大小限制")
        return candidate

    def files(self) -> list[Path]:
        result = []
        for path in self.root.rglob('*'):
            if any(part in {'.git', '.venv', 'target', 'node_modules', '.tracepilot'} for part in path.relative_to(self.root).parts):
                continue
            if path.suffix.casefold() not in self.read_suffixes:
                continue
            try:
                result.append(self.resolve_file(path.relative_to(self.root).as_posix()))
            except (WorkspaceViolation, FileNotFoundError, OSError):
                continue
            if len(result) > 1000:
                raise WorkspaceViolation('仓库文件数量超过本地演示上限')
        return result

    def read_text(self, relative_path: str) -> str:
        return self.resolve_file(relative_path).read_text(encoding="utf-8")

    def source_sha256(self, relative_path: str) -> str:
        return sha256(self.resolve_file(relative_path).read_bytes()).hexdigest()
