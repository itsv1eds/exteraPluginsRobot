from aiogram.fsm.state import State, StatesGroup


class UserSubmission(StatesGroup):
    choose_language = State()
    choose_action = State()
    choose_submission_type = State()
    waiting_file = State()
    enter_usage = State()
    enter_channel = State()
    choose_category = State()
    confirm = State()
    search_waiting_query = State()
    draft_editor = State()
    draft_waiting_value = State()
    draft_waiting_file = State()
    draft_choose_category = State()
    edit_select_field = State()
    edit_waiting_file = State()
    edit_enter_description = State()
    edit_enter_usage = State()
    edit_enter_channel = State()
    edit_choose_category = State()


class AdminReview(StatesGroup):
    menu = State()
    queue_new = State()
    queue_update = State()
    viewing_queue = State()
    review_item = State()
    choose_language = State()
    enter_en_description = State()
    enter_en_usage = State()
    enter_checked_version = State()
    enter_revision_comment = State()
    draft_waiting_value = State()
    draft_waiting_file = State()
    draft_choose_category = State()
