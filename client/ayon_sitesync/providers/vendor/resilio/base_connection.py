import requests
from .errors import *


try:
    from json import JSONDecodeError
except ImportError:
    JSONDecodeError = ValueError


BASE_API_URL = '/api/v2'


def api_request(func):
    def wrapper(self, url, *args, **kwargs):
        kwargs['headers'] = {
            'Authorization': 'Token ' + self._token,
            'Content-Type': 'application/json'
        }
        kwargs['verify'] = self._verify

        url = self._base_url + url

        try:
            response = func(self, url, *args, **kwargs)
        except requests.RequestException as e:
            raise ApiConnectionError('Connection to Management Console failed', e)

        if response.status_code >= 400:
            try:
                error_json = response.json()
                message = error_json['message'] if 'message' in error_json else ''
                data = error_json['data'] if 'data' in error_json else None
            except JSONDecodeError:
                message = response.text
                data = None

            if response.status_code == 401:
                raise ApiUnauthorizedError(message)
            else:
                raise ApiError(message, status_code=response.status_code, data=data)

        return response
    return wrapper


class BaseConnection(object):
    """
    Base class for interaction with Management Console API

    Don't use it directly

    See also
    --------
    ConnectApi
    """

    def __init__(self, address, token, verify):
        self._token = token
        self._address = address
        self._base_url = address + BASE_API_URL
        self._verify = verify

    # Request methods
    @api_request
    def _get(self, *args, **kwargs):
        return requests.get(*args, **kwargs)

    @api_request
    def _post(self, *args, **kwargs):
        return requests.post(*args, **kwargs)

    @api_request
    def _put(self, *args, **kwargs):
        return requests.put(*args, **kwargs)

    @api_request
    def _delete(self, *args, **kwargs):
        return requests.delete(*args, **kwargs)

    # Helpers
    def _create(self, *args, **kwargs):
        r = self._post(*args, **kwargs)
        try:
            return r.json()['id']
        except JSONDecodeError as e:
            raise ApiError('Response is not a json: ' + r.text, e)

    def _get_json(self, *args, **kwargs):
        r = self._get(*args, **kwargs)
        try:
            return r.json()
        except JSONDecodeError as e:
            raise ApiError('Response is not a json: ' + r.text, e)

    # Agents
    def _get_agents(self):
        return self._get_json('/agents')

    def _get_agent(self, agent_id):
        return self._get_json('/agents/%d' % agent_id)

    def _update_agent(self, agent_id, attrs):
        self._put('/agents/%d' % agent_id, json=attrs)

    def _get_agent_config(self):
        return self._get_json('/agents/config')

    def _delete_agent(self, agent_id):
        self._delete('/agents/%d' % agent_id)


    # Jobs
    def _get_jobs(self):
        return self._get_json('/jobs')

    def _get_job(self, job_id):
        return self._get_json('/jobs/%d' % job_id)

    def _create_job(self, attrs):
        return self._create('/jobs', json=attrs)

    def _update_job(self, job_id, attrs):
        self._put('/jobs/%d' % job_id, json=attrs)

    def _delete_job(self, job_id):
        self._delete('/jobs/%d' % job_id)

    def _start_job(self, job_id):
        params = { 'job_id': job_id }
        return self._create('/runs', json=params)


    # Job runs
    def _get_job_run(self, job_run_id):
        return self._get_json('/runs/%d' % job_run_id)

    def _get_last_job_run(self, job_id):
        job_runs = self._get_json('/runs?limit=1&job_id=%d&sort=finish_time' % job_id)
        return job_runs['data'][0] if len(job_runs['data']) else None

    def _stop_job_run(self, job_run_id):
        self._put('/runs/%d/stop' % job_run_id)

    def _pause_job_run(self, job_run_id):
        self._put('/runs/%d/pause' % job_run_id)

    def _resume_job_run(self, job_run_id):
        self._put('/runs/%d/resume' % job_run_id)

    def _get_agent_status(self, job_run_id, agent_id):
        return self._get_json('/runs/%d/agents/%d' % (job_run_id, agent_id))

    def _get_agents_statuses(self, job_run_id):
        return self._get_json('/runs/%d/agents' % job_run_id)


    # Groups
    def _get_groups(self):
        return self._get_json('/groups')

    def _get_group(self, group_id):
        return self._get_json('/groups/%d' % group_id)

    def _create_group(self, attrs):
        return self._create('/groups', json=attrs)

    def _update_group(self, group_id, attrs):
        self._put('/groups/%d' % group_id, json=attrs)

    def _delete_group(self, group_id):
        self._delete('/groups/%d' % group_id)
