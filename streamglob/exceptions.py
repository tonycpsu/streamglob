class SGException(Exception):
    pass

class SGPlayInvalidArgumentError(SGException):
    pass

class StreamSessionException(SGException):
    pass

class SGInvalidFilterValue(SGException):
    pass

class SGIncompleteIdentifier(SGException):
    pass

class SGIncompleteSpecifier(SGException):
    pass
