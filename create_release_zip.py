"""Build a validated, self-contained Blender extension archive."""

from __future__ import annotations

import argparse
import hashlib
import os
import runpy
import shutil
import subprocess
import sys
from pathlib import Path


ProjectRoot = Path(__file__).resolve().parent
SourceDirectory = ProjectRoot / "blender_mcp"
VersionData = runpy.run_path(str(SourceDirectory / "__version__.py"))
Version = str(VersionData["VERSION"])
DefaultOutput = ProjectRoot / f"blender_mcp_v{Version}.zip"


def GetExpectedWheelHashes() -> dict[str, str]:
    HashFile = SourceDirectory / "wheels" / "SHA256SUMS"
    Expected: dict[str, str] = {}
    for Line in HashFile.read_text(encoding="utf-8").splitlines():
        if not Line:
            continue
        Parts = Line.split("  ", 1)
        if len(Parts) != 2 or len(Parts[0]) != 64:
            raise RuntimeError("Invalid bundled-wheel hash inventory")
        Expected[Parts[1]] = Parts[0]
    if not Expected:
        raise RuntimeError("Bundled-wheel hash inventory is empty")
    return Expected


def VerifyBundledWheels() -> None:
    WheelDirectory = SourceDirectory / "wheels"
    Expected = GetExpectedWheelHashes()
    ActualNames = {PathValue.name for PathValue in WheelDirectory.glob("*.whl")}
    if ActualNames != set(Expected):
        raise RuntimeError("Bundled wheels do not match the release inventory")

    for Name, ExpectedHash in Expected.items():
        Digest = hashlib.sha256((WheelDirectory / Name).read_bytes()).hexdigest()
        if Digest != ExpectedHash:
            raise RuntimeError(f"Bundled wheel failed integrity verification: {Name}")


def GetBlenderExecutable(ExplicitPath: str | None) -> Path:
    Candidate = ExplicitPath or os.environ.get("BLENDER_EXECUTABLE") or shutil.which("blender")
    if not Candidate:
        raise RuntimeError(
            "Blender executable not found; set BLENDER_EXECUTABLE or pass --blender-executable"
        )
    Resolved = Path(Candidate).expanduser().resolve()
    if not Resolved.is_file():
        raise RuntimeError("Configured Blender executable does not exist")
    return Resolved


def BuildRelease(BlenderExecutable: Path, OutputPath: Path = DefaultOutput) -> Path:
    VerifyBundledWheels()
    ResolvedOutput = OutputPath.resolve()
    Command = [
        str(BlenderExecutable),
        "--command",
        "extension",
        "build",
        "--source-dir",
        str(SourceDirectory),
        "--output-filepath",
        str(ResolvedOutput),
    ]
    Result = subprocess.run(Command, check=False, capture_output=True, text=True, timeout=120)
    if Result.returncode != 0 or not ResolvedOutput.is_file():
        raise RuntimeError("Blender extension build failed; inspect the build output")
    print(Result.stdout.strip())
    print(f"[BlenderMCP:Release] Built {ResolvedOutput.name}")
    return ResolvedOutput


def Main() -> int:
    Parser = argparse.ArgumentParser(description=__doc__)
    Parser.add_argument("--blender-executable")
    Parser.add_argument("--output", type=Path, default=DefaultOutput)
    Arguments = Parser.parse_args()
    try:
        BlenderExecutable = GetBlenderExecutable(Arguments.blender_executable)
        BuildRelease(BlenderExecutable, Arguments.output)
    except (OSError, RuntimeError, subprocess.SubprocessError) as Error:
        print(f"[BlenderMCP:Release] {Error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(Main())
