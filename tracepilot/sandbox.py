"""Run Java tests in a bounded Docker container, never in the host process."""

from __future__ import annotations

import os
from pathlib import Path
from queue import Empty, Full, Queue
import re
import subprocess
from tempfile import TemporaryDirectory
from threading import Event, Thread
from time import monotonic

from tracepilot.models import TestResult
from tracepilot.security import WorkspacePolicy


class SandboxUnavailable(RuntimeError):
    """The isolated runner could not start or could not be stopped safely."""


class DockerJavacSandboxRunner:
    """Copy only allowlisted Java source into a networkless, read-only container.

    Docker is a risk reduction boundary, not a guarantee against kernel or
    container-runtime vulnerabilities. Never mount the Docker socket here.
    """

    _TARGET = re.compile(r"^[A-Za-z_$][A-Za-z0-9_.$]*$")
    _CID = re.compile(r"^[0-9a-f]{12,64}$")

    def __init__(
        self,
        root: str | Path,
        *,
        image: str = "eclipse-temurin:17-jdk-jammy",
        docker_executable: str = "docker",
        timeout_seconds: int = 90,
        max_output_bytes: int = 1_000_000,
    ) -> None:
        if timeout_seconds <= 0 or max_output_bytes <= 0:
            raise ValueError("沙箱时间和输出限制必须为正数")
        self.policy = WorkspacePolicy(root)
        self.image = image
        self.docker_executable = docker_executable
        self.timeout_seconds = timeout_seconds
        self.max_output_bytes = max_output_bytes
        self.script = Path(__file__).with_name("run_java_test.sh")

    def _stop_container(self, cid_file: Path) -> None:
        if not cid_file.is_file():
            raise SandboxUnavailable("无法确认沙箱容器状态，需要人工检查 Docker")
        cid = cid_file.read_text(encoding="ascii").strip()
        if not self._CID.fullmatch(cid):
            raise SandboxUnavailable("沙箱容器 ID 无效，需要人工检查 Docker")
        try:
            stopped = subprocess.run(
                [self.docker_executable, "rm", "-f", cid],
                capture_output=True,
                text=True,
                timeout=15,
                shell=False,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise SandboxUnavailable("无法确认沙箱容器已停止") from error
        if stopped.returncode != 0:
            # --rm may have removed a container that exited just before cleanup.
            if "No such container" not in stopped.stderr:
                raise SandboxUnavailable("无法确认沙箱容器已停止")

    def _run_bounded(self, command: list[str], cid_file: Path) -> tuple[int, str]:
        """Drain both output streams with bounded memory and a total byte budget."""
        process = subprocess.Popen(
            command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL, shell=False, env=os.environ.copy(),
        )
        chunks: Queue[bytes | BaseException | None] = Queue(maxsize=8)
        stop_reader = Event()

        def enqueue(item: bytes | BaseException | None) -> None:
            while not stop_reader.is_set():
                try:
                    chunks.put(item, timeout=0.1)
                    return
                except Full:
                    continue

        def read_output() -> None:
            try:
                assert process.stdout is not None
                while not stop_reader.is_set():
                    chunk = process.stdout.read1(8192)
                    if not chunk:
                        break
                    enqueue(chunk)
            except Exception as error:
                enqueue(error)
            finally:
                enqueue(None)

        reader = Thread(target=read_output, daemon=True)
        reader.start()
        deadline = monotonic() + self.timeout_seconds
        tail = bytearray()
        total_bytes = 0
        finished = False
        try:
            while not finished:
                remaining = deadline - monotonic()
                if remaining <= 0:
                    self._stop_container(cid_file)
                    raise SandboxUnavailable("受限 Java 测试超时，容器已停止")
                try:
                    chunk = chunks.get(timeout=min(remaining, 0.1))
                except Empty:
                    continue
                if chunk is None:
                    finished = True
                elif isinstance(chunk, BaseException):
                    self._stop_container(cid_file)
                    raise SandboxUnavailable("沙箱输出读取失败，容器已停止") from chunk
                else:
                    total_bytes += len(chunk)
                    if total_bytes > self.max_output_bytes:
                        self._stop_container(cid_file)
                        raise SandboxUnavailable("沙箱输出超过总预算，容器已停止")
                    tail.extend(chunk)
                    del tail[:-4000]
            try:
                return_code = process.wait(timeout=max(0.001, deadline - monotonic()))
            except subprocess.TimeoutExpired as error:
                self._stop_container(cid_file)
                raise SandboxUnavailable("受限 Java 测试超时，容器已停止") from error
            return return_code, tail.decode("utf-8", errors="replace")
        finally:
            stop_reader.set()
            if process.poll() is None:
                process.kill()
            process.wait(timeout=15)
            reader.join(timeout=2)
            if process.stdout is not None:
                process.stdout.close()

    def run(self, test_target: str) -> TestResult:
        if not self._TARGET.fullmatch(test_target):
            raise ValueError("测试目标不在白名单格式内")
        if not self.script.is_file():
            raise SandboxUnavailable("固定测试脚本缺失，拒绝在宿主机回退运行")

        with TemporaryDirectory(prefix="tracepilot-sandbox-") as temp:
            temp_root = Path(temp)
            staged = temp_root / "source"
            staged.mkdir()
            source_count = 0
            for source in self.policy.java_source_files():
                relative = source.relative_to(self.policy.root)
                destination = staged / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(source.read_bytes())
                source_count += 1
            if not source_count:
                raise ValueError("仓库中没有允许测试的 Java 源文件")

            cid_file = temp_root / "container.cid"
            command = [
                self.docker_executable, "run", "--rm", "--pull=never",
                "--log-driver=none",
                "--cidfile", str(cid_file),
                "--network=none", "--read-only", "--cap-drop=ALL",
                "--security-opt=no-new-privileges", "--pids-limit=64",
                "--memory=512m", "--memory-swap=512m", "--cpus=1",
                "--user=65534:65534", "--env=HOME=/tmp",
                "--tmpfs=/tmp:rw,nosuid,nodev,noexec,size=256m,mode=1777",
                "--mount", f"type=bind,source={staged},target=/workspace,readonly",
                "--mount", f"type=bind,source={self.script},target=/run-java-test.sh,readonly",
                "--workdir=/workspace", "--entrypoint=/bin/sh",
                self.image, "/run-java-test.sh", test_target,
            ]
            try:
                return_code, output = self._run_bounded(command, cid_file)
            except FileNotFoundError as error:
                raise SandboxUnavailable("Docker 不可用，拒绝在宿主机回退运行") from error

            if return_code in {125, 126, 127} or not cid_file.is_file():
                raise SandboxUnavailable("受限容器未能启动，请检查 Docker 与本地 JDK 镜像")
            return TestResult(
                passed=return_code == 0,
                command=["docker", "run", "--network=none", self.image, test_target],
                return_code=return_code,
                output_tail=output,
            )
