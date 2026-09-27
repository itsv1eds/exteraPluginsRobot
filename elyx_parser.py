import ast
import json
import re
import stat
import zipfile
from pathlib import PurePosixPath

import yaml

from plugin_parser import PluginMetadata, PluginParseError, _version_only

_MAX_ENTRY = 8 * 1024 * 1024
_MAX_METADATA = 256 * 1024
_MAX_EXPANDED = 512 * 1024 * 1024
_MAX_FILES = 10000


class _YamlLoader(yaml.SafeLoader):
    def compose_node(self, parent, index):
        if self.check_event(yaml.AliasEvent):
            raise PluginParseError("YAML aliases are not supported")
        self._nodes = getattr(self, "_nodes", 0) + 1
        if self._nodes > 10000:
            raise PluginParseError("Too many YAML nodes")
        return super().compose_node(parent, index)


def _path(value):
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise PluginParseError("Invalid archive path")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or ":" in value:
        raise PluginParseError("Unsafe archive path")
    return str(path)


def _read(archive, path, limit=_MAX_METADATA):
    try:
        info = archive.getinfo(path)
    except KeyError as exc:
        raise PluginParseError(f"Missing archive file: {path}") from exc
    if info.file_size > limit:
        raise PluginParseError(f"Archive file too large: {path}")
    with archive.open(info) as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise PluginParseError(f"Archive file too large: {path}")
    return data


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
                        try:
                            result[target.id] = ast.literal_eval(node.value)
                        except (ValueError, TypeError):
                            pass
        return result
    if path.endswith(".json"):
        result = json.loads(data)
    elif path.endswith((".yaml", ".yml")):
        result = yaml.load(data, Loader=_YamlLoader)
    else:
        raise PluginParseError("Metadata must be YAML, JSON or literal Python assignments")
    if not isinstance(result, dict):
        raise PluginParseError(f"Expected an object in {path}")
    return result


def parse_elyx_file(path, fallback_version=None):
    try:
        return _parse(path, fallback_version)
    except PluginParseError:
        raise
    except (zipfile.BadZipFile, UnicodeError, yaml.YAMLError, ValueError, SyntaxError, RuntimeError, NotImplementedError, RecursionError) as exc:
        raise PluginParseError(f"Invalid Elyx archive: {exc}") from exc


def _parse(path, fallback_version):
    with zipfile.ZipFile(path) as archive:
        infos = archive.infolist()
        if len(infos) > _MAX_FILES or sum(i.file_size for i in infos) > _MAX_EXPANDED:
            raise PluginParseError("Archive exceeds inspection limits")
        names = set()
        for info in infos:
            normalized = _path(info.filename.rstrip("/"))
            if normalized in names:
                raise PluginParseError("Duplicate archive paths")
            names.add(normalized)
            if info.flag_bits & 1:
                raise PluginParseError("Encrypted archives cannot be reviewed; send an unencrypted build")
            if stat.S_ISLNK(info.external_attr >> 16):
                raise PluginParseError("Archive symlinks are not supported")
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
        fields = {str(k).strip("_"): v for k, v in meta.items()}
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
                folder = _path(refs[key]).rstrip("/")
                if folder not in names and not any(n.startswith(folder + "/") for n in names):
                    raise PluginParseError(f"Missing {key} directory: {folder}")
        if strings_dir:
            locale_size = 0
            folder = _path(strings_dir).rstrip("/") + "/"
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
                localized[locale][key] = re.sub(r"\{([^{}]+)\}", lambda m: str(strings.get(m[1], m[1])), value)
        has_settings = False
        compiled = False
        sources_size = 0
        for info in infos:
            if info.filename.endswith(".pyc"):
                compiled = True
                with archive.open(info) as stream:
                    magic = stream.read(4)
                if magic != b"\xa7\x0d\x0d\x0a" or info.file_size < 16:
                    raise PluginParseError("Compiled modules must target Python 3.11")
            elif info.filename.endswith(".py"):
                sources_size += info.file_size
                if sources_size > 32 * 1024 * 1024:
                    raise PluginParseError("Too much Python source to inspect")
                source = _read(archive, info.filename, _MAX_ENTRY).decode("utf-8")
                has_settings |= bool(re.search(r"\b(?:create_settings|ui\.settings)\b", source))
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
            optional={"sdk_version": fields.get("sdk_version") or "", "elyx_version": fields.get("elyx_version") or "", "requirements": requirements,
                      "requires": requires, "localized": localized, "compiled": compiled,
                      "main": main, "metainfo": meta_path, "icon": fields.get("icon"), "link": fields.get("link")},
        )
