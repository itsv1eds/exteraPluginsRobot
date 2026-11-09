import argparse
import struct
from datetime import datetime
from pathlib import Path
from zipfile import ZIP_DEFLATED, BadZipFile, ZipFile


ROOT = Path(__file__).resolve().parents[1]
ROOT_FILES = ("main.py", "auth.py", "requirements.txt", "Dockerfile", "docker-compose.yml", "README.md", "config.json.example")
SOURCE_DIRECTORIES = ("core", "bot", "userbot", "scripts")
LOCAL_SCRIPTS = {"seed_test_catalog.py", "restore_request_history.py"}
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg"}


def deployment_files(root: Path = ROOT) -> list[Path]:
    root = root.resolve()
    files = [root / name for name in ROOT_FILES]
    for directory in SOURCE_DIRECTORIES:
        source = root / directory
        if not source.is_dir() or source.is_symlink():
            raise ValueError(f"Missing source directory or symlink: {source}")
        files.extend(
            path for path in source.rglob("*.py")
            if "__pycache__" not in path.parts and not (directory == "scripts" and path.name in LOCAL_SCRIPTS)
        )
    for directory in ("bot/locales", "img"):
        if not (root / directory).is_dir() or (root / directory).is_symlink():
            raise ValueError(f"Missing resource directory or symlink: {root / directory}")
    files.extend((root / "bot/locales").rglob("*.json"))
    files.extend(path for path in (root / "img").rglob("*") if path.suffix.lower() in IMAGE_SUFFIXES)
    for path in files:
        if not path.is_file() or path.is_symlink() or not path.resolve().is_relative_to(root):
            raise ValueError(f"Missing deployment file or symlink: {path}")
        if any((root / parent).is_symlink() for parent in path.relative_to(root).parents):
            raise ValueError(f"Deployment directory is a symlink: {path}")
    return sorted(set(files))


def validate_archive(output: Path) -> None:
    with ZipFile(output) as archive, output.open("rb") as stream:
        for entry in archive.infolist():
            stream.seek(entry.header_offset)
            header = stream.read(10)
            if len(header) != 10:
                raise ValueError(f"Truncated ZIP header: {entry.filename}")
            signature, _, flags, method = struct.unpack("<4sHHH", header)
            if (signature != b"PK\x03\x04" or method != ZIP_DEFLATED or flags & 9
                    or entry.compress_type != ZIP_DEFLATED or entry.flag_bits & 9):
                raise ValueError(f"Deployment entry must use DEFLATE without encryption or data descriptors: {entry.filename}")
        invalid = archive.testzip()
        if invalid is not None:
            raise ValueError(f"ZIP integrity check failed: {invalid}")


def build_archive(output: Path, root: Path = ROOT) -> Path:
    root = root.resolve()
    files = deployment_files(root)
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    created = False
    try:
        with ZipFile(output, "x", compression=ZIP_DEFLATED) as archive:
            created = True
            for path in files:
                archive.write(path, path.relative_to(root).as_posix(), compress_type=ZIP_DEFLATED)
        validate_archive(output)
    except BaseException:
        if created:
            output.unlink(missing_ok=True)
        raise
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a code-only deployment archive without data or credentials")
    parser.add_argument("--output", type=Path, default=ROOT / "dist" / f"exteraPluginsRobot-deploy-{datetime.now():%Y%m%d-%H%M%S}.zip")
    args = parser.parse_args()
    try:
        archive = build_archive(args.output)
    except (OSError, ValueError, BadZipFile) as exc:
        parser.exit(1, f"Deployment archive failed: {exc}\n")
    print(archive)


if __name__ == "__main__":
    main()
