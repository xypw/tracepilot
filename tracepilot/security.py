"""仓库边界与文件指纹校验。"""

from __future__ import annotations

from hashlib import sha256
import os
from pathlib import Path
import stat
import unicodedata


class WorkspaceViolation(ValueError):
    """文件访问越过受控仓库边界。"""


class WorkspacePolicy:
    def __init__(
        self,
        root: str | Path,
        *,
        max_file_bytes: int = 200_000,
        max_files: int = 1_000,
    ) -> None:
        self.root = Path(root).resolve()
        if not self.root.is_dir():
            raise ValueError("workspace root 必须是已存在目录")
        self.max_file_bytes = max_file_bytes
        self.max_files = max_files
        self.read_suffixes = {".java", ".xml", ".properties", ".yml", ".yaml", ".md"}
        self.write_suffixes = {".java"}

    def _resolve_path(self, relative_path: str) -> Path:
        """读取、搜索与编译共同使用的路径入口。"""
        if not isinstance(relative_path, str) or not relative_path.strip():
            raise WorkspaceViolation("文件路径不能为空")
        if "\\" in relative_path or any(
            unicodedata.category(character) == "Cc" for character in relative_path
        ):
            raise WorkspaceViolation("路径必须使用正斜杠，且不能包含控制字符")
        raw = Path(relative_path)
        if raw.is_absolute() or '..' in raw.parts or ':' in relative_path:
            raise WorkspaceViolation("只接受仓库内相对路径")
        if any(
            part.startswith(".") or part.casefold() in {"target", "node_modules"}
            for part in relative_path.split("/")
        ):
            raise WorkspaceViolation("禁止访问隐藏路径或排除目录")
        candidate = self.root / raw
        for part in [candidate, *candidate.parents]:
            if part == self.root:
                break
            try:
                metadata = part.lstat()
            except FileNotFoundError:
                continue
            if stat.S_ISLNK(metadata.st_mode) or (
                getattr(metadata, "st_file_attributes", 0)
                & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 1024)
            ):
                raise WorkspaceViolation("禁止访问符号链接或重解析点")
        candidate = candidate.resolve()
        try:
            candidate.relative_to(self.root)
        except ValueError as error:
            raise WorkspaceViolation("文件路径越过仓库边界") from error
        return candidate

    def resolve_file(self, relative_path: str, *, writable: bool = False) -> Path:
        candidate = self._resolve_path(relative_path)
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
        result: list[Path] = []
        for directory, names, filenames in os.walk(self.root, followlinks=False):
            parent = Path(directory)
            allowed_directories = []
            for name in sorted(names):
                path = parent / name
                try:
                    self._resolve_path(path.relative_to(self.root).as_posix())
                except (WorkspaceViolation, OSError):
                    continue
                allowed_directories.append(name)
            names[:] = allowed_directories
            for name in sorted(filenames):
                path = parent / name
                if path.suffix.casefold() not in self.read_suffixes:
                    continue
                try:
                    result.append(self.resolve_file(path.relative_to(self.root).as_posix()))
                except (WorkspaceViolation, OSError):
                    continue
                if len(result) > self.max_files:
                    raise WorkspaceViolation('仓库文件数量超过本地演示上限')
        return sorted(result, key=lambda path: path.relative_to(self.root).as_posix())

    def java_source_files(self) -> list[Path]:
        """收集编译允许使用的源码；违规源码必须在启动编译前报错。"""
        result: list[Path] = []

        def raise_walk_error(error: OSError) -> None:
            raise WorkspaceViolation("无法安全枚举 Java 源码目录") from error

        for relative_root in ("src/main/java", "src/test/java"):
            source_root = self._resolve_path(relative_root)
            if not source_root.exists():
                continue
            if not source_root.is_dir():
                raise WorkspaceViolation("Java 源码根必须是目录")
            for directory, names, filenames in os.walk(
                source_root, followlinks=False, onerror=raise_walk_error
            ):
                parent = Path(directory)
                for name in sorted(names):
                    self._resolve_path((parent / name).relative_to(self.root).as_posix())
                names.sort()
                for name in sorted(filenames):
                    path = parent / name
                    if path.suffix.casefold() != ".java":
                        continue
                    result.append(self.resolve_file(path.relative_to(self.root).as_posix()))
                    if len(result) > self.max_files:
                        raise WorkspaceViolation("Java 源码数量超过本地演示上限")
        return sorted(result, key=lambda path: path.relative_to(self.root).as_posix())

    def read_text(self, relative_path: str) -> str:
        return self.resolve_file(relative_path).read_text(encoding="utf-8")

    def source_sha256(self, relative_path: str) -> str:
        return sha256(self.resolve_file(relative_path).read_bytes()).hexdigest()
