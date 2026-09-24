class BusinessError(Exception):
    def __init__(self, code, message, status=400, details=None):
        self.code, self.message, self.status = code, message, status
        self.details = details or {}
        super().__init__(message)
