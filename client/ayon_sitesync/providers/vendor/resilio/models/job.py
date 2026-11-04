from datetime import datetime
from .base_model import BaseModel
from ..constants import Permission, SchedulerType, JobType
from ..utils import Path


class Job(BaseModel):
    """
    Data model for job representation

    Parameters
    ----------
    api : BaseConnection
        Adapter for all API requests
    data : dict
        Object attributes

    See also
    --------
    ConnectApi.new_job
    """

    def __init__(self, api, data):
        super(Job, self).__init__(api, data)

        if 'groups' not in self._attrs:
            self._attrs['groups'] = []

        if 'settings' not in self._attrs:
            self._attrs['settings'] = {}

        if (self._attrs['type'] != JobType.SYNC) and ('scheduler' not in self._attrs):
            self._attrs['scheduler'] = { 'type': SchedulerType.MANUALLY }

    def __str__(self):
        if not self.created:
            return 'Unsaved job'
        else:
            return 'Job[{}]'.format(self.id)

    def save(self):
        """
        Push model to the Management Console

        Parameters
        ----------
        None
        """

        if not self.created:
            job_id = self._create_job(self._attrs)
            self._attrs['id'] = job_id
        else:
            new_attrs = {}
            update_attrs = ['name', 'description', 'groups', 'triggers', 'script',
                'scheduler', 'settings', 'post_command_local_time', 'profile_id']

            for a in update_attrs:
                if a in self._attrs:
                    new_attrs[a] = self._attrs[a]

            self._update_job(self.id, new_attrs)
        self.fetch()

    def fetch(self):
        """
        Pull updated model from the Management Console

        Parameters
        ----------
        None
        """

        self._attrs = self._get_job(self.id)

    def start(self):
        """
        Start job

        Parameters
        ----------
        None

        Returns
        -------
        int
            Job run ID
        """

        return self._start_job(self.id)

    def stop(self):
        """
        Stop last job run

        Parameters
        ----------
        None
        """

        job_run_id = self.last_run_id
        if job_run_id:
            self._stop_job_run(job_run_id)

    @property
    def last_run_id(self):
        """Last job run id. 0 if there is no job run"""

        jr = self._get_last_job_run(self.id)
        return jr['id'] if jr else 0

    # Groups
    def add_group(self, group, path, permission):
        """
        Add group to the job

        Parameters
        ----------
        group : Group
            Group model to be added to the job
        path : Path
            Path object containing paths for all platforms
        permission : str
            Permission of the group. See `Permission` in constants for possible values
        """

        assert isinstance(path, Path)

        group_job = {
            'id': group.id,
            'permission': permission,
            'path': path.get_object()
        }

        self._attrs['groups'].append(group_job)

    def add_source_group(self, group, path):
        """
        Add group to the job with RW permission

        Parameters
        ----------
        group : Group
            Group model to be added to the job
        path : Path
            Path object containing paths for all platforms
        """

        self.add_group(group, path, Permission.SOURCE)

    def add_destination_group(self, group, path):
        """
        Add group to the job with RO permission

        Parameters
        ----------
        group : Group
            Group model to be added to the job
        path : Path
            Path object containing paths for all platforms
        """

        self.add_group(group, path, Permission.DESTINATION)

    def get_groups_ids(self):
        """
        Get list of group IDs participating in the job

        Parameters
        ----------
        None
        """

        return [g['id'] for g in self._attrs['groups']]


    # Scheduler
    def _set_scheduler_params(self, start, finish):
        assert self._attrs['type'] != JobType.SYNC, 'Sync job does not support scheduler'

        if start is not None:
            self._attrs['scheduler']['start'] = start
        elif 'start' in self._attrs['scheduler']:
            del self._attrs['scheduler']['start']

        if finish is not None:
            self._attrs['scheduler']['finish'] = finish
        elif 'finish' in self._attrs['scheduler']:
            del self._attrs['scheduler']['finish']

    def schedule_once(self, time):
        """
        Set job scheduler to run the job only once

        Parameters
        ----------
        time : int
            Timestamp in seconds when to start
        """

        self._attrs['scheduler'] = {
            'type': SchedulerType.ONCE,
            'time': time
        }
        self._set_scheduler_params(None, None)

    def schedule_manually(self):
        """
        Disable job scheduler

        Parameters
        ----------
        None
        """

        self._attrs['scheduler'] = {
            'type': SchedulerType.MANUALLY
        }
        self._set_scheduler_params(None, None)

    def schedule_minutes(self, every, start=None, finish=None):
        """
        Set job scheduler to run the job every N minutes

        Parameters
        ----------
        every : int
            Run every N minutes (minimum value is 5 minutes)
        start : int, optional
            Scheduler start time. Timestamp in seconds
        finish : int, optional
            Scheduler finish time. Timestamp in seconds
        """

        self._attrs['scheduler'] = {
            'type': SchedulerType.MINUTES,
            'every': every
        }
        self._set_scheduler_params(start, finish)

    def schedule_hourly(self, every, start=None, finish=None):
        """
        Set job scheduler to run the job every N hours

        Parameters
        ----------
        every : int
            Run every N hours
        start : int, optional
            Scheduler start time. Timestamp in seconds
        finish : int, optional
            Scheduler finish time. Timestamp in seconds
        """

        self._attrs['scheduler'] = {
            'type': SchedulerType.HOURLY,
            'every': every
        }
        self._set_scheduler_params(start, finish)

    def schedule_daily(self, every, time, start=None, finish=None):
        """
        Set job scheduler to run the job every N days

        Parameters
        ----------
        every : int
            Run every N days
        time : int
            Number of seconds since midnight
        start : int, optional
            Scheduler start time. Timestamp in seconds
        finish : int, optional
            Scheduler finish time. Timestamp in seconds
        """

        self._attrs['scheduler'] = {
            'type': SchedulerType.DAILY,
            'every': every,
            'time': time
        }
        self._set_scheduler_params(start, finish)

    def schedule_weekly(self, days, time, start=None, finish=None):
        """
        Set job scheduler to run the job weekly

        Parameters
        ----------
        days : int[]
            Array of days of week to run. Encoded in numbers from 0 to 6, where 0 is Sunday
        time : int[]
            Array of seconds since midnight to run at
            Time is not related to day of week. Job can be started several times a day
        start : int, optional
            Scheduler start time. Timestamp in seconds
        finish : int, optional
            Scheduler finish time. Timestamp in seconds
        """

        self._attrs['scheduler'] = {
            'type': SchedulerType.WEEKLY,
            'days': days,
            'time': time
        }
        self._set_scheduler_params(start, finish)

    # Triggers
    def add_trigger(self, trigger, script):
        """
        Set script for consolidation or distribution job

        Parameters
        ----------
        trigger : str
            Trigger name. Possible values: pre_indexing, post_download, complete
        script : Script
            Script object which contains scripts for all platforms
        """

        assert self.type in [JobType.CONSOLIDATION, JobType.DISTRIBUTION], 'Job doesn\'t supports triggers'
        assert trigger in ['pre_indexing', 'post_download', 'complete'], 'Invalid trigger name'

        if 'triggers' not in self._attrs:
            self._attrs['triggers'] = {}
        self._attrs['triggers'][trigger] = script.get_object()

    def set_script(self, script):
        """
        Set script for script job

        Parameters
        ----------
        script : Script
            Script object which contains scripts for all platforms
        """

        assert self.type == JobType.SCRIPT, 'Only script job supports script'

        self._attrs['script'] = script.get_object()

    @property
    def type(self):
        """Job type"""

        return self._attrs['type']

    @property
    def name(self):
        """Name"""

        return self._attrs['name']

    @property
    def description(self):
        """Description"""

        return self._attrs['description']

    @property
    def errors(self):
        """Errors list"""

        return self._attrs['errors']

    @property
    def groups(self):
        """
        Groups in the job.
        Note that these are NOT Group objects, but dicts with group attributes.
        """

        return self._attrs['groups']

    @property
    def last_start_time(self):
        """Last start time"""

        return datetime.fromtimestamp(self._attrs['last_start_time'])

    @property
    def settings(self):
        """Job settings"""

        return self._attrs['settings']

    @property
    def groups_ids(self):
        """IDs of groups in the job"""

        return self.get_groups_ids()

