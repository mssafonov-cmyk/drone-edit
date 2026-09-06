"""Шаблон фетчера. Скопировать в sources/<name>.py и реализовать fetch().
Требования: stdlib + requests (если установлен); таймаут ≤ 20 с на запрос; User-Agent
браузера; при 4xx/5xx — raise. Не более ~15 запросов на прогон на источник.
"""
NAME = "template"


def fetch(boats, windows):
    """boats: текущий snapshot['boats']; windows: [(from,to), ...] 9 окон по 7 дней.
    Вернуть {boat_id: {...}} по контракту из run_check.py."""
    raise NotImplementedError
