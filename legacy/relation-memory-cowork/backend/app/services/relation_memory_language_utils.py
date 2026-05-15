from __future__ import annotations

import re


CYRILLIC_TO_LATIN = str.maketrans(
    {
        "а": "a",
        "б": "b",
        "в": "v",
        "г": "g",
        "д": "d",
        "е": "e",
        "ё": "e",
        "ж": "zh",
        "з": "z",
        "и": "i",
        "й": "i",
        "к": "k",
        "л": "l",
        "м": "m",
        "н": "n",
        "о": "o",
        "п": "p",
        "р": "r",
        "с": "s",
        "т": "t",
        "у": "u",
        "ф": "f",
        "х": "h",
        "ц": "ts",
        "ч": "ch",
        "ш": "sh",
        "щ": "sch",
        "ъ": "",
        "ы": "y",
        "ь": "",
        "э": "e",
        "ю": "yu",
        "я": "ya",
    }
)
LATIN_TO_CYRILLIC_CONFUSABLES = str.maketrans(
    {
        "a": "а",
        "b": "в",
        "c": "с",
        "e": "е",
        "h": "н",
        "k": "к",
        "m": "м",
        "o": "о",
        "p": "р",
        "t": "т",
        "x": "х",
        "y": "у",
    }
)
CYRILLIC_TO_LATIN_CONFUSABLES = str.maketrans(
    {
        "а": "a",
        "в": "b",
        "с": "c",
        "е": "e",
        "к": "k",
        "м": "m",
        "н": "h",
        "о": "o",
        "р": "p",
        "т": "t",
        "у": "y",
        "х": "x",
    }
)

CONTEXTUAL_TOKENS = {
    "это",
    "эта",
    "этот",
    "эту",
    "эти",
    "он",
    "она",
    "оно",
    "них",
    "нее",
    "неё",
    "него",
    "ней",
    "ее",
    "её",
    "его",
    "этом",
    "этома",
}
QUESTION_NOISE_TOKENS = {
    "что",
    "как",
    "куда",
    "почему",
    "кто",
    "какие",
    "какая",
    "какой",
    "какую",
    "ли",
    "же",
    "вообще",
    "метрика",
    "метрики",
    "показатель",
    "показатели",
    "влияет",
    "связан",
    "связана",
    "связано",
    "связаны",
    "связь",
    "между",
    "от",
    "на",
    "с",
    "и",
    "а",
    "ну",
    "то",
}
CHANGE_UP_PATTERN = (
    r"вырос(?:ла|ло|ли)?|возрос(?:ла|ло|ли)?|подрос(?:ла|ло|ли)?|"
    r"пополз(?:ла|ло|ли)?(?:\s+вверх)?|увеличил(?:ся|ась|ось|ись)?|"
    r"раст[её]т|растут|увеличива(?:ет(?:ся)?|ются)|поднима(?:ет(?:ся)?|ются)|"
    r"ид[её]т\s+вверх|grow(?:s|ing)?|grew|rose|ris(?:e|es|ing)|increas(?:e|es|ed|ing)"
)
CHANGE_DOWN_PATTERN = (
    r"упал(?:а|о|и)?|снизил(?:ся|ась|ось|ись)?|просел(?:а|о|и)?|"
    r"пада(?:ет|ют|л[аои]?|ли)|снижа(?:ет(?:ся)?|ются)|уменьша(?:ет(?:ся)?|ются)|"
    r"проседа(?:ет|ют)|сокраща(?:ет(?:ся)?|ются)|ид[её]т\s+вниз|"
    r"fall(?:s|ing)?|fell|declin(?:e|es|ed|ing)|decreas(?:e|es|ed|ing)|drop(?:s|ped|ping)?"
)
CHANGE_NEUTRAL_PATTERN = (
    r"измени(?:лся|лась|лось|лись)|изменя(?:ет(?:ся)?|ются)|меня(?:ет(?:ся)?|ются)|"
    r"колебл(?:ется|ются)|скач(?:ет|ут)|chang(?:e|es|ed|ing)"
)
CHANGE_VERB_PATTERN = f"{CHANGE_UP_PATTERN}|{CHANGE_DOWN_PATTERN}|{CHANGE_NEUTRAL_PATTERN}"
RUSSIAN_STEM_SUFFIXES = (
    "иями",
    "ями",
    "ами",
    "его",
    "ого",
    "ему",
    "ому",
    "ыми",
    "ими",
    "иях",
    "ах",
    "ях",
    "ов",
    "ев",
    "ей",
    "ий",
    "ый",
    "ой",
    "ая",
    "яя",
    "ое",
    "ее",
    "ые",
    "ие",
    "ам",
    "ям",
    "ом",
    "ем",
    "ую",
    "юю",
    "ия",
    "ья",
    "ию",
    "ью",
    "иям",
    "ия",
    "ы",
    "и",
    "а",
    "я",
    "е",
    "о",
    "у",
    "ю",
)
ENGLISH_STEM_SUFFIXES = ("ingly", "edly", "ments", "ment", "ings", "ers", "ies", "ing", "ied", "ers", "ed", "es", "s")
SELECTION_WORDS = {
    "1": 0,
    "первая": 0,
    "первую": 0,
    "первый": 0,
    "2": 1,
    "вторая": 1,
    "вторую": 1,
    "второй": 1,
    "3": 2,
    "третья": 2,
    "третью": 2,
    "третий": 2,
}
SEMANTIC_ALIAS_HINTS = {
    "revenue": ["выручка", "доход", "продажи"],
    "выручк": ["выручка", "доход", "продажи"],
    "gross_margin": ["валовая маржа", "маржа"],
    "gross": ["валовая маржа", "маржа"],
    "orders": ["заказы", "объем заказов"],
    "order": ["заказы", "объем заказов"],
    "gsm": ["топливо", "затраты на топливо", "топливные расходы", "fuel"],
    "гсм": ["топливо", "затраты на топливо", "топливные расходы", "fuel"],
    "fot": ["зарплата", "зарплата водителей", "фонд оплаты труда"],
    "фот": ["зарплата", "зарплата водителей", "фонд оплаты труда"],
    "sebestoimost": ["себестоимость", "себестоимость рейса", "стоимость рейса", "cost"],
    "себестоимост": ["себестоимость", "себестоимость рейса", "стоимость рейса", "cost"],
    "amortiz": ["амортизация"],
    "амортиз": ["амортизация"],
    "platon": ["платон", "платная дорога", "платные дороги", "платон и платные дороги"],
    "state_toll": ["государственные дорожные сборы", "дорожные сборы", "гос сборы", "стоимость платных дорог", "тариф платных дорог"],
    "toll": ["государственные дорожные сборы", "дорожные сборы", "гос сборы", "стоимость платных дорог", "тариф платных дорог"],
    "toll_tariff": ["тарифная политика операторов платных дорог", "тарифы операторов платных дорог"],
    "лизинг": ["лизинг"],
    "lizing": ["лизинг"],
    "remont": ["ремонт", "ремонты"],
    "ремонт": ["ремонт", "ремонты"],
    "voditel": ["водители", "водитель"],
    "водител": ["водители", "водитель"],
}
BROAD_ALIAS_PREFERRED_CODE_HINTS = {
    "государство": ("state_toll",),
    "платные дороги": ("state_toll",),
    "стоимость платных дорог": ("state_toll",),
    "тариф платных дорог": ("state_toll",),
    "тарифная политика операторов платных дорог": ("toll_tariff_policy",),
    "погода": ("weather_risk",),
    "weather": ("weather_risk",),
    "гсм": ("zatraty_na_gsm",),
    "gsm": ("zatraty_na_gsm",),
    "рынок ftl": ("market_ftl_rate",),
}


