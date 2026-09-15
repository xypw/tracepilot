"""Agent 可调用的受限代码工具、补丁工具和测试运行器。"""

from __future__ import annotations

from difflib import unified_diff
from pathlib import Path
import re
import subprocess
import tempfile
import sqlite3
from threading import RLock
from typing import Protocol

from tracepilot.models import FileMatch, PatchProposal, TestResult
from tracepilot.security import WorkspacePolicy, WorkspaceViolation


class TestRunner(Protocol):
    def run(self, test_target: str) -> TestResult: ...


class CodeTools:
    def __init__(self, policy: WorkspacePolicy) -> None:
        self.policy = policy

    def locate_stack_files(self, failure_text: str, *, limit: int = 8) -> list[str]:
        """按 Stack Trace 中的 Java 文件名定位仓库内候选文件。"""
        names = list(dict.fromkeys(re.findall(r"([A-Za-z_$][A-Za-z0-9_$]*\.java):\d+", failure_text)))
        matches = []
        for name in names:
            for path in self.policy.files():
                if path.name == name:
                    relative = path.relative_to(self.policy.root).as_posix()
                    if relative not in matches:
                        matches.append(relative)
                    if len(matches) >= limit:
                        return matches
        return matches

    def search(self, query: str, *, limit: int = 20) -> list[FileMatch]:
        if not isinstance(query, str) or not query.strip() or len(query) > 128:
            raise ValueError("搜索词必须是1到128字符")
        needle = query.casefold()
        matches = []
        for path in self.policy.files():
            if not path.is_file() or path.suffix.casefold() not in self.policy.read_suffixes:
                continue
            if path.stat().st_size > self.policy.max_file_bytes:
                continue
            for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
                if needle in line.casefold():
                    matches.append(FileMatch(
                        relative_path=path.relative_to(self.policy.root).as_posix(),
                        line=line_number,
                        excerpt=line.strip()[:300],
                    ))
                    if len(matches) >= limit:
                        return matches
        return matches

    def read(self, relative_path: str) -> str:
        return self.policy.read_text(relative_path)


class MavenTestRunner:
    """只允许运行单个测试类，不接受任意Shell参数。"""

    _TARGET = re.compile(r"^[A-Za-z_$][A-Za-z0-9_.$]*$")

    def __init__(self, root: str | Path, *, executable: str = "mvn", timeout_seconds: int = 120) -> None:
        self.root = Path(root).resolve()
        self.executable = executable
        self.timeout_seconds = timeout_seconds

    def run(self, test_target: str) -> TestResult:
        if not self._TARGET.fullmatch(test_target):
            raise WorkspaceViolation("测试目标不在白名单格式内")
        command = [self.executable, "-B", "-ntp", f"-Dtest={test_target}", "test"]
        completed = subprocess.run(
            command,
            cwd=self.root,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=self.timeout_seconds,
            shell=False,
        )
        output = (completed.stdout + "\n" + completed.stderr)[-4_000:]
        return TestResult(
            passed=completed.returncode == 0,
            command=command,
            return_code=completed.returncode,
            output_tail=output,
        )


class JavacMainTestRunner:
    """无需Maven依赖的离线验收运行器，只执行固定Java测试主类。"""

    _TARGET = re.compile(r"^[A-Za-z_$][A-Za-z0-9_.$]*$")

    def __init__(
        self,
        root: str | Path,
        *,
        java_executable: str = "java",
        javac_executable: str = "javac",
        timeout_seconds: int = 30,
    ) -> None:
        self.root = Path(root).resolve()
        self.java_executable = java_executable
        self.javac_executable = javac_executable
        self.timeout_seconds = timeout_seconds

    def run(self, test_target: str) -> TestResult:
        if not self._TARGET.fullmatch(test_target):
            raise WorkspaceViolation("测试目标不在白名单格式内")
        source_files = [str(path) for path in self.root.rglob("*.java")]
        if not source_files:
            raise ValueError("仓库中没有Java源文件")
        with tempfile.TemporaryDirectory(prefix="tracepilot-javac-") as output_dir:
            compile_command = [
                self.javac_executable,
                "-encoding",
                "UTF-8",
                "-d",
                output_dir,
                *source_files,
            ]
            compiled = subprocess.run(
                compile_command,
                cwd=self.root,
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                timeout=self.timeout_seconds,
                shell=False,
            )
            if compiled.returncode != 0:
                output = (compiled.stdout + "\n" + compiled.stderr)[-4_000:]
                return TestResult(
                    passed=False,
                    command=compile_command,
                    return_code=compiled.returncode,
                    output_tail=output,
                )
            test_command = [self.java_executable, "-ea", "-cp", output_dir, test_target]
            tested = subprocess.run(
                test_command,
                cwd=self.root,
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                timeout=self.timeout_seconds,
                shell=False,
            )
            output = (tested.stdout + "\n" + tested.stderr)[-4_000:]
            return TestResult(
                passed=tested.returncode == 0,
                command=test_command,
                return_code=tested.returncode,
                output_tail=output,
            )


