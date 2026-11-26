# SPDX-License-Identifier: NOASSERTION
"""버전별 부트로더와 PMU 릴리스 산출물을 Release/pmu에 모은다."""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parents[1]
BOOTLOADER_RELEASE = REPO_ROOT / "ges_bootloader_STM32F405" / "Release"
APP_RELEASE = REPO_ROOT / "PMU" / "Release"
VERSION_PATTERN = re.compile(r"^\d+(?:\.\d+)+$")


@dataclass(frozen=True)
class ToolRelease:
    """exe 도구 하나의 릴리스 설정 (tools/build.py 산출물 위치와 복사 대상)."""

    name: str                   # Release/pmu 아래 폴더명이자 출력 문구
    project: str                # 저장소 루트 아래 프로젝트 폴더
    exes: tuple[str, ...]       # 복사 대상 exe (첫 번째는 필수, 나머지는 만들어져 있을 때만)
    version_rel: str = "out/work/build_version.txt"     # 프로젝트 기준 빌드 버전 파일 위치

    @property
    def dist(self) -> Path:
        """build.py 가 exe 를 만드는 폴더."""
        return REPO_ROOT / self.project / "out" / "dist"

    @property
    def version_file(self) -> Path:
        """build.py 가 쓴 이번 빌드 버전 파일."""
        return REPO_ROOT / self.project / self.version_rel

    @property
    def target(self) -> Path:
        """버전 폴더를 모으는 곳."""
        return SCRIPT_DIR / self.name


TOOLS = (
    ToolRelease("PMUFactoryTool", "PMUFactoryTool", ("PMUFactoryTool", "PMUFactoryTool_cli")),  # CLI 는 --cli 빌드 때만
    ToolRelease("OLDPMUinfo", "OLDPMUgetinfo", ("OLDPMUinfo",)),    # 구형 PMU 정보 수집 도구 (창 모드 exe 하나)
    ToolRelease("PMUtool", "PMUtool", ("PMUtool",), "out/build_version.txt"),   # 시험·보정 도구 (build_exe.py 가 out/ 에 기록)
)


def version_key(version: str) -> tuple[int, ...]:
    """버전 문자열을 숫자 튜플로 변환한다."""
    return tuple(int(part) for part in version.split("."))


def version_directories(root: Path) -> list[Path]:
    """릴리스 루트에서 숫자 점 표기 버전 폴더만 반환한다."""
    if not root.is_dir():
        raise FileNotFoundError(f"릴리스 폴더가 없습니다: {root}")
    return sorted(
        (path for path in root.iterdir() if path.is_dir() and VERSION_PATTERN.fullmatch(path.name)),
        key=lambda path: version_key(path.name),
    )


def existing_versions(root: Path) -> list[str]:
    """패키지 대상 폴더에 이미 등록된 버전을 반환한다."""
    if not root.exists():
        return []
    return [path.name for path in version_directories(root)]


def tool_build_version(tool: ToolRelease) -> str:
    """tools/build.py 가 기록한 이번 빌드 버전(예: 0.10.0.90)을 읽는다. 없거나 형식이 다르면 빈 문자열."""
    try:
        text = tool.version_file.read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    return text if VERSION_PATTERN.fullmatch(text) else ""


def sync_tool(tool: ToolRelease, dry_run: bool) -> str:
    """빌드한 exe 를 새 버전일 때만 [도구명]/[버전]/ 으로 복사하고 결과 문구를 돌려준다.

    첫 번째 exe 는 필수, 나머지(CLI 등)는 만들어져 있을 때만 복사한다.
    버전은 build.py 가 기록한 값이며, 기존 최신 버전 이하이거나 exe 가 없으면 건너뛴다.
    """
    exes = [name for name in tool.exes if (tool.dist / f"{name}.exe").is_file()]
    if tool.exes[0] not in exes:
        return f"exe 없음 (먼저 {tool.project}/tools/build.py 실행): {tool.dist / (tool.exes[0] + '.exe')}"
    version = tool_build_version(tool)
    if not version:
        return f"빌드 버전을 읽지 못함 (build.py 를 다시 실행): {tool.version_file}"
    newest = max((version_key(item) for item in existing_versions(tool.target)), default=())
    if newest and version_key(version) <= newest:
        return "새 버전 없음"
    if not dry_run:
        destination = tool.target / version
        destination.mkdir(parents=True, exist_ok=True)
        for name in exes:
            shutil.copy2(tool.dist / f"{name}.exe", destination / f"{name}_{version}.exe")
    return version


def main() -> int:
    """신규 버전 릴리스 파일을 앱·부트로더·PMUFactoryTool 폴더로 동기화한다."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="복사 예정 버전만 출력")
    args = parser.parse_args()

    targets = (
        (
            "bootloader", BOOTLOADER_RELEASE, SCRIPT_DIR / "bootloader",
            (
                "ges_bootloader_STM32F405_{version}.bin",
                "ges_bootloader_STM32F405_{version}.hex",
                "GES_Factory_Tool_{version}.exe",
            ),
        ),
        ("app", APP_RELEASE, SCRIPT_DIR / "app", ("pmu_{version}.bin", "pmu_{version}.ges")),
    )

    try:
        for label, source_root, target_root, patterns in targets:
            new_dirs = []
            newest = max((version_key(item) for item in existing_versions(target_root)), default=())
            for source_dir in version_directories(source_root):
                if newest and version_key(source_dir.name) <= newest:
                    continue
                if any((source_dir / pattern.format(version=source_dir.name)).is_file() for pattern in patterns):
                    new_dirs.append(source_dir.name)

            if args.dry_run:
                print(f"{label}: {', '.join(new_dirs) if new_dirs else '새 버전 없음'}")
                continue

            copied: list[str] = []
            for version in new_dirs:
                source_dir = source_root / version
                destination = target_root / version
                destination.mkdir(parents=True, exist_ok=True)
                for pattern in patterns:
                    artifact = source_dir / pattern.format(version=version)
                    if artifact.is_file():
                        shutil.copy2(artifact, destination / artifact.name)
                copied.append(version)
            print(f"{label}: {', '.join(copied) if copied else '새 버전 없음'}")
        for tool in TOOLS:
            print(f"{tool.name}: {sync_tool(tool, args.dry_run)}")
    except OSError as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