def normalize_phrase_text(value: str) -> str:
    text = str(value or "").replace("\xa0", " ").replace("«", '"').replace("»", '"')
    text = re.sub(r"[\r\n\t]+", " ", text)
    return re.sub(r"\s+", " ", text).strip(" ?!.,:;\"'")


def normalize_user_text(value: str) -> str:
    text = normalize_phrase_text(value).lower().replace("ё", "е")
    text = text.replace("/", " ").replace("_", " ")
    text = re.sub(r"[^a-zа-я0-9+ -]+", " ", text)
    tokens = [expand_abbreviation(_fold_mixed_script_token(token)) for token in re.split(r"\s+", text) if token]
    return " ".join(tokens).strip()


def latinize_text(value: str) -> str:
    normalized = normalize_user_text(value)
    if not normalized:
        return ""
    return re.sub(r"\s+", " ", normalized.translate(CYRILLIC_TO_LATIN)).strip()


def stem_phrase(value: str) -> str:
    tokens = [stem_token(token) for token in normalize_user_text(value).split() if token]
    return " ".join(tokens).strip()


def _edit_distance(left: str, right: str) -> int:
    if left == right:
        return 0
    if not left:
        return len(right)
    if not right:
        return len(left)
    previous = list(range(len(right) + 1))
    for index, left_char in enumerate(left, start=1):
        current = [index]
        for right_index, right_char in enumerate(right, start=1):
            current.append(
                min(
                    previous[right_index] + 1,
                    current[right_index - 1] + 1,
                    previous[right_index - 1] + (0 if left_char == right_char else 1),
                )
            )
        previous = current
    return previous[-1]


def informative_tokens(value: str) -> list[str]:
    normalized = normalize_user_text(value)
    tokens = []
    for token in normalized.split():
        if len(token) < 2:
            continue
        if token in QUESTION_NOISE_TOKENS:
            continue
        if token in CONTEXTUAL_TOKENS:
            continue
        tokens.append(stem_token(token))
    return list(dict.fromkeys(token for token in tokens if token))


def expand_abbreviation(token: str) -> str:
    lowered = token.lower()
    if lowered in {"гсм", "gsm"}:
        return "топливо"
    if lowered in {"фот", "fot"}:
        return "зарплата"
    if lowered in {"фтл", "ftl"}:
        return "ftl"
    return lowered


def stem_token(token: str) -> str:
    lowered = token.lower()
    if not lowered:
        return ""
    for suffix in RUSSIAN_STEM_SUFFIXES:
        if len(lowered) > len(suffix) + 2 and lowered.endswith(suffix):
            return lowered[: -len(suffix)]
    for suffix in ENGLISH_STEM_SUFFIXES:
        if len(lowered) > len(suffix) + 2 and lowered.endswith(suffix):
            return lowered[: -len(suffix)]
    return lowered


def _fold_mixed_script_token(token: str) -> str:
    has_cyrillic = bool(re.search(r"[а-я]", token))
    has_latin = bool(re.search(r"[a-z]", token))
    if not has_cyrillic or not has_latin:
        return token
    cyrillic_count = len(re.findall(r"[а-я]", token))
    latin_count = len(re.findall(r"[a-z]", token))
    if cyrillic_count >= latin_count:
        return token.translate(LATIN_TO_CYRILLIC_CONFUSABLES)
    return token.translate(CYRILLIC_TO_LATIN_CONFUSABLES)
