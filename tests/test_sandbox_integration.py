"""Opt-in Docker acceptance; uses one synthetic Java source, no model calls."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from tracepilot.sandbox import DockerJavacSandboxRunner


@pytest.mark.skipif(
    os.environ.get("TRACEPILOT_DOCKER_TESTS") != "1",
    reason="Requires explicit Docker acceptance and a preloaded JDK image",
)
def test_real_container_blocks_host_access_and_source_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "workspace/src/test/java/demo/SandboxProbe.java"
    source.parent.mkdir(parents=True)
    source.write_text(r'''
package demo;

import java.nio.file.*;
import java.util.*;
import java.io.IOException;

public class SandboxProbe {
    private static void require(boolean condition, String message) {
        if (!condition) throw new AssertionError(message);
    }

    private static void expectWriteDenied(Path target) throws Exception {
        try {
            Files.writeString(target, "sandbox probe");
        } catch (IOException denied) {
            return;
        }
        throw new AssertionError("Unexpected write access: " + target);
    }

    public static void main(String[] args) throws Exception {
        String status = Files.readString(Path.of("/proc/self/status"));
        require(status.contains("Uid:\t65534\t65534\t65534\t65534"), "non-root UID");
        require(status.contains("CapEff:\t0000000000000000"), "dropped capabilities");
        require(status.contains("NoNewPrivs:\t1"), "no new privileges");
        require(System.getenv("TRACEPILOT_TEST_HOST_SECRET") == null, "host secret leaked");
        require(!Files.exists(Path.of("/var/run/docker.sock")), "Docker socket exposed");
        require(!Files.exists(Path.of("/workspace/.env")), "private file staged");
        require(!Files.exists(Path.of("/workspace/RootExtra.java")), "extra source staged");
        try (var devices = Files.list(Path.of("/sys/class/net"))) {
            require(devices.allMatch(p -> p.getFileName().toString().equals("lo")),
                    "external network interface exposed");
        }
        expectWriteDenied(Path.of("/workspace/src/test/java/demo/SandboxProbe.java"));
        expectWriteDenied(Path.of("/tracepilot-root-write-probe"));
        Files.writeString(Path.of("/tmp/sandbox-probe"), "temporary storage works");
        require(Files.readString(Path.of("/sys/fs/cgroup/memory.max")).trim()
                .equals("536870912"), "memory cap");
        require(Files.readString(Path.of("/sys/fs/cgroup/pids.max")).trim()
                .equals("64"), "process cap");
        System.out.println("PASS: non-root, capabilities, network isolation, read-only source/root, "
                + "private environment, no Docker socket, memory/process limits, temporary compilation");
    }
}
''', encoding="utf-8")
    root = tmp_path / "workspace"
    (root / ".env").write_text("TEST_ONLY_SECRET=not-for-container", encoding="utf-8")
    (root / "RootExtra.java").write_text("class RootExtra {}", encoding="utf-8")
    monkeypatch.setenv("TRACEPILOT_TEST_HOST_SECRET", "synthetic-host-secret")
    original = source.read_bytes()

    result = DockerJavacSandboxRunner(root).run("demo.SandboxProbe")

    assert result.passed, result.output_tail
    assert "PASS: non-root" in result.output_tail
    assert source.read_bytes() == original
