import ast
import binascii
import json
import re
import stat
import struct
import zipfile
import zlib
from contextlib import contextmanager
from pathlib import PurePosixPath

import yaml

from core.plugin_parser import PluginMetadata, PluginParseError, _version_only
from core.categories import normalize_categories
from core.plugin_formats import ELYX_LIMIT

_MAX_ENTRY = 8 * 1024 * 1024
_MAX_METADATA = 256 * 1024
_MAX_EXPANDED = 512 * 1024 * 1024
_MAX_FILES = 10000
_MAX_DIRECTORY = 8 * 1024 * 1024
_MAX_DEPTH = 32
_MAX_NODES = 10000
_CHUNK_SIZE = 64 * 1024


def _unique_mapping(pairs):
    result = {}
    for key, value in pairs:
        if not isinstance(key, str):
            raise PluginParseError("Metadata keys must be strings")
        if key in result:
            raise PluginParseError(f"Duplicate metadata key: {key}")
        result[key] = value
    return result


class _YamlLoader(yaml.SafeLoader):
    def compose_node(self, parent, index):
        if self.check_event(yaml.AliasEvent):
            raise PluginParseError("YAML aliases are not supported")
        self._nodes = getattr(self, "_nodes", 0) + 1
        if self._nodes > _MAX_NODES:
            raise PluginParseError("Too many YAML nodes")
        self._depth = getattr(self, "_depth", 0) + 1
        try:
            if self._depth > _MAX_DEPTH:
                raise PluginParseError("YAML nesting exceeds inspection limits")
            return super().compose_node(parent, index)
        finally:
            self._depth -= 1

    def construct_mapping(self, node, deep=False):
        self.flatten_mapping(node)
        return _unique_mapping((self.construct_object(key, deep=deep), self.construct_object(value, deep=deep))
                               for key, value in node.value)


def _path(value, *, directory=False):
    if directory and isinstance(value, str):
        value = value.removesuffix("/")
    if not isinstance(value, str) or not value or "\\" in value or any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise PluginParseError("Invalid archive path")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in value.split("/")) or ":" in value:
        raise PluginParseError("Unsafe archive path")
    if len(value.encode("utf-8")) > 1024:
        raise PluginParseError("Archive path is too long")
    return str(path)


def _preflight(stream):
    stream.seek(0, 2)
    size = stream.tell()
    if size > ELYX_LIMIT:
        raise PluginParseError("Elyx archive exceeds its file size limit")
    stream.seek(max(0, size - 65557))
    tail = stream.read()
    position = tail.rfind(b"PK\x05\x06")
    if position < 0 or position + 22 > len(tail):
        raise PluginParseError("Missing ZIP directory")
    _, disk, directory_disk, disk_count, count, directory_size, offset, comment_size = struct.unpack_from("<4s4H2IH", tail, position)
    if position + 22 + comment_size != len(tail) or disk or directory_disk or disk_count != count:
        raise PluginParseError("Invalid or multipart ZIP directory")
    directory_end = size - len(tail) + position
    if directory_end >= 20:
        stream.seek(directory_end - 20)
        locator = stream.read(20)
        if locator.startswith(b"PK\x06\x07"):
            _, disk64, position64, disks64 = struct.unpack("<4sIQI", locator)
            if disk64 or disks64 != 1 or position64 + 56 > directory_end - 20:
                raise PluginParseError("Invalid ZIP64 directory")
            stream.seek(position64)
            record = stream.read(56)
            signature, length, _, _, disk64, start64, disk_count64, count64, size64, offset64 = struct.unpack("<4sQ2H2I4Q", record)
            if (signature != b"PK\x06\x06" or length != 44 or position64 + 12 + length != directory_end - 20
                    or disk64 or start64 or disk_count64 != count64):
                raise PluginParseError("Invalid ZIP64 directory")
            count, directory_size, offset, directory_end = count64, size64, offset64, position64
    if not 0 < count <= _MAX_FILES or not 0 < directory_size <= _MAX_DIRECTORY:
        raise PluginParseError("ZIP directory exceeds inspection limits")
    start = directory_end - directory_size
    if start < 0 or offset > start:
        raise PluginParseError("Invalid ZIP directory offset")
    stream.seek(start)
    remaining, actual_count = directory_size, 0
    while remaining:
        header = stream.read(46)
        if len(header) != 46 or header[:4] != b"PK\x01\x02" or struct.unpack_from("<H", header, 34)[0]:
            raise PluginParseError("Invalid ZIP directory entry")
        entry_size = 46 + sum(struct.unpack_from("<3H", header, 28))
        actual_count += 1
        if entry_size > remaining or actual_count > _MAX_FILES:
            raise PluginParseError("ZIP directory exceeds inspection limits")
        stream.seek(entry_size - 46, 1)
        remaining -= entry_size
    if actual_count != count:
        raise PluginParseError("ZIP directory file count mismatch")
    stream.seek(0)


