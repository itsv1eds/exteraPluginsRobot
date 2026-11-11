import logging

from aiogram import F, Router
from aiogram.types import CallbackQuery, ChosenInlineResult

from bot.context import get_lang
from bot.services.inline import PENDING_CALLBACK, RESULT_PREFIX, apply_inline_formatting, snapshot_for
from bot.texts import t


router = Router(name="inline-formatting")
logger = logging.getLogger(__name__)


@router.chosen_inline_result(F.result_id.startswith(RESULT_PREFIX))
async def on_chosen_inline_result(result: ChosenInlineResult) -> None:
    logger.info("event=inline.formatting.chosen bot_id=%s result_id=%s user_id=%s has_inline_id=%s",
                result.bot.id, result.result_id, result.from_user.id, bool(result.inline_message_id))
    if not result.inline_message_id:
        logger.warning("event=inline.formatting.chosen_without_inline_id result_id=%s", result.result_id)
        return
    snapshot = snapshot_for(int(result.bot.id), result.result_id, result.from_user.id)
    await apply_inline_formatting(result.bot, result.result_id, result.from_user.id, result.inline_message_id)
    request_id = _vote_card_request_id(snapshot.reply_markup if snapshot else None)
    if request_id:
        _remember_vote_card(request_id, result)


def _vote_card_request_id(markup) -> str | None:
    from core.request_store import get_request_by_deeplink_token

    for row in getattr(markup, "inline_keyboard", None) or []:
        for button in row:
            url = str(getattr(button, "url", None) or "")
            if "?start=modvote_" not in url:
                continue
            token = url.split("?start=modvote_", 1)[1].split("_", 1)[-1]
            entry = get_request_by_deeplink_token(token)
            if entry:
                return str(entry.get("id") or "") or None
    return None


def _remember_vote_card(request_id: str, result: ChosenInlineResult) -> None:
    from bot.services.moderation import remember_inline_card

    remember_inline_card(request_id, result.inline_message_id)
    logger.info("event=vote.inline_card_sent request_id=%s user_id=%s", request_id, result.from_user.id)


@router.chosen_inline_result(F.result_id.startswith("request:"))
async def on_chosen_request_card(result: ChosenInlineResult) -> None:
    from bot.callback_tokens import decode_slug

    if not result.inline_message_id:
        logger.warning("event=vote.inline_card_without_id result_id=%s", result.result_id)
        return
    _remember_vote_card(decode_slug(result.result_id.split(":", 1)[1]), result)


@router.callback_query(F.data == PENDING_CALLBACK)
async def on_inline_pending(cb: CallbackQuery) -> None:
    await cb.answer(t("inline_formatting_expired", get_lang(cb.from_user.id)), show_alert=True)


@router.callback_query(F.data.regexp(r"^ifmt:[0-9a-f]{32}$"))
async def on_apply_inline_formatting(cb: CallbackQuery) -> None:
    lang = get_lang(cb.from_user.id)
    if not snapshot_for(int(cb.bot.id), cb.data, cb.from_user.id):
        await cb.answer(t("inline_formatting_expired", lang), show_alert=True)
        return
    logger.info("event=inline.formatting.callback bot_id=%s result_id=%s user_id=%s",
                cb.bot.id, cb.data, cb.from_user.id)
    applied = await apply_inline_formatting(cb.bot, cb.data, cb.from_user.id, cb.inline_message_id)
    if applied:
        await cb.answer()
    else:
        await cb.answer(t("inline_formatting_unavailable", lang), show_alert=True)
