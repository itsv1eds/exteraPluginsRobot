import ast
import hashlib
import json
import os
import tempfile
import zipfile
from copy import copy
from pathlib import Path

import yaml

from core.categories import normalize_categories
from core.plugin_formats import ELYX_EXTENSIONS, plugin_extension, plugin_file_limit
from core.plugin_parser import PluginParseError


def embed_category_text(text: str, categories, *, field="__category__") -> str:
    keys = normalize_categories(categories, strict=True)
    tree = ast.parse(text)
    existing = []
    anchor = None
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        names = [target.id for target in targets if isinstance(target, ast.Name)]
        if field in names:
            if len(targets) != 1:
                raise PluginParseError("Category metadata must use a separate assignment")
            existing.append(node)
        if "__app_version__" in names or "app_version" in names:
            anchor = node
    literal = json.dumps(keys, ensure_ascii=False)
    if existing:
        if all(ast.literal_eval(node.value) == keys for node in existing):
            return text
        encoded = text.encode("utf-8")
        lines = encoded.splitlines(keepends=True)
        offsets = [0]
        for line in lines:
            offsets.append(offsets[-1] + len(line))
        for node in reversed(existing):
            start = offsets[node.value.lineno - 1] + node.value.col_offset
            end = offsets[node.value.end_lineno - 1] + node.value.end_col_offset
            encoded = encoded[:start] + literal.encode("utf-8") + encoded[end:]
        result = encoded.decode("utf-8")
    else:
        lines = text.splitlines(keepends=True)
        newline = "\r\n" if "\r\n" in text else "\n"
        if anchor is None:
            metadata = []
            for node in tree.body:
                if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                    continue
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                if any(isinstance(target, ast.Name) and (field != "__category__" or target.id.startswith("__")) for target in targets):
                    metadata.append(node)
            anchor = metadata[-1] if metadata else None
        at = anchor.end_lineno if anchor else 0
        if at and not lines[at - 1].endswith(("\n", "\r")):
            lines[at - 1] += newline
        lines.insert(at, f"{field} = {literal}{newline}")
        result = "".join(lines)
    ast.parse(result)
    return result


def write_plugin_categories(path: str | Path, categories, *, versions_dir: Path | None = None) -> bool:
    path = Path(path)
    keys = normalize_categories(categories, strict=True)
    if not keys:
        raise PluginParseError("Choose at least one plugin category")
    extension = plugin_extension(path)
    if extension not in (*ELYX_EXTENSIONS, "plugin"):
        raise PluginParseError("Unsupported plugin extension")
    descriptor, temporary = tempfile.mkstemp(prefix=".categories-", suffix="." + extension, dir=path.parent)
    os.close(descriptor)
    temporary = Path(temporary)
    try:
        if extension == "plugin":
            original = path.read_bytes()
            updated = embed_category_text(original.decode("utf-8"), keys).encode("utf-8")
            if original == updated:
                return False
            temporary.write_bytes(updated)
        else:
            from core.elyx_parser import _mapping, _path, _read, member_chunks, open_elyx_archive, parse_elyx_file

            metadata = parse_elyx_file(path)
            with open_elyx_archive(path) as source:
                target = _path(metadata.optional["metainfo"])
                values = _mapping(source, target)
                if values.get("__category__", values.get("category")) == keys:
                    return False
                field = "__category__" if "__category__" in values else "category"
                values[field] = keys
                if field == "__category__":
                    values.pop("category", None)
                else:
                    values.pop("__category__", None)
                original = _read(source, target)
                if target.endswith(".py"):
                    updated = embed_category_text(original.decode("utf-8"), keys, field=field).encode("utf-8")
                elif target.endswith(".json"):
                    updated = (json.dumps(values, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
                else:
                    updated = yaml.safe_dump(values, allow_unicode=True, sort_keys=False).encode("utf-8")
                if original == updated:
                    return False
                with zipfile.ZipFile(temporary, "w") as destination:
                    destination.comment = source.comment
                    for info in source.infolist():
                        if info.filename == target:
                            destination.writestr(copy(info), updated)
                        else:
                            with destination.open(copy(info), "w") as writer:
                                for chunk in member_chunks(source, info, info.file_size):
                                    writer.write(chunk)
                                    if destination.fp.tell() > plugin_file_limit(path):
                                        raise PluginParseError("Plugin file exceeds its size limit after adding categories")
            if parse_elyx_file(temporary).categories != keys:
                raise PluginParseError("Failed to save Elyx categories")
        if temporary.stat().st_size > plugin_file_limit(path):
            raise PluginParseError("Plugin file exceeds its size limit after adding categories")
        os.chmod(temporary, path.stat().st_mode & 0o777)
        if versions_dir is not None:
            with path.open("rb") as source:
                digest = hashlib.file_digest(source, "sha256").hexdigest()
            retained = versions_dir / digest / path.name
            retained.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.link(path, retained)
            except FileExistsError:
                pass
        os.replace(temporary, path)
        return True
    finally:
        temporary.unlink(missing_ok=True)
