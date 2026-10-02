from __future__ import annotations

from pathlib import Path
import errno

import pytest

from tracepilot.security import WorkspacePolicy, WorkspaceViolation


def test_policy_rejects_path_traversal(java_fixture: Path) -> None:
    policy = WorkspacePolicy(java_fixture)
    with pytest.raises(WorkspaceViolation):
        policy.read_text("../secret.java")


def test_policy_only_allows_writes_to_production_java(java_fixture: Path) -> None:
    policy = WorkspacePolicy(java_fixture)
    with pytest.raises(WorkspaceViolation):
        policy.resolve_file(
            "src/test/java/demo/CustomerServiceTest.java", writable=True
        )


def test_policy_rejects_non_allowlisted_suffix(java_fixture: Path) -> None:
    file_path = java_fixture / "secret.txt"
    file_path.write_text("secret", encoding="utf-8")
    policy = WorkspacePolicy(java_fixture)
    with pytest.raises(WorkspaceViolation):
        policy.read_text("secret.txt")


@pytest.mark.parametrize(
    "relative_path",
    [
        ".aws/credentials.yml",
        ".env.yaml",
        ".git/config.md",
        ".tracepilot/state.yml",
        "target/Generated.java",
        "node_modules/library/Config.java",
    ],
)
def test_direct_reads_and_search_apply_same_exclusions(
    java_fixture: Path, relative_path: str
) -> None:
    excluded = java_fixture / relative_path
    excluded.parent.mkdir(parents=True, exist_ok=True)
    excluded.write_text("sensitive", encoding="utf-8")
    policy = WorkspacePolicy(java_fixture)

    with pytest.raises(WorkspaceViolation):
        policy.read_text(relative_path)
    assert excluded not in policy.files()


@pytest.mark.parametrize(
    "relative_path",
    ["src\\main\\java\\demo\\CustomerService.java", "bad\0.java", "bad\n.java"],
)
def test_policy_rejects_noncanonical_or_control_paths(
    java_fixture: Path, relative_path: str
) -> None:
    with pytest.raises(WorkspaceViolation):
        WorkspacePolicy(java_fixture).resolve_file(relative_path)


def make_symlink(link: Path, target: Path, *, directory: bool = False) -> None:
    try:
        link.symlink_to(target, target_is_directory=directory)
    except OSError as error:
        if getattr(error, "winerror", None) == 1314 or error.errno in {
            errno.EPERM, errno.EACCES, errno.ENOSYS, errno.EOPNOTSUPP
        }:
            pytest.skip(f"当前环境不能创建符号链接: {error}")
        raise


@pytest.mark.parametrize("kind", ["file", "directory", "broken"])
def test_policy_rejects_symlink_reads_and_omits_search_results(
    java_fixture: Path, tmp_path: Path, kind: str
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.java").write_text("class Secret {}", encoding="utf-8")
    if kind == "directory":
        link = java_fixture / "linked"
        make_symlink(link, outside, directory=True)
        relative_path = "linked/secret.java"
    else:
        link = java_fixture / "linked.java"
        target = outside / ("absent.java" if kind == "broken" else "secret.java")
        make_symlink(link, target)
        relative_path = "linked.java"
    policy = WorkspacePolicy(java_fixture)

    with pytest.raises(WorkspaceViolation, match="符号链接|重解析点"):
        policy.resolve_file(relative_path)
    assert all("linked" not in path.relative_to(java_fixture).as_posix() for path in policy.files())


def test_file_limit_is_configurable_and_order_is_stable(java_fixture: Path) -> None:
    policy = WorkspacePolicy(java_fixture, max_files=1)
    with pytest.raises(WorkspaceViolation, match="文件数量"):
        policy.files()
    paths = WorkspacePolicy(java_fixture).files()
    assert paths == sorted(paths, key=lambda path: path.relative_to(java_fixture).as_posix())


def test_java_sources_exclude_files_outside_explicit_roots(java_fixture: Path) -> None:
    (java_fixture / "Extra.java").write_text("class Extra {}", encoding="utf-8")
    excluded = java_fixture / "target" / "Generated.java"
    excluded.parent.mkdir()
    excluded.write_text("class Generated {}", encoding="utf-8")
    sources = WorkspacePolicy(java_fixture).java_source_files()

    assert [path.relative_to(java_fixture).as_posix() for path in sources] == [
        "src/main/java/demo/CustomerService.java",
        "src/test/java/demo/CustomerServiceTest.java",
    ]


def test_java_source_limit_counts_both_source_roots(java_fixture: Path) -> None:
    with pytest.raises(WorkspaceViolation, match="源码数量"):
        WorkspacePolicy(java_fixture, max_files=1).java_source_files()


@pytest.mark.parametrize("kind", ["file", "directory", "root", "broken"])
def test_java_source_collection_rejects_links_before_compilation(
    java_fixture: Path, tmp_path: Path, kind: str
) -> None:
    outside = tmp_path / "source-outside"
    outside.mkdir()
    (outside / "Unsafe.java").write_text("class Unsafe {}", encoding="utf-8")
    source = java_fixture / "src/main/java"
    if kind == "root":
        original = source.with_name("original-java")
        source.rename(original)
        make_symlink(source, original, directory=True)
    elif kind == "directory":
        make_symlink(source / "linked", outside, directory=True)
    else:
        target = outside / ("missing.java" if kind == "broken" else "Unsafe.java")
        make_symlink(source / "Linked.java", target)

    with pytest.raises(WorkspaceViolation, match="符号链接|重解析点"):
        WorkspacePolicy(java_fixture).java_source_files()


def test_java_source_collection_rejects_oversized_sources(java_fixture: Path) -> None:
    oversized = java_fixture / "src/main/java/Oversized.java"
    oversized.write_bytes(b" " * 200_001)

    with pytest.raises(WorkspaceViolation, match="大小"):
        WorkspacePolicy(java_fixture).java_source_files()


def test_missing_source_roots_return_empty_list(tmp_path: Path) -> None:
    assert WorkspacePolicy(tmp_path).java_source_files() == []
