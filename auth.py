import argparse
import asyncio
import getpass
import os
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from telethon import TelegramClient
from telethon.errors import (
    AuthKeyDuplicatedError,
    FloodWaitError,
    PasswordHashInvalidError,
    PhoneCodeExpiredError,
    PhoneCodeInvalidError,
    PhoneNumberBannedError,
    SessionPasswordNeededError,
)

from storage import flush_all, load_config, save_config


def parse_args():
    parser = argparse.ArgumentParser(description="Авторизация отдельной сессии юзербота")
    session = parser.add_mutually_exclusive_group()
    session.add_argument("--new-session", action="store_true")
    session.add_argument("--session-name")
    parser.add_argument("--session-dir", type=Path)
    parser.add_argument("--activate", action="store_true", help="Сохранить новую сессию в конфигурации после входа")
    return parser.parse_args()


async def authorize(args):
    config = load_config()
    userbot = config.get("userbot", {})
    file_bot = config.get("bot_mtproto", {})
    api_id = userbot.get("api_id") or file_bot.get("api_id")
    api_hash = userbot.get("api_hash") or file_bot.get("api_hash")
    if not api_id or not api_hash:
        print("В конфигурации SQLite не настроены api_id и api_hash. Получите их на https://my.telegram.org.")
        return False
    session_dir = (args.session_dir or Path(userbot.get("session_dir") or "sessions")).resolve()
    session_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    if args.new_session:
        session_name = f"userbot_{datetime.now():%Y%m%d_%H%M%S}_{uuid4().hex[:8]}"
    else:
        session_name = str(args.session_name or userbot.get("session_name") or "userbot_session")
    if Path(session_name).name != session_name:
        raise ValueError("Некорректное имя сессии")
    session_path = session_dir / session_name
    client = TelegramClient(str(session_path), int(api_id), str(api_hash))
    try:
        await asyncio.wait_for(client.connect(), timeout=30)
        if not await client.is_user_authorized():
            phone = input("Номер телефона (+...): ").strip()
            sent_code = await client.send_code_request(phone)
            print(f"Код запрошен. Способ доставки: {type(sent_code.type).__name__}.")
            signed_in = False
            for _ in range(5):
                code = getpass.getpass("Код Telegram (ввод скрыт): ").strip().replace(" ", "")
                try:
                    await client.sign_in(phone=phone, code=code, phone_code_hash=sent_code.phone_code_hash)
                    signed_in = True
                    break
                except PhoneCodeInvalidError:
                    print("Неверный код. Попробуйте ещё раз.")
                except SessionPasswordNeededError:
                    for _ in range(3):
                        password = getpass.getpass("Пароль двухэтапной защиты (ввод скрыт): ")
                        try:
                            await client.sign_in(password=password)
                            signed_in = True
                            break
                        except PasswordHashInvalidError:
                            print("Неверный пароль.")
                    break
            if not signed_in:
                print("Вход не завершён. Конфигурация не изменена.")
                return False
        me = await client.get_me()
        if me is None or me.bot:
            print("Нужна сессия пользовательского аккаунта Telegram.")
            return False
        print(f"Вход выполнен: @{me.username}" if me.username else f"Вход выполнен: {me.first_name}")
        print(f"Сессия: {session_path}.session")
        if args.activate:
            config["userbot"] = {
                **userbot,
                "api_id": int(api_id),
                "api_hash": str(api_hash),
                "session_dir": str(session_dir),
                "session_name": session_name,
            }
            save_config(config)
            await flush_all()
            print("Новая сессия сохранена в конфигурации. Теперь перезапустите бота.")
        return True
    except AuthKeyDuplicatedError:
        print("Ключ сессии отозван. Запустите вход с --new-session. Не копируйте сессию между работающими экземплярами.")
    except PhoneCodeExpiredError:
        print("Код истёк. Запустите авторизацию заново.")
    except PhoneNumberBannedError:
        print("Telegram заблокировал вход для этого номера.")
    except FloodWaitError as exc:
        print(f"Telegram требует подождать {exc.seconds} секунд перед следующей попыткой.")
    finally:
        await client.disconnect()
        for suffix in (".session", ".session-journal"):
            path = Path(str(session_path) + suffix)
            if path.exists():
                os.chmod(path, 0o600)
    return False


if __name__ == "__main__":
    try:
        success = asyncio.run(authorize(parse_args()))
    except (KeyboardInterrupt, EOFError):
        print("\nАвторизация отменена.")
        success = False
    except Exception as exc:
        print(f"Авторизация не завершена: {type(exc).__name__}. Конфигурация не изменена.")
        success = False
    raise SystemExit(0 if success else 1)
