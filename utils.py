from datetime import date, timedelta


def get_saturdays_of_month(year: int, month: int) -> list:
    """Все субботы указанного месяца."""
    saturdays = []
    d = date(year, month, 1)
    while d.month == month:
        if d.weekday() == 5:  # суббота
            saturdays.append(d)
        d += timedelta(days=1)
    return saturdays


def is_first_weekend(saturday: date) -> bool:
    """Первые ли это выходные месяца (суббота в первых 7 днях)?"""
    return saturday.day <= 7


def auto_task_type(saturday: date) -> str:
    """Автоматически определить тип уборки по дате субботы."""
    return "wet" if is_first_weekend(saturday) else "dry"


def get_next_saturday() -> date:
    """Ближайшая суббота от сегодня."""
    today = date.today()
    days_ahead = 5 - today.weekday()
    if days_ahead <= 0:
        days_ahead += 7
    return today + timedelta(days=days_ahead)
