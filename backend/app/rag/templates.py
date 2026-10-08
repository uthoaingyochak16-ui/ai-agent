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


def normalize_question(q: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", q.lower())).strip()
