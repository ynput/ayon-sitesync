from ..base_connection import BaseConnection
from ..errors import ApiError


class BaseModel(BaseConnection):
    """
    Base class for all data models

    Parameters
    ----------
    api : BaseConnection
        Adapter for all API requests
    data : dict
        Object attributes
    """

    def __init__(self, api, data):
        super(BaseModel, self).__init__(api._address, api._token, api._verify)
        self._attrs = data if data else {}

    def save(self):
        """
        Push model to the Management Console

        Parameters
        ----------
        None
        """

        raise NotImplementedError()

    def fetch(self):
        """
        Pull updated model from the Management Console

        Parameters
        ----------
        None
        """

        raise NotImplementedError()

    @property
    def created(self):
        """Does model exist on the Management Console"""

        return 'id' in self._attrs

    @property
    def attrs(self):
        """Model attributes"""

        return self._attrs

    @property
    def id(self):
        """Model ID"""

        if 'id' not in self._attrs:
            raise ApiError('No id field. The model is not saved.')
        return self._attrs['id']

    def __eq__(self, other):
        if isinstance(other, self.__class__) and self.created and other.created:
            return self.id == other.id
        else:
            return False