class SafePatchApplier:
    def __init__(self, policy: WorkspacePolicy, runner: TestRunner, *, receipt_path: str = ':memory:') -> None:
        self.policy = policy
        self.runner = runner
        self.lock = RLock()
        self.receipts = sqlite3.connect(receipt_path, check_same_thread=False)
        self.receipts.execute('CREATE TABLE IF NOT EXISTS receipts (id TEXT PRIMARY KEY, status TEXT NOT NULL, result TEXT)')
        self.receipts.commit()

    def close(self) -> None:
        self.receipts.close()

    def receipt_status(self, proposal: PatchProposal) -> str | None:
        """返回幂等回执状态，供状态查询和故障恢复使用。"""
        key = str(self.policy.root) + ':' + proposal.proposal_digest
        row = self.receipts.execute(
            'SELECT status FROM receipts WHERE id=?', (key,)
        ).fetchone()
        return row[0] if row else None

    def preview(self, proposal: PatchProposal) -> str:
        original = self.policy.read_text(proposal.relative_path)
        updated = original.replace(proposal.old_text, proposal.new_text, 1)
        return "".join(unified_diff(
            original.splitlines(keepends=True),
            updated.splitlines(keepends=True),
            fromfile=f"a/{proposal.relative_path}",
            tofile=f"b/{proposal.relative_path}",
        ))

    def apply_and_test(self, proposal: PatchProposal) -> tuple[TestResult, bool]:
        with self.lock:
            return self._apply_locked(proposal)

    def _apply_locked(self, proposal: PatchProposal) -> tuple[TestResult, bool]:
        rebuilt = PatchProposal.create(**proposal.model_dump(exclude={'proposal_id', 'proposal_digest'}))
        if rebuilt.proposal_digest != proposal.proposal_digest:
            raise WorkspaceViolation('补丁摘要校验失败')
        key = str(self.policy.root) + ':' + proposal.proposal_digest
        row = self.receipts.execute('SELECT status, result FROM receipts WHERE id=?', (key,)).fetchone()
        if row:
            if row[0] == 'DONE':
                result = TestResult.model_validate_json(row[1])
                return result, not result.passed
            raise WorkspaceViolation('RESULT_UNKNOWN: 前次执行未形成回执，需要人工检查工作区')
        path = self.policy.resolve_file(proposal.relative_path, writable=True)
        if self.policy.source_sha256(proposal.relative_path) != proposal.source_sha256:
            raise WorkspaceViolation("源文件已变化，补丁确认失效")
        original_bytes = path.read_bytes()
        original = original_bytes.decode('utf-8')
        if original.count(proposal.old_text) != 1:
            raise WorkspaceViolation("待替换代码不是唯一匹配")
        updated = original.replace(proposal.old_text, proposal.new_text, 1)
        if abs(len(updated) - len(original)) > 4_000:
            raise WorkspaceViolation("补丁规模超过限制")
        self.receipts.execute('INSERT INTO receipts VALUES (?, ?, NULL)', (key, 'EXECUTING'))
        self.receipts.commit()
        updated_bytes = updated.encode('utf-8')
        path.write_bytes(updated_bytes)
        try:
            result = self.runner.run(proposal.test_target)
        except Exception:
            if path.read_bytes() == updated_bytes:
                path.write_bytes(original_bytes)
            raise
        if not result.passed:
            if path.read_bytes() != updated_bytes:
                raise WorkspaceViolation('RESULT_UNKNOWN: 文件在测试期间被其他进程修改，请人工检查')
            path.write_bytes(original_bytes)
        self.receipts.execute('UPDATE receipts SET status=?, result=? WHERE id=?', ('DONE', result.model_dump_json(), key))
        self.receipts.commit()
        return result, not result.passed
