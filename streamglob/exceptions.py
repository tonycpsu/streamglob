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

class SGStreamNotFound(SGException):
    pass

class SGFileExists(SGException):
    pass

class SGInvalidFilenameTemplate(SGException):
    pass
