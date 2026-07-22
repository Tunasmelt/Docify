def error_envelope(code: str, message: str, detail: dict | None = None) -> dict:
    error = {"code": code, "message": message}
    if detail is not None:
        error["detail"] = detail
    return {"error": error}
