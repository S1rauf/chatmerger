# /app/modules/billing/exceptions.py

class BillingError(Exception):
    """
    Базовый класс для всех ошибок, связанных с биллингом.
    Это позволяет ловить все ошибки биллинга одним блоком:
    except BillingError:
    """
    pass

class InsufficientFundsError(BillingError):
    """
    Возникает, когда у пользователя не хватает средств на балансе для совершения операции.
    """
    pass

class TariffLimitReachedError(BillingError):
    """
    Возникает, когда пользователь пытается выполнить действие,
    превышающее лимиты его текущего тарифного плана.
    """
    pass
