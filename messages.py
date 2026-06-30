TASK_NAMES = {
    "wet": "влажная уборка",
    "dry": "сухая уборка",
    "horse": "мытье коня",
}

PLACE_NAMES = {
    "threesome": "трёхместка",
    "common": "общая территория",
}


def get_task_name(task_type: str) -> str:
    """Получить название задачи — встроенной или кастомной."""
    if task_type in TASK_NAMES:
        return TASK_NAMES[task_type]
    # Кастомный тип: key вида custom_название
    if task_type.startswith("custom_"):
        from database import get_event_type_by_key
        event = get_event_type_by_key(task_type)
        if event:
            return event["name"]
    return task_type


def reminder_text(username: str, task_type: str, place: str) -> str:
    task_name = get_task_name(task_type)
    place_name = PLACE_NAMES.get(place, place)
    return (
        f"@{username} вас выбрал совет круглого стола. "
        f"На этих выходных ваша обязанность выполнить {task_name} ({place_name}). "
        f"Будьте готовы."
    )


def control_text_1(username: str, task_type: str, place: str) -> str:
    task_name = get_task_name(task_type)
    place_name = PLACE_NAMES.get(place, place)
    return (
        f"@{username} вы убрались? Партия не любит тунеядцев. Она их преследует. "
        f"Напомню, на ваших плечах {task_name} ({place_name})."
    )


def control_text_repeat(username: str) -> str:
    word = "уберись"
    return f"@{username} " + " ".join([word] * 63)


def horse_reminder_text(username: str) -> str:
    return (
        f"@{username} во избежание появления гномов-калоедов нужно провести "
        f"санитарную проверку туалетного узла и принять соответствующие меры."
    )
