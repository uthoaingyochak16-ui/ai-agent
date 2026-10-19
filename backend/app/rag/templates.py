import re

_VAR = re.compile(r"\{([A-Za-z0-9_]+)\}")


def fill_template(template: str, variables: dict[str, str]) -> tuple[str, list[str]]:
    """Returns (text, missing_variable_names). Unknown variables are left in place and reported."""
    missing: list[str] = []

    def sub(m: re.Match) -> str:
        k = m.group(1)
        if k in variables:
            return variables[k]
        missing.append(k)
        return m.group(0)
    return _VAR.sub(sub, template), missing


_SENTENCE = re.compile(r"(?<=[।.!?\n])\s*")


def fill_known(template: str, variables: dict[str, str]) -> str:
    """The answer without its unknown values: every sentence that needs a variable with no value is dropped, the rest
    filled in. Not sent to a guest as is (the dropped sentence is often the actual answer); it goes to the AI as a hint."""
    keep = [s.strip() for s in _SENTENCE.split(template)
            if s.strip() and all(k in variables for k in _VAR.findall(s))]
    return _VAR.sub(lambda m: variables[m.group(1)], " ".join(keep)).strip()


def normalize_question(q: str) -> str:
    # keep Bangla vowel signs (া ি ু ে ্ ...): Python's \w does not count them as letters, and dropping them turns
    # "আমি মানুষের" into "আম ম ন ষ র", so different words would look alike
    return re.sub(r"\s+", " ", re.sub(r"[^\w\sঀ-৿]", " ", q.lower())).strip()
