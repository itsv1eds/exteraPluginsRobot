import asyncio
import logging
import pathlib
from telethon import TelegramClient
from telethon.errors import (
    SessionPasswordNeededError,
    FloodWaitError,
    PhoneCodeInvalidError,
    PhoneCodeExpiredError,
    PhoneNumberBannedError,
)
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from storage import load_config

CONFIG = load_config()

async def authorize():
    userbot_config = CONFIG.get("userbot", {})
    api_id = userbot_config.get("api_id")
    api_hash = userbot_config.get("api_hash")
   
    if not api_id or not api_hash:
        print("❌ Не настроены api_id и api_hash в config.json")
        print("   Создай новые на https://my.telegram.org → API development tools")
        return
   
    session_dir = Path("sessions")
    session_dir.mkdir(parents=True, exist_ok=True)
   
    session_path = str(session_dir / "userbot_session")
   
    client = TelegramClient(session_path, int(api_id), str(api_hash))
   
    print("🔐 Авторизация юзербота...")
    print()
   
    await client.connect()
   
    if await client.is_user_authorized():
        me = await client.get_me()
        print(f"✅ Уже авторизован как: {me.first_name} (@{me.username or 'нет'})")
        await client.disconnect()
        return
   
    phone = input("📱 Введите номер телефона (с +): ").strip()
   
    # Попытки запроса кода (на случай временных ошибок)
    for attempt in range(1, 4):
        try:
            print(f"🔄 Попытка {attempt}/3: запрос кода...")
            sent_code = await client.send_code_request(
                phone,
                force_sms=True,  # Пытаемся заставить SMS (редко работает, но пробуем)
            )
            print("✅ Запрос кода прошёл успешно!")
            print(f"   Тип доставки: {sent_code.type}")
            print(f"   Таймаут: {sent_code.timeout} секунд")
            if sent_code.type == "app":
                print("   → Код должен прийти в чат 'Telegram' в официальном приложении")
            elif sent_code.type == "sms":
                print("   → Код должен прийти по SMS")
            break  # Успех — выходим из цикла
        except FloodWaitError as e:
            print(f"⏳ FloodWait: нужно подождать {e.seconds} секунд")
            await asyncio.sleep(e.seconds + 10)
        except PhoneNumberBannedError:
            print("🚫 Номер забанен в Telegram")
            return
        except Exception as e:
            print(f"❌ Ошибка при запросе кода: {type(e).__name__}: {e}")
            if attempt < 3:
                print("   Повторяем через 30 секунд...")
                await asyncio.sleep(30)
            else:
                print("   Слишком много ошибок. Проверь api_id/api_hash и номер.")
                return
    else:
        print("❌ Не удалось запросить код после 3 попыток.")
        return

    print()
    print("📨 Ожидаем код...")
   
    for _ in range(5):  # 5 попыток ввода кода
        code = input("🔢 Введите код из Telegram (или SMS): ").strip()
       
        try:
            await client.sign_in(phone, code)
            break
        except SessionPasswordNeededError:
            password = input("🔑 Введите 2FA пароль: ").strip()
            await client.sign_in(password=password)
            break
        except PhoneCodeInvalidError:
            print("❌ Неверный код. Попробуйте ещё раз.")
        except PhoneCodeExpiredError:
            print("❌ Код истёк. Запустите скрипт заново.")
            return
        except FloodWaitError as e:
            print(f"⏳ FloodWait при входе: подождите {e.seconds} секунд")
            await asyncio.sleep(e.seconds + 10)
        except Exception as e:
            print(f"❌ Ошибка при входе: {type(e).__name__}: {e}")
            return
   
    me = await client.get_me()
    print()
    print(f"✅ Авторизован как: {me.first_name} (@{me.username or 'нет'})")
    print(f"   ID: {me.id}")
    print()
    print("Сессия сохранена в sessions/userbot_session.session")
    print("   Если код всё равно не приходит — создай НОВЫЕ api_id и api_hash!")
   
    await client.disconnect()

if __name__ == "__main__":
    asyncio.run(authorize())