class UrlNotFoundError(Exception):
    def __init__(self, short_code: str) -> None:
        self.short_code = short_code
        super().__init__(f"No URL found for short_code={short_code!r}")


class UrlExpiredError(Exception):
    def __init__(self, short_code: str) -> None:
        self.short_code = short_code
        super().__init__(f"URL for short_code={short_code!r} has expired")