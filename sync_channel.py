import asyncio
import argparse
import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


async def cmd_full_sync(args: argparse.Namespace) -> None:
    from userbot.client import get_userbot
    
    print("🔄 Начинаю синхронизацию канала...")
    print()
    
    try:
        userbot = await get_userbot()
        if not userbot:
            print("Юзербот не настроен")
            sys.exit(1)
        
        stats = await userbot.full_sync(limit=args.limit)
        
        print()
        print("Синхронизация завершена!")
        print(f"   📦 Плагинов: {stats.get('plugins', 0)}")
        print(f"   🎨 Иконпаков: {stats.get('icons', 0)}")
        print(f"   ⏭️  Пропущено: {stats.get('skipped', 0)}")
        print(f"   Ошибок: {stats.get('errors', 0)}")
        
    except Exception as e:
        logger.exception("Sync failed")
        print(f"Ошибка: {e}")
        sys.exit(1)


async def cmd_status(args: argparse.Namespace) -> None:
    from storage import load_plugins, load_icons
    
    plugins_db = load_plugins()
    icons_db = load_icons()
    
    plugins = plugins_db.get("plugins", [])
    icons = icons_db.get("iconpacks", [])
    
    published_plugins = [p for p in plugins if p.get("status") == "published"]
    published_icons = [i for i in icons if i.get("status") == "published"]
    
    categories = {}
    for p in published_plugins:
        cat = p.get("category") or "без категории"
        categories[cat] = categories.get(cat, 0) + 1
    
    print("📊 Статус базы данных:")
    print()
    print(f"   📦 Плагинов: {len(published_plugins)}")
    print(f"   🎨 Иконпаков: {len(published_icons)}")
    print()
    print("   📂 По категориям:")
    for cat, count in sorted(categories.items(), key=lambda x: -x[1]):
        print(f"      • {cat}: {count}")
    print()
    print(f"   📅 Обновлено: {plugins_db.get('updated_at', '?')}")


async def cmd_clear(args: argparse.Namespace) -> None:
    from storage import (
        flush_all,
        save_icons,
        save_plugins,
        save_requests,
        save_users,
    )

    if args.what in ("all", "plugins"):
        save_plugins({"plugins": []})
        print("Плагины очищены")
    
    if args.what in ("all", "icons"):
        save_icons({"iconpacks": []})
        print("Иконки очищены")
    
    if args.what in ("all", "requests"):
        save_requests({"requests": []})
        print("Заявки очищены")
    
    if args.what in ("all", "users"):
        save_users({"users": {}})
        print("Пользователи очищены")

    await flush_all()


def main() -> None:
    parser = argparse.ArgumentParser(description="Управление каталогом плагинов")
    subparsers = parser.add_subparsers(dest="command", required=True)
    
    sync_parser = subparsers.add_parser("sync", help="Синхронизация с каналом")
    sync_parser.add_argument("--limit", "-l", type=int, default=0, help="Лимит сообщений (0 = все)")
    sync_parser.set_defaults(func=cmd_full_sync)
    
    status_parser = subparsers.add_parser("status", help="Статус базы данных")
    status_parser.set_defaults(func=cmd_status)
    
    clear_parser = subparsers.add_parser("clear", help="Очистить базу данных")
    clear_parser.add_argument("what", choices=["all", "plugins", "icons", "requests", "users"], help="Что очистить")
    clear_parser.set_defaults(func=cmd_clear)
    
    args = parser.parse_args()
    
    if asyncio.iscoroutinefunction(args.func):
        asyncio.run(args.func(args))
    else:
        args.func(args)


if __name__ == "__main__":
    main()
