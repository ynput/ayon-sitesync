
class ErrorCodes:
    SE_JOB_NO_SOURCE_GROUP = 10001
    SE_JOB_NO_DESTINATION_GROUP = 10002
    SE_JOB_NO_GROUPS = 10003
    SE_JOB_SAME_DESTINATION_PATH = 10004
    SE_JOB_UNRESOLVED_TAGS = 10005
    SE_JOB_NO_CLIENTS = 10006
    SE_JOB_NO_SOURCE_CLIENTS = 10007
    SE_JOB_NO_DESTINATION_CLIENTS = 10008
    SE_AGENT_DISK_FULL = 10200
    SE_SERVER_EXCESSIVE_TIME_DIFF = 10300


class ApiError(Exception):
    def __init__(self, message, status_code=None, cause=None, data=None):
        super(ApiError, self).__init__(message)
        self.cause = cause
        self.message = message
        self.status_code = status_code
        self.data = data

    def _same_destination_path_error(self):
        if not self.data or not isinstance(self.data, list):
            return None
    
        for d in self.data:
            if d['code'] == ErrorCodes.SE_JOB_SAME_DESTINATION_PATH:
                return '{} ({})'.format(d['message'], d['description'])
        return None

    def error_name(self):
        return 'Api error'

    def __str__(self):
        same_destination_error = self._same_destination_path_error()
        if same_destination_error:
            return '{}: {}'.format(self.error_name(), same_destination_error)
        else:
            return '{}: {}'.format(self.error_name(), self.message)


class ApiConnectionError(ApiError):
    def error_name(self):
        return 'Connection error'


class ApiUnauthorizedError(ApiError):
    def error_name(self):
        return 'Unauthorized error'