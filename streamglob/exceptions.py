class SGException(Exception):
    pass

class SGPlayInvalidArgumentError(SGException):
    pass

class SGStreamSessionException(SGException):
    pass

class SGInvalidFilterValue(SGException):
    pass

class SGIncompleteIdentifier(SGException):
    pass

class SGIncompleteSpecifier(SGException):
    pass
