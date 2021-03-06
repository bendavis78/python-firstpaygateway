class GatewayError(Exception):
    def __init__(self, result):
        self.result = result
        if result.validation_has_failed:
            self.error_messages = [
                e.message for e in result.validation_failures
            ]
        else:
            self.error_messages = result.error_messages
        super(Exception, self).__init__(self.error_messages[0])


class GatewayValidationError(GatewayError):
    pass
