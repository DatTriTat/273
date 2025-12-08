import types


def matches_filter(data: dict, filters: dict) -> bool:
    """
    Simple numeric/string filter:
    filters = {"temp": ">30", "humidity": "<80", "status": "=ok"}
    data    = {"temp": 32, "humidity": 70, "status": "ok"}
    """
    if not filters:
        return True

    for key, condition in filters.items():
        if key not in data:
            return False

        value = data[key]
        if not isinstance(condition, str):
            # fallback to direct equality
            if value != condition:
                return False
            continue

        cond = condition.strip()
        try:
            # numeric compare
            if cond[0] in (">", "<", "="):
                num = float(cond[1:])
                val_num = float(value)

                if cond.startswith(">") and not (val_num > num):
                    return False
                if cond.startswith("<") and not (val_num < num):
                    return False
                if cond.startswith("=") and not (val_num == num):
                    return False
            else:
                # exact string match
                if str(value) != cond:
                    return False
        except (ValueError, IndexError):
            # fallback to string equality
            if cond.startswith("="):
                if str(value) != cond[1:]:
                    return False
            else:
                if str(value) != cond:
                    return False

    return True


def run_function_filter(fn_code: str, publication: dict) -> bool:
    """
    Function-based filter.
    fn_code should look like: "lambda pub: pub.get('temp',0) > 30"
    WARNING: this is not safe for untrusted users, but OK for controlled experiment.
    """
    if not fn_code:
        return True

    try:
        fn = eval(fn_code, {"__builtins__": {}}, {})
        if not isinstance(fn, types.LambdaType):
            return False
        return bool(fn(publication))
    except Exception:
        # if error, treat as non-match
        return False
