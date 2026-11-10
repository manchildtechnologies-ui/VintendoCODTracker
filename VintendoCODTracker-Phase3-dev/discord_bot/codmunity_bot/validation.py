import re


def valid_username(value: str) -> bool:
    return bool(value and len(value.strip()) <= 64 and re.fullmatch(r'[\w .#-]+', value.strip(), flags=re.UNICODE))


def valid_uid(value: str) -> bool:
    return bool(value and re.fullmatch(r'[A-Za-z0-9_-]{8,128}', value.strip()))
