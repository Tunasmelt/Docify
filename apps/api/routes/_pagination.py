import base64

# Extracted from documents.py (FEAT-008) when FEAT-026 needed the identical
# keyset-cursor encode/decode a second time for GET /conversations —
# duplicating this verbatim would have been the second copy of logic this
# project already avoids elsewhere (e.g. figure_fetcher.py's chunk-to-
# figure resolution). No behavior change from the original.


def encode_cursor(value: str) -> str:
    return base64.urlsafe_b64encode(value.encode()).decode()


def decode_cursor(cursor: str) -> str:
    return base64.urlsafe_b64decode(cursor.encode()).decode()