@contextmanager
def open_elyx_archive(path):
    with open(path, "rb") as stream:
        _preflight(stream)
        with zipfile.ZipFile(stream) as archive:
            infos = archive.infolist()
            if len(infos) > _MAX_FILES or sum(info.file_size for info in infos) > _MAX_EXPANDED:
                raise PluginParseError("Archive exceeds inspection limits")
            names, files = set(), set()
            for info in infos:
                normalized = _path(info.orig_filename[:-1] if info.is_dir() else info.orig_filename)
                if normalized in names:
                    raise PluginParseError("Duplicate archive paths")
                names.add(normalized)
                if info.flag_bits & (1 | 64):
                    raise PluginParseError("Encrypted archives cannot be reviewed; send an unencrypted build")
                mode = stat.S_IFMT(info.external_attr >> 16)
                if mode not in {0, stat.S_IFREG, stat.S_IFDIR}:
                    raise PluginParseError("Archive links and special files are not supported")
                if mode and (mode == stat.S_IFDIR) != info.is_dir():
                    raise PluginParseError("Archive file type does not match its path")
                if info.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}:
                    raise PluginParseError("Use ZIP with DEFLATE compression or no compression")
                if info.is_dir() and info.file_size:
                    raise PluginParseError("Archive directories must be empty")
                if info.compress_type == zipfile.ZIP_STORED and info.file_size != info.compress_size:
                    raise PluginParseError("Invalid uncompressed ZIP entry size")
                if not info.is_dir():
                    files.add(normalized)
            for name in names:
                if any(str(parent) in files for parent in PurePosixPath(name).parents):
                    raise PluginParseError("Archive path contains a regular file as a directory")
            yield archive


def member_chunks(archive, info, limit):
    if info.file_size > limit:
        raise PluginParseError(f"Archive file too large: {info.filename}")
    with archive.open(info):
        data_offset = archive.fp.tell()
    archive.fp.seek(info.header_offset)
    header = archive.fp.read(30)
    if (len(header) != 30 or struct.unpack_from("<H", header, 8)[0] != info.compress_type
            or struct.unpack_from("<H", header, 6)[0] != info.flag_bits):
        raise PluginParseError("ZIP local header does not match its directory")
    if not info.flag_bits & 8:
        checksum, compressed_size, file_size = struct.unpack_from("<3I", header, 14)
        if compressed_size == 0xffffffff or file_size == 0xffffffff:
            name_length, extra_length = struct.unpack_from("<2H", header, 26)
            archive.fp.seek(info.header_offset + 30 + name_length)
            extra = archive.fp.read(extra_length)
            position, values = 0, None
            while position + 4 <= len(extra):
                kind, length = struct.unpack_from("<2H", extra, position)
                position += 4
                if position + length > len(extra):
                    raise PluginParseError("Invalid ZIP64 local header")
                if kind == 1:
                    if values is not None:
                        raise PluginParseError("Duplicate ZIP64 local sizes")
                    values = extra[position:position + length]
                position += length
            if values is None:
                raise PluginParseError("Missing ZIP64 local sizes")
            position = 0
            if file_size == 0xffffffff:
                file_size = struct.unpack_from("<Q", values, position)[0]
                position += 8
            if compressed_size == 0xffffffff:
                compressed_size = struct.unpack_from("<Q", values, position)[0]
        if (checksum, compressed_size, file_size) != (info.CRC, info.compress_size, info.file_size):
            raise PluginParseError("ZIP local sizes or CRC do not match its directory")
    archive.fp.seek(data_offset)
    decoder = zlib.decompressobj(-15) if info.compress_type == zipfile.ZIP_DEFLATED else None
    remaining, expanded, checksum = info.compress_size, 0, 0
    maximum = min(limit, info.file_size)
    while remaining:
        compressed = archive.fp.read(min(_CHUNK_SIZE, remaining))
        if not compressed:
            raise PluginParseError("Truncated ZIP entry")
        remaining -= len(compressed)
        while compressed:
            chunk = decoder.decompress(compressed, min(_CHUNK_SIZE, maximum - expanded + 1)) if decoder else compressed
            compressed = decoder.unconsumed_tail if decoder else b""
            expanded += len(chunk)
            if expanded > maximum:
                raise PluginParseError(f"ZIP entry expands beyond its declared size: {info.filename}")
            checksum = binascii.crc32(chunk, checksum)
            if chunk:
                yield chunk
            if decoder and decoder.eof and (decoder.unused_data or compressed or remaining):
                raise PluginParseError("Unexpected data after compressed ZIP entry")
    if expanded != info.file_size or checksum != info.CRC or (decoder and not decoder.eof):
        raise PluginParseError(f"Corrupted ZIP entry: {info.filename}")


