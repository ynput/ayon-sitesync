import requests
from .base_connection import BaseConnection
from .models import Agent, Group, Job, JobRun


class ConnectApi(BaseConnection):
    """
    Create adapter for the Management Console API

    This class is used to call top level API such as list/create/delete entities.

    Parameters
    ----------
    address : str
        Base URL for all API requests which includes https protocol, hosname and port
    token : str
        API token
    verify : bool, optional
        Verify SSL certificate (default is False)
    """

    def __init__(self, address, token, verify=False):
        super(ConnectApi, self).__init__(address, token, verify)

        if not verify:
            from requests.packages.urllib3.exceptions import InsecureRequestWarning
            requests.packages.urllib3.disable_warnings(InsecureRequestWarning)

    # Agents
    def get_agents(self):
        """
        Get all agents

        Parameters
        ----------
        None

        Returns
        -------
        list
            List of agents as Agent models
        """

        agents_attrs = self._get_agents()
        return [Agent(self, attrs) for attrs in agents_attrs]

    def get_agent(self, agent_id):
        """
        Get agent by id

        Parameters
        ----------
        agent_id : int
            ID of agent to get

        Returns
        -------
        Agent
            Agent model corresponding to specified ID
        """

        attrs = self._get_agent(agent_id)
        return Agent(self, attrs)

    def disconnect_agent(self, agent):
        """
        Disconnect agent from the Management Console by id

        Parameters
        ----------
        agent : Agent or int
            Agent model or agent ID representing agent to disconnect
        """

        agent_id = agent.id if isinstance(agent, Agent) else agent
        self._delete_agent(agent_id)

    # Groups
    def create_group(self, name, agents, description=''):
        """
        Create group

        Parameters
        ----------
        name : str
            Name of the group
        agents : list
            List of Agent models
        description : str, optional
            Description of the group (default is empty string)

        Returns
        -------
        Group
            Created Group model

        Example
        -------
        ```py
        api = ConnectApi('https://localhost:8443', '<API-token>')
        servers = []
        for agent in api.get_agents():
            if agent.name.startswith('srv_'):
                servers.append(agent)
        api.create_group('Servers', servers, 'Servers group')
        ```
        """

        attrs = {
            'name': name,
            'description': description,
            'agents': [{'id': a.id} for a in agents]
        }
        group_id = self._create_group(attrs)

        group = Group(self, dict(id=group_id))
        group.fetch()
        return group

    def get_groups(self):
        """
        Get all groups

        Parameters
        ----------
        None

        Returns
        -------
        list
            List of groups as Group models
        """

        groups_attrs = self._get_groups()
        return [Group(self, attrs) for attrs in groups_attrs]

    def get_group(self, group_id):
        """
        Get group by id

        Parameters
        ----------
        group_id : int
            ID of group to get

        Returns
        -------
        Group
            Group model corresponding to specified ID
        """

        attrs = self._get_group(group_id)
        return Group(self, attrs)

    def delete_group(self, group):
        """
        Delete group from the Management Console

        Parameters
        ----------
        group : Group or int
            Group model or group ID representing group to delete
        """

        group_id = group.id if isinstance(group, Group) else group
        self._delete_group(group_id)

    # Jobs
    def new_job(self, job_type, name, description=''):
        """
        Create new job

        Parameters
        ----------
        job_type : JobType
            Job type. See JobType in constants for possible values
        name : str
            Job name
        description : str, optional
            Job description (default is empty string)

        Example
        -------
        ```py
        api = ConnectApi('https://localhost:8443', '<API-token>')
        agents = api.get_agents()

        src_group = api.create_group('src.group', [agents[0]])
        dst_group = api.create_group('dst.group', agents[1:])

        job = api.new_job(JobType.DISTRIBUTION, 'Deploy dataset')

        job.add_source_group(src_group, Path('source'))
        job.add_destination_group(dst_group, Path('dest'))

        job.add_trigger('post_download', {
            'linux': 'Linux command',
            'win': 'Windows command',
            'osx': 'OS X command'
        })

        job.save()
        job.start()
        ```
        """

        attrs = {
            'type': job_type,
            'name': name,
            'description': description
        }

        return Job(self, attrs)

    def get_jobs(self, name=None, wildcard_name_pattern=None, is_active=None, ever_run=None):
        """
        Get all jobs

        Parameters
        ----------
        name: str, optional
        wildcard_name_pattern: str, optional
        is_active: bool, optional
        ever_run: bool, optional

        Returns
        -------
        list
            List of jobs as Job models
        """
        params = {}
        if name is not None:
            params['name'] = name
        if wildcard_name_pattern is not None:
            params['wildcard_name_pattern'] = wildcard_name_pattern
        if is_active is not None:
            params['is_active'] = is_active
        if ever_run is not None:
            params['ever_run'] = ever_run

        jobs_attrs = self._get_json('/jobs', params=params)
        return [Job(self, attrs) for attrs in jobs_attrs]

    def get_job(self, job_id):
        """
        Get job by id

        Parameters
        ----------
        job_id : int
            ID of job to get

        Returns
        -------
        Job
            Job model corresponding to specified ID
        """

        attrs = self._get_job(job_id)
        return Job(self, attrs)

    def delete_job(self, job):
        """
        Delete job from the Management Console

        Parameters
        ----------
        job : Job or int
            Job model or job ID representing job to delete
        """

        job_id = job.id if isinstance(job, Job) else job
        self._delete_job(job_id)

    def get_job_run(self, job_run_id):
        """
        Get job run by ID

        Parameters
        ----------
        job_run_id : int
            Job run ID

        Returns
        -------
        JobRun
            Job run model corresponding to specified ID
        """

        attrs = self._get_job_run(job_run_id)
        return JobRun(self, attrs)

    def test_connection(self):
        """
        Test API connection

        Parameters
        ----------
        None
        """

        self._get_jobs()