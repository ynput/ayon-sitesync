from .base_model import BaseModel


class Group(BaseModel):
    """
    Data model for group representation

    Parameters
    ----------
    api : BaseConnection
        Adapter for all API requests
    data : dict
        Object attributes
    """

    def __init__(self, api, data):
        super(Group, self).__init__(api, data)

    def __str__(self):
        return '{}'.format(self._attrs['name'])

    def save(self):
        """
        Push model to the Management Console

        Parameters
        ----------
        None
        """

        if not self.created:
            model_id = self._create_group(self._attrs)
            self._attrs['id'] = model_id
        else:
            self._update_group(self._attrs)
        self.fetch()

    def fetch(self):
        """
        Pull updated model from the Management Console

        Parameters
        ----------
        None
        """

        self._attrs = self._get_group(self.id)

    @property
    def name(self):
        """Name"""

        return self._attrs['name']

    @property
    def description(self):
        """Description"""

        return self._attrs['description']

    @property
    def agents_ids(self):
        """IDs of agents in the group"""

        return [a['id'] for a in self._attrs['agents']]

    @property
    def jobs_ids(self):
        """IDs of jobs to which group is added"""

        return [j['id'] for j in self._attrs['jobs']]
