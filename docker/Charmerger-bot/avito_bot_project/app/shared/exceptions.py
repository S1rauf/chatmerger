# /app/shared/exceptions.py

class ApplicationError(Exception):
    """
    Базовый класс для всех кастомных исключений в нашем приложении.
    Это позволяет ловить все наши ошибки одним `except ApplicationError:`.
    """
    pass

class AvitoAPIError(ApplicationError):
    """
    Исключение, возникающее при ошибках во время взаимодействия с API Avito.
    Например, HTTP-ошибки 4xx/5xx или ошибки в теле ответа.
    """
    def __init__(self, message="An error occurred with the Avito API"):
        self.message = message
        super().__init__(self.message)


class DatabaseError(ApplicationError):
    """
    Исключение для ошибок, связанных с базой данных,
    если стандартных исключений SQLAlchemy недостаточно.
    """
    pass
