from pathlib import Path

PLUGIN_LIMIT = 8 * 1024 * 1024
ELYX_LIMIT = 100 * 1024 * 1024
ELYX_EXTENSIONS = ("elyx.zip", "eaf.zip", "elyx", "eaf")


def plugin_extension(filename: str | Path) -> str:
    name = str(filename).lower()
    for extension in (*ELYX_EXTENSIONS, "plugin"):
        if name.endswith("." + extension):
            return extension
    return ""


def plugin_file_limit(filename: str | Path) -> int:
    extension = plugin_extension(filename)
    return ELYX_LIMIT if extension in ELYX_EXTENSIONS else PLUGIN_LIMIT


def payload_extension(plugin: dict) -> str:
    return plugin_extension(plugin.get("file_name") or "") or plugin_extension(plugin.get("file_path") or "") or str(plugin.get("file_extension") or "plugin")
