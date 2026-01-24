import argparse
import textwrap
from pathlib import Path

from plugin_parser import PluginParseError, parse_plugin_file


def cmd_preview(args: argparse.Namespace) -> None:
    metadata = parse_plugin_file(args.plugin)
    print(f"File: {Path(args.plugin).name}")
    print("ID:", metadata.id)
    print("Version:", metadata.version)
    print("Has settings:", "✅" if metadata.has_ui_settings else "❌")

    template = metadata.as_post_template()
    print("\nTemplate draft:\n")
    lines = [f"Название: {template['Название']}", f"Автор: {template['Автор']}"]
    lines.append(f"Описание: {template['Описание']}")
    lines.append("Использование: <добавьте вручную>")
    lines.append(f"Настройки: {template['Настройки']}")
    lines.append(f"Минимальная версия: {template['Минимальная версия']}")
    text = "\n".join(lines)
    print(textwrap.fill(text, width=120, replace_whitespace=False))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Tools for working with plugins")
    subparsers = parser.add_subparsers(dest="command", required=True)

    preview = subparsers.add_parser("preview", help="Show metadata template for plugin file")
    preview.add_argument("plugin", help="Path to .plugin file")
    preview.set_defaults(func=cmd_preview)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    try:
        args.func(args)
    except PluginParseError as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
