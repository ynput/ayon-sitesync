from .base_model import BaseModel
from ..constants import JobRunStatus

class JobRun(BaseModel):
    """
    Data model for job run representation

    Parameters
    ----------
    api : BaseConnection
        Adapter for all API requests
    data : dict
        Object attributes

    See also
    --------
    ConnectApi.get_job_run
    """

    def __init__(self, api, data):
        super().__init__(api, data)

    def __str__(self):
        return 'JobRun[{}]'.format(self.id)

    def fetch(self):
        """
        Pull updated model from the Management Console

        Parameters
        ----------
        None
        """

        self._attrs = self._get_job_run(self.id)

    def stop(self):
        """
        Stop job run

        Parameters
        ----------
        None
        """
        self._stop_job_run(self.id)

    def pause(self):
        """
        Pause job run

        Parameters
        ----------
        None
        """
        if not self.paused:
            self._pause_job_run(self.id)
            self.fetch()

    def resume(self):
        """
        Resume job run

        Parameters
        ----------
        None
        """
        if self.paused:
            self._resume_job_run(self.id)
            self.fetch()

    @property
    def is_synced(self):
        """Is job run finished"""

        return self._attrs['status'] == JobRunStatus.FINISHED

    @property
    def download_percent(self):
        """Data download progress"""

        if self._attrs['size_total'] == 0:
            return 0

        if self._attrs['agents_total'] < 2:
            return 100

        expected = self._attrs['size_total'] * (self._attrs['agents_total'] - 1)
        completed = self._attrs['size_completed']

        return min(int((completed / expected) * 100), 100)

    @property
    def active(self):
        """Is job run active"""

        return self._attrs['active']

    @property
    def paused(self):
        """Is job run paused"""

        return self._attrs['status'] == JobRunStatus.PAUSED

    # Agent status
    def get_agents_statuses(self):
        """
        Get all agents statuses

        Paramaters
        ----------
        None

        Returns
        -------
        list
            List of dicts with detailed agents status
        """

        return self._get_agents_statuses(self.id)['data']

    def get_agent_status(self, agent_id):
        """
        Get status of particular agent

        Parameters
        ----------
        agent_id : int
            Agent ID

        Returns
        -------
        dict
            Detailed agent's status
        """
        return self._get_agent_status(self.id, agent_id)

    @property
    def status(self):
        """Job run status"""

        return self._attrs['status']

    @property
    def total_size(self):
        """Total size in bytes"""

        return self._attrs['size_total']

    @property
    def total_files(self):
        """Total size in files"""

        return self._attrs['files_total']

    @property
    def down_speed(self):
        """Download speed"""

        return self._attrs['down_speed']

    @property
    def errors(self):
        """Errors"""

        return self._attrs['errors']
