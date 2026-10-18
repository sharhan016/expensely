import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

DATE_FORMATS = (
    "%d/%m/%Y",
    "%d/%m/%y",
    "%d-%m-%Y",
    "%d-%m-%y",
    "%d-%b-%Y",
    "%d-%b-%y",
    "%d %b %Y",
    "%d %B %Y",
    "%Y-%m-%d",
    "%b %d, %Y",
)
_NOISE = re.compile(r"(?i)(?:₹|rs\.?|inr|\s)")
_DEBIT = re.compile(r"(?i)\b(?:dr|debit)\.?$")
_CREDIT = re.compile(r"(?i)\b(?:cr|credit)\.?$")


def parse_amount(value: str | int | float | Decimal) -> Decimal:
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (int, float)):
        return Decimal(str(value))
    text = str(value).strip()
    if not text:
        raise ValueError("empty amount")
    negative = text.startswith("(") and text.endswith(")")
    if negative:
        text = text[1:-1]
    if _DEBIT.search(text):
        negative = True
        text = _DEBIT.sub("", text)
    else:
        text = _CREDIT.sub("", text)
    text = _NOISE.sub("", text).replace(",", "")
    if text.endswith("-"):
        negative, text = True, text[:-1]
    if text.startswith(("-", "+")):
        negative = text[0] == "-"
        text = text[1:]
    try:
        amount = Decimal(text)
    except InvalidOperation as exc:
        raise ValueError(f"could not read {value!r} as an amount") from exc
    return -amount if negative else amount


def parse_signed_amount(debit: str = "", credit: str = "") -> Decimal:
    debit_value = parse_amount(debit) if str(debit).strip() not in {"", "-", "--"} else None
    credit_value = parse_amount(credit) if str(credit).strip() not in {"", "-", "--"} else None
    if debit_value not in {None, Decimal("0")} and credit_value not in {None, Decimal("0")}:
        raise ValueError("row has both debit and credit values")
    if debit_value is not None:
        return -abs(debit_value)
    if credit_value is not None:
        return abs(credit_value)
    raise ValueError("row has no amount")


def parse_date(value: str | date) -> date:
    if isinstance(value, date):
        return value
    text = " ".join(str(value).strip().split())
    for date_format in DATE_FORMATS:
        try:
            parsed = datetime.strptime(text, date_format).date()
            if parsed.year > date.today().year + 1:
                return parsed.replace(year=parsed.year - 100)
            return parsed
        except ValueError:
            continue
    raise ValueError(f"could not read {value!r} as a date")