def _read(archive, path, limit=_MAX_METADATA):
    try:
        info = archive.getinfo(path)
    except KeyError as exc:
        raise PluginParseError(f"Missing archive file: {path}") from exc
    return b"".join(member_chunks(archive, info, limit))


def _metadata_limits(value):
    pending, nodes = [(value, 0)], 0
    while pending:
        item, depth = pending.pop()
        nodes += 1
        if nodes > _MAX_NODES or depth > _MAX_DEPTH:
            raise PluginParseError("Metadata structure exceeds inspection limits")
        if isinstance(item, dict):
            if any(not isinstance(key, str) for key in item):
                raise PluginParseError("Metadata keys must be strings")
            pending.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, (list, tuple)):
            pending.extend((child, depth + 1) for child in item)
        elif item is not None and not isinstance(item, (str, bool, int, float)):
            raise PluginParseError("Metadata contains unsupported values")
    return value


def _mapping(archive, path):
    data = _read(archive, path).decode("utf-8")
    if path.endswith(".py"):
        tree = ast.parse(data)
        result = {}
        for node in tree.body:
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    if isinstance(target, ast.Name):
                        if target.id in result:
                            raise PluginParseError(f"Duplicate metadata key: {target.id}")
                        try:
                            result[target.id] = ast.literal_eval(node.value)
                        except (ValueError, TypeError) as exc:
                            if target.id.strip("_") == "category":
                                raise PluginParseError("Category metadata must be a literal list") from exc
        return _metadata_limits(result)
    if path.endswith(".json"):
        result = json.loads(data, object_pairs_hook=_unique_mapping)
    elif path.endswith((".yaml", ".yml")):
        result = yaml.load(data, Loader=_YamlLoader)
    else:
        raise PluginParseError("Metadata must be YAML, JSON or literal Python assignments")
    if not isinstance(result, dict):
        raise PluginParseError(f"Expected an object in {path}")
    return _metadata_limits(result)


def _localized_text(value, strings):
    parts, size, start = [], 0, 0
    for match in re.finditer(r"\{([^{}]+)\}", value):
        replacement = strings.get(match[1], match[1])
        if not isinstance(replacement, (str, int, float, bool)):
            raise PluginParseError("Localized metadata placeholders must resolve to scalar values")
        for part in (value[start:match.start()], str(replacement)):
            size += len(part.encode("utf-8"))
            if size > _MAX_METADATA:
                raise PluginParseError("Localized metadata exceeds inspection limits")
            parts.append(part)
        start = match.end()
    tail = value[start:]
    if size + len(tail.encode("utf-8")) > _MAX_METADATA:
        raise PluginParseError("Localized metadata exceeds inspection limits")
    return "".join(parts) + tail


def parse_elyx_file(path, fallback_version=None):
    try:
        return _parse(path, fallback_version)
    except PluginParseError:
        raise
    except (zipfile.BadZipFile, UnicodeError, yaml.YAMLError, ValueError, TypeError, SyntaxError, RuntimeError,
            NotImplementedError, RecursionError, EOFError, struct.error, zlib.error) as exc:
        raise PluginParseError(f"Invalid Elyx archive: {exc}") from exc


