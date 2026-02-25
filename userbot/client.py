import asyncio
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from telethon import TelegramClient
from telethon.tl.types import DocumentAttributeFilename, Message, MessageEntityBlockquote
from telethon.extensions import html as telethon_html

from channel_parser import parse_channel_post
from storage import load_plugins, save_plugins, load_icons, save_icons, load_config
from bot.cache import invalidate
from catalog import invalidate_catalog_cache

logger = logging.getLogger(__name__)

CONFIG = load_config()
SYNC_CHANNEL_USERNAME = CONFIG.get("channel", {}).get("username", "exteraPluginsSup")
SYNC_CHANNEL_ID = CONFIG.get("channel", {}).get("id", -1003869091631)
ICONS_CHANNEL_USERNAME = CONFIG.get("icons_channel", {}).get("username", "exteraIcons")
ICONS_CHANNEL_ID = CONFIG.get("icons_channel", {}).get("id", None)


def _invalidate_all() -> None:
    invalidate("plugins")
    invalidate("icons")
    invalidate_catalog_cache()


class UserbotClient:
    _instance: Optional["UserbotClient"] = None
    _lock = asyncio.Lock()
    
    def __init__(
        self,
        api_id: int,
        api_hash: str,
        session_name: str = "userbot_session",
        session_dir: Path = Path("sessions"),
    ):
        self.api_id = api_id
        self.api_hash = api_hash
        self.session_dir = session_dir
        self.session_dir.mkdir(parents=True, exist_ok=True)
        
        session_path = str(self.session_dir / session_name)
        self.client = TelegramClient(session_path, api_id, api_hash)
        self._publish_entity = None
        self._sync_entity = None
        self._icons_publish_entity = None
        self._icons_sync_entity = None
        self._started = False
        self._disabled = False
    
    @classmethod
    async def get_instance(cls) -> Optional["UserbotClient"]:
        async with cls._lock:
            if cls._instance is None:
                userbot_config = CONFIG.get("userbot", {})
                api_id = userbot_config.get("api_id")
                api_hash = userbot_config.get("api_hash")
                
                if not api_id or not api_hash:
                    logger.warning("Userbot credentials not configured")
                    return None
                
                cls._instance = cls(int(api_id), str(api_hash))
            
            if not cls._instance._started:
                started = await cls._instance.start()
                if not started:
                    return None
            
            return cls._instance
    
    async def start(self) -> bool:
        if self._started:
            return True
        if self._disabled:
            return False

        await self.client.connect()
        if not await self.client.is_user_authorized():
            self._disabled = True
            await self.client.disconnect()
            logger.warning(
                "Userbot session is not authorized. Run `python auth.py` "
                "or `docker compose run --rm --profile tools auth` first."
            )
            return False

        self._started = True
        logger.info("Userbot started")
        return True
    
    async def stop(self) -> None:
        if self._started:
            await self.client.disconnect()
            self._started = False
            logger.info("Userbot stopped")
    
    async def get_publish_entity(self):
        if not self._publish_entity:
            try:
                self._publish_entity = await self.client.get_entity(SYNC_CHANNEL_ID)
            except Exception:
                channel = CONFIG.get("publish_channel", "exteraPluginsSup")
                self._publish_entity = await self.client.get_entity(channel)
        return self._publish_entity
    
    async def get_sync_entity(self):
        if not self._sync_entity:
            try:
                self._sync_entity = await self.client.get_entity(SYNC_CHANNEL_ID)
            except Exception:
                self._sync_entity = await self.client.get_entity(SYNC_CHANNEL_USERNAME)
        return self._sync_entity

    async def get_icons_publish_entity(self):
        if not self._icons_publish_entity:
            try:
                if ICONS_CHANNEL_ID:
                    self._icons_publish_entity = await self.client.get_entity(ICONS_CHANNEL_ID)
                else:
                    raise ValueError("Missing icons channel ID")
            except Exception:
                self._icons_publish_entity = await self.client.get_entity(ICONS_CHANNEL_USERNAME)
        return self._icons_publish_entity

    async def get_icons_sync_entity(self):
        if not self._icons_sync_entity:
            try:
                if ICONS_CHANNEL_ID:
                    self._icons_sync_entity = await self.client.get_entity(ICONS_CHANNEL_ID)
                else:
                    raise ValueError("Missing icons channel ID")
            except Exception:
                self._icons_sync_entity = await self.client.get_entity(ICONS_CHANNEL_USERNAME)
        return self._icons_sync_entity

    def _parse_html(self, text: str) -> tuple[str, list]:
        start_token = "\uFFF0BQ_START\uFFF0"
        start_expandable_token = "\uFFF0BQ_EXP_START\uFFF0"
        end_token = "\uFFF0BQ_END\uFFF0"
        tokenized = text.replace("<blockquote expandable>", start_expandable_token)
        tokenized = tokenized.replace("<blockquote>", start_token)
        tokenized = tokenized.replace("</blockquote>", end_token)

        parsed_text, entities = telethon_html.parse(tokenized)

        kept = [True] * len(parsed_text)
        blockquote_ranges = []
        idx = 0
        while True:
            expand_idx = parsed_text.find(start_expandable_token, idx)
            plain_idx = parsed_text.find(start_token, idx)
            if expand_idx == -1 and plain_idx == -1:
                break
            if plain_idx == -1 or (expand_idx != -1 and expand_idx < plain_idx):
                start_idx = expand_idx
                token_len = len(start_expandable_token)
                expandable = True
            else:
                start_idx = plain_idx
                token_len = len(start_token)
                expandable = False
            end_idx = parsed_text.find(end_token, start_idx + token_len)
            if end_idx == -1:
                break

            blockquote_ranges.append((start_idx + token_len, end_idx, expandable))
            for i in range(start_idx, start_idx + token_len):
                kept[i] = False
            for i in range(end_idx, end_idx + len(end_token)):
                kept[i] = False
            idx = end_idx + len(end_token)

        prefix = [0] * (len(parsed_text) + 1)
        for i, flag in enumerate(kept):
            prefix[i + 1] = prefix[i] + (1 if flag else 0)

        cleaned_text = "".join(ch for i, ch in enumerate(parsed_text) if kept[i])

        adjusted_entities = []
        for entity in entities:
            start = getattr(entity, "offset", 0)
            length = getattr(entity, "length", 0)
            new_offset = prefix[start]
            new_length = prefix[start + length] - prefix[start]
            if new_length <= 0:
                continue
            entity.offset = new_offset
            entity.length = new_length
            adjusted_entities.append(entity)

        for start, end, expandable in blockquote_ranges:
            length = prefix[end] - prefix[start]
            if length > 0:
                if expandable:
                    try:
                        entity = MessageEntityBlockquote(
                            offset=prefix[start],
                            length=length,
                            collapsed=True,
                        )
                    except TypeError:
                        entity = MessageEntityBlockquote(offset=prefix[start], length=length)
                        if getattr(entity, "collapsed", None) is not None:
                            try:
                                entity.collapsed = True
                            except Exception:
                                pass
                else:
                    entity = MessageEntityBlockquote(offset=prefix[start], length=length)
                adjusted_entities.append(entity)

        return cleaned_text, adjusted_entities
    
    async def publish_plugin(
        self,
        text: str,
        file_path: Optional[str] = None,
    ) -> Dict[str, Any]:
        entity = await self.get_publish_entity()

        if file_path and Path(file_path).exists():
            message = await self.client.send_file(
                entity,
                file=file_path,
                caption=text,
                parse_mode="html",
            )
        else:
            message = await self.client.send_message(
                entity,
                text,
                parse_mode="html",
                link_preview=False,
            )
        
        channel_username = CONFIG.get("publish_channel", "xzcvzxa")
        
        return {
            "message_id": message.id,
            "chat_id": entity.id,
            "link": f"https://t.me/{channel_username}/{message.id}",
        }


    async def publish_post(self, text: str) -> Dict[str, Any]:
        entity = await self.get_publish_entity()
        message = await self.client.send_message(
            entity,
            text,
            parse_mode="html",
            link_preview=False,
        )

        channel_username = CONFIG.get("publish_channel", "xzcvzxa")

        return {
            "message_id": message.id,
            "chat_id": entity.id,
            "link": f"https://t.me/{channel_username}/{message.id}",
        }

    async def schedule_post(self, text: str, schedule_date: datetime) -> Dict[str, Any]:
        entity = await self.get_publish_entity()
        message = await self.client.send_message(
            entity,
            text,
            parse_mode="html",
            link_preview=False,
            schedule=schedule_date,
        )

        channel_username = CONFIG.get("publish_channel", "xzcvzxa")

        return {
            "message_id": message.id,
            "chat_id": entity.id,
            "link": f"https://t.me/{channel_username}/{message.id}",
            "scheduled_at": schedule_date.isoformat(),
        }

    async def schedule_plugin(
        self,
        text: str,
        schedule_date: datetime,
        file_path: Optional[str] = None,
    ) -> Dict[str, Any]:
        entity = await self.get_publish_entity()

        if file_path and Path(file_path).exists():
            message = await self.client.send_file(
                entity,
                file=file_path,
                caption=text,
                parse_mode="html",
                schedule=schedule_date,
            )
        else:
            message = await self.client.send_message(
                entity,
                text,
                parse_mode="html",
                link_preview=False,
                schedule=schedule_date,
            )

        channel_username = CONFIG.get("publish_channel", "xzcvzxa")

        return {
            "message_id": message.id,
            "chat_id": entity.id,
            "link": f"https://t.me/{channel_username}/{message.id}",
            "scheduled_at": schedule_date.isoformat(),
        }

    async def schedule_icon(
        self,
        text: str,
        schedule_date: datetime,
        file_path: Optional[str] = None,
    ) -> Dict[str, Any]:
        entity = await self.get_icons_publish_entity()

        if file_path and Path(file_path).exists():
            message = await self.client.send_file(
                entity,
                file=file_path,
                caption=text,
                parse_mode="html",
                schedule=schedule_date,
            )
        else:
            message = await self.client.send_message(
                entity,
                text,
                parse_mode="html",
                link_preview=False,
                schedule=schedule_date,
            )

        channel_username = CONFIG.get("icons_channel", {}).get("username", ICONS_CHANNEL_USERNAME)

        return {
            "message_id": message.id,
            "chat_id": entity.id,
            "link": f"https://t.me/{channel_username}/{message.id}",
            "scheduled_at": schedule_date.isoformat(),
        }

    async def publish_icon(
        self,
        text: str,
        file_path: Optional[str] = None,
    ) -> Dict[str, Any]:
        entity = await self.get_icons_publish_entity()

        if file_path and Path(file_path).exists():
            message = await self.client.send_file(
                entity,
                file=file_path,
                caption=text,
                parse_mode="html",
            )
        else:
            message = await self.client.send_message(
                entity,
                text,
                parse_mode="html",
                link_preview=False,
            )

        channel_username = CONFIG.get("icons_channel", {}).get("username", ICONS_CHANNEL_USERNAME)

        return {
            "message_id": message.id,
            "chat_id": entity.id,
            "link": f"https://t.me/{channel_username}/{message.id}",
        }
    
    async def update_message(
        self,
        message_id: int,
        text: str,
        file_path: Optional[str] = None,
    ) -> Dict[str, Any]:
        entity = await self.get_publish_entity()

        try:
            if file_path and Path(file_path).exists():
                file_name = Path(file_path).name
                attributes = [DocumentAttributeFilename(file_name)]
                await self.client.edit_message(
                    entity,
                    message_id,
                    text,
                    file=file_path,
                    attributes=attributes,
                    parse_mode="html",
                )
            else:
                await self.client.edit_message(
                    entity,
                    message_id,
                    text,
                    parse_mode="html",
                )

            channel_username = CONFIG.get("publish_channel", "xzcvzxa")

            return {
                "message_id": message_id,
                "chat_id": entity.id,
                "link": f"https://t.me/{channel_username}/{message_id}",
                "updated": True,
            }
        except Exception as e:
            logger.error(f"Failed to update message {message_id}: {e}")
            raise


    async def update_icon_message(
        self,
        message_id: int,
        text: str,
        file_path: Optional[str] = None,
    ) -> Dict[str, Any]:
        entity = await self.get_icons_publish_entity()

        try:
            if file_path and Path(file_path).exists():
                file_name = Path(file_path).name
                attributes = [DocumentAttributeFilename(file_name)]
                await self.client.edit_message(
                    entity,
                    message_id,
                    text,
                    file=file_path,
                    attributes=attributes,
                    parse_mode="html",
                )
            else:
                await self.client.edit_message(
                    entity,
                    message_id,
                    text,
                    parse_mode="html",
                )

            channel_username = CONFIG.get("icons_channel", {}).get("username", ICONS_CHANNEL_USERNAME)

            return {
                "message_id": message_id,
                "chat_id": entity.id,
                "link": f"https://t.me/{channel_username}/{message_id}",
                "updated": True,
            }
        except Exception as e:
            logger.error(f"Failed to update icon message {message_id}: {e}")
            raise

    async def delete_message(self, message_id: int) -> None:
        entity = await self.get_publish_entity()
        await self.client.delete_messages(entity, message_id)
    
    async def full_sync(self, limit: int = 0) -> Dict[str, int]:
        stats = {"plugins": 0, "icons": 0, "skipped": 0, "errors": 0}

        plugins_db = load_plugins()
        icons_db = load_icons()

        existing_plugin_ids = {
            p.get("channel_message", {}).get("message_id")
            for p in plugins_db.get("plugins", [])
        }
        existing_icon_ids = {
            i.get("channel_message", {}).get("message_id")
            for i in icons_db.get("iconpacks", [])
        }

        async def sync_entity(entity, target: str) -> None:
            all_messages: List[Message] = []
            async for msg in self.client.iter_messages(entity, limit=limit or None):
                all_messages.append(msg)

            logger.info(f"Fetched {len(all_messages)} messages")

            media_groups: Dict[int, List[Message]] = {}
            standalone: List[Message] = []

            for msg in all_messages:
                if msg.grouped_id:
                    media_groups.setdefault(msg.grouped_id, []).append(msg)
                else:
                    standalone.append(msg)

            for group_id, messages in media_groups.items():
                try:
                    result = self._process_group(messages, entity)
                    if not result:
                        stats["skipped"] += len(messages)
                        continue

                    entry, content_type, msg_id = result

                    if content_type == "plugin" and target == "plugins" and msg_id not in existing_plugin_ids:
                        plugins_db.setdefault("plugins", []).append(entry)
                        existing_plugin_ids.add(msg_id)
                        stats["plugins"] += 1
                    elif content_type == "icon" and target == "icons" and msg_id not in existing_icon_ids:
                        icons_db.setdefault("iconpacks", []).append(entry)
                        existing_icon_ids.add(msg_id)
                        stats["icons"] += 1
                    else:
                        stats["skipped"] += 1
                except Exception as e:
                    logger.error(f"Error processing group {group_id}: {e}")
                    stats["errors"] += 1

            for msg in standalone:
                try:
                    result = self._process_standalone(msg, entity)
                    if not result:
                        stats["skipped"] += 1
                        continue

                    entry, content_type = result

                    if content_type == "plugin" and target == "plugins" and msg.id not in existing_plugin_ids:
                        plugins_db.setdefault("plugins", []).append(entry)
                        existing_plugin_ids.add(msg.id)
                        stats["plugins"] += 1
                    elif content_type == "icon" and target == "icons" and msg.id not in existing_icon_ids:
                        icons_db.setdefault("iconpacks", []).append(entry)
                        existing_icon_ids.add(msg.id)
                        stats["icons"] += 1
                    else:
                        stats["skipped"] += 1
                except Exception as e:
                    logger.error(f"Error processing message {msg.id}: {e}")
                    stats["errors"] += 1

        plugin_entity = await self.get_sync_entity()
        await sync_entity(plugin_entity, "plugins")

        if ICONS_CHANNEL_ID or ICONS_CHANNEL_USERNAME:
            icons_entity = await self.get_icons_sync_entity()
            await sync_entity(icons_entity, "icons")

        save_plugins(plugins_db)
        save_icons(icons_db)
        _invalidate_all()

        logger.info(f"Sync complete: {stats}")
        return stats
    
    def _get_file_name(self, message: Message) -> Optional[str]:
        if not message.document:
            return None
        for attr in message.document.attributes:
            if isinstance(attr, DocumentAttributeFilename):
                return attr.file_name
        return None
    
    def _get_file_info(self, message: Message) -> Optional[Dict[str, Any]]:
        if not message.document:
            return None
        return {
            "file_id": str(message.document.id),
            "access_hash": str(message.document.access_hash),
            "file_name": self._get_file_name(message),
            "file_size": message.document.size,
            "message_id": message.id,
        }
    
    def _detect_content_type(self, message: Message) -> Optional[str]:
        file_name = self._get_file_name(message)
        if not file_name:
            return None
        fn_lower = file_name.lower()
        if fn_lower.endswith(".plugin"):
            return "plugin"
        elif fn_lower.endswith(".icons"):
            return "icon"
        return None
    
    def _process_group(self, messages: List[Message], entity) -> Optional[tuple]:
        text_content = None
        file_msg = None
        text_msg = None
        content_type = None
        
        for msg in messages:
            if msg.message:
                text_content = msg.message
                text_msg = msg
            if msg.document:
                file_msg = msg
                ct = self._detect_content_type(msg)
                if ct:
                    content_type = ct
        
        if not text_content:
            return None
        
        main_msg = text_msg or messages[0]
        
        parsed = parse_channel_post(
            text_content,
            message_id=main_msg.id,
            message_date=main_msg.date,
        )
        
        if not parsed:
            return None
        
        if content_type:
            parsed.is_plugin = (content_type == "plugin")
        
        entry = parsed.to_catalog_entry(entity.id, SYNC_CHANNEL_USERNAME)
        
        if file_msg:
            file_info = self._get_file_info(file_msg)
            if file_info:
                entry["file"] = file_info
        
        result_type = "plugin" if parsed.is_plugin else "icon"
        return entry, result_type, main_msg.id
    
    def _process_standalone(self, msg: Message, entity) -> Optional[tuple]:
        text = msg.message or ""
        content_type = self._detect_content_type(msg)
        
        if not text:
            return None
        
        parsed = parse_channel_post(text, message_id=msg.id, message_date=msg.date)
        if not parsed:
            return None
        
        if content_type:
            parsed.is_plugin = (content_type == "plugin")
        
        entry = parsed.to_catalog_entry(entity.id, SYNC_CHANNEL_USERNAME)
        
        file_info = self._get_file_info(msg)
        if file_info:
            entry["file"] = file_info
        
        result_type = "plugin" if parsed.is_plugin else "icon"
        return entry, result_type


async def get_userbot() -> Optional[UserbotClient]:
    return await UserbotClient.get_instance()
