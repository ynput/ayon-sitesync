from datetime import datetime
from .base_model import BaseModel


class Agent(BaseModel):
    """
    Data model for agent representation

    Parameters
    ----------
    api : BaseConnection
        Adapter for all API requests
    data : dict
        Object attributes
    """

    def __init__(self, api, data):
        super(Agent, self).__init__(api, data)

    def __str__(self):
        return '{}[{}]'.format(self._attrs['name'], self._attrs['ip'])

    def fetch(self):
        """
        Pull updated model from the Management Console

        Parameters
        ----------
        None
        """

        self._attrs = self._get_agent(self.id)

    @property
    def name(self):
        """Name"""

        return self._attrs['name']

    @property
    def deviceid(self):
        """Peer ID"""

        return self._attrs['deviceid']

    @property
    def online(self):
        """Online status"""

        return self._attrs['online']

    @property
    def ip(self):
        """IP address"""

        if 'ip' not in self._attrs:
            return None
        return self._attrs['ip']

    @property
    def group_ids(self):
        """IDs of groups to which agent is added"""

        return [g['id'] for g in self._attrs['groups']]

    @property
    def status(self):
        """Status"""

        return self._attrs['status']

    @property
    def wan_enabled(self):
        """WAN optimization"""

        return self._attrs['wan_enabled']

    @property
    def last_seen(self):
        """
        Last connect or disconnect time

        See also
        --------
        Agent.online
        """

        return datetime.fromtimestamp(self._attrs['last_seen'])

    @property
    def os(self):
        """
        Operating system

        See also
        --------
        AgentOS
        """

        return self._attrs['os']

    @property
    def errors(self):
        """Errors"""

        return self._attrs['errors']

    @property
    def tags(self):
        """Tags"""

        return self._attrs['tags']