def _parse(path, fallback_version):
    with open_elyx_archive(path) as archive:
        infos = archive.infolist()
        names = {info.filename.rstrip("/") for info in infos}
        ref_path = next((n for n in ("refmap.yaml", "refmap.yml", "refmap.json") if n in names), None)
        refs = _mapping(archive, ref_path) if ref_path else {}
        main = _path(refs.get("main") or "main.py")
        if main not in names and main.endswith(".py") and main + "c" in names:
            main += "c"
        if main not in names or not main.endswith((".py", ".pyc")):
            raise PluginParseError("Missing Python entry point (.py or .pyc)")
        meta_path = refs.get("metainfo") or next((n for n in ("metainfo.yaml", "metainfo.yml", "metainfo.json") if n in names), None)
        if not meta_path:
            raise PluginParseError("Missing metainfo or refmap.metainfo")
        meta = _mapping(archive, _path(meta_path))
        fields = _unique_mapping((key.strip("_"), value) for key, value in meta.items())
        categories = normalize_categories(fields.get("category"), strict=True)
        if not fields.get("version") and fallback_version:
            fields["version"] = fallback_version
        missing = [k for k in ("id", "name", "author", "version") if not isinstance(fields.get(k), str) or not fields[k].strip()]
        if missing:
            raise PluginParseError("Missing string metadata fields: " + ", ".join(missing))
        if not re.fullmatch(r"[A-Za-z0-9_]{2,32}", fields["id"]):
            raise PluginParseError("Elyx id must contain 2–32 ASCII letters, digits or underscores")
        minimum = fields.get("min_version") or ""
        app = fields.get("app_version") or ""
        if not isinstance(minimum, str) or not isinstance(app, str) or not (minimum or app):
            raise PluginParseError("Specify min_version or app_version as a string")
        locales = {}
        strings_dir = refs.get("strings") or ("strings" if any(n == "strings" or n.startswith("strings/") for n in names) else None)
        for key in ("strings", "assets", "wheels"):
            if refs.get(key):
                folder = _path(refs[key], directory=True)
                if folder not in names and not any(n.startswith(folder + "/") for n in names):
                    raise PluginParseError(f"Missing {key} directory: {folder}")
        if strings_dir:
            locale_size = 0
            folder = _path(strings_dir, directory=True) + "/"
            for name in sorted(names):
                if (name.startswith(folder) or name == strings_dir) and name.endswith((".json", ".yaml", ".yml", ".py")):
                    stem = PurePosixPath(name).stem
                    locale = stem.rsplit("_", 1)[-1] if "_" in stem else "en"
                    if locale in ("ru", "en"):
                        locale_size += archive.getinfo(name).file_size
                        if locale_size > 4 * 1024 * 1024:
                            raise PluginParseError("Localization metadata exceeds inspection limits")
                        locales.setdefault(locale, {}).update(_mapping(archive, name))
        localized = {}
        for locale in ("ru", "en"):
            strings = {**locales.get("en", {}), **locales.get(locale, {})}
            localized[locale] = {}
            for key in ("name", "description"):
                value = fields.get(key) or ""
                if not isinstance(value, str):
                    raise PluginParseError(f"{key} must be a string")
                localized[locale][key] = _localized_text(value, strings)
        has_settings = False
        compiled = False
        sources_size = 0
        for info in infos:
            if info.filename.endswith(".pyc"):
                compiled = True
                magic = b""
                for chunk in member_chunks(archive, info, _MAX_EXPANDED):
                    if not magic:
                        magic = chunk[:4]
                if magic != b"\xa7\x0d\x0d\x0a" or info.file_size < 16:
                    raise PluginParseError("Compiled modules must target Python 3.11")
            elif info.filename.endswith(".py"):
                sources_size += info.file_size
                if sources_size > 32 * 1024 * 1024:
                    raise PluginParseError("Too much Python source to inspect")
                source = _read(archive, info.filename, _MAX_ENTRY).decode("utf-8")
                has_settings |= bool(re.search(r"\b(?:create_settings|ui\.settings)\b", source))
            else:
                for _ in member_chunks(archive, info, _MAX_EXPANDED):
                    pass
        for key in ("sdk_version", "elyx_version", "icon", "link"):
            if fields.get(key) is not None and not isinstance(fields[key], str):
                raise PluginParseError(f"{key} must be a string")
        requirements = fields.get("requirements") or ""
        requires = fields.get("requires") or {}
        if not isinstance(requirements, str) or not isinstance(requires, dict):
            raise PluginParseError("requirements must be a string and requires must be an object")
        if any(not isinstance(k, str) or (v is not None and not isinstance(v, str)) for k, v in requires.items()):
            raise PluginParseError("Plugin dependencies must use string ids and links")
        return PluginMetadata(
            id=fields["id"], name=localized["en"]["name"], description=localized["en"]["description"],
            author=fields["author"], version=fields["version"], min_version=minimum or _version_only(app),
            has_ui_settings=has_settings, raw_text=json.dumps(fields, ensure_ascii=False, default=str), app_version=app,
            categories=categories,
            optional={"sdk_version": fields.get("sdk_version") or "", "elyx_version": fields.get("elyx_version") or "", "requirements": requirements,
                      "requires": requires, "localized": localized, "compiled": compiled,
                      "main": main, "metainfo": meta_path, "icon": fields.get("icon"), "link": fields.get("link")},
        )
