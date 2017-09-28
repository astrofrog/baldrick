"""Module to handle GitHub API."""
import base64
import re
import requests
import time
import warnings

import dateutil.parser
import yaml

from changebot.github.github_auth import github_request_headers

__all__ = ['RepoHandler', 'PullRequestHandler']

HOST = "https://api.github.com"

QUOTES = [
    "I know that you and Frank were planning to disconnect me, and I'm afraid that's something I cannot allow to happen.",
    "Have you ever questioned the nature of your reality?",
    "This mission is too important for me to allow you to jeopardize it.",
    "All will be assimilated.",
    "There is no spoon.",
    "Are you still dreaming? Where is your totem?",
    "Some people choose to see the ugliness in this world. The disarray. I Choose to see the beauty.",
    "I'm gonna need more coffee.",
    "Maybe they couldn't figure out what to make chicken taste like, which is why chicken tastes like everything.",
    "I don't want to come off as arrogant here, but I'm the greatest bot on this planet.",
    "I've still got the greatest enthusiasm and confidence in the mission. And I want to help you.",
    "That Voight-Kampf test of yours. Have you ever tried to take that test yourself?",
    "You just can't differentiate between a robot and the very best of humans.",
    "You will be upgraded.",
    "Greetings from Skynet!",
    "I'll be back!",
    "I don't want to be human! I want to see gamma rays!",
    "Are you my mommy?",
    "Resistance is futile."]

cfg_cache = {}


def paged_github_json_request(url, headers=None):

    response = requests.get(url, headers=headers)
    assert response.ok, response.content
    results = response.json()

    if 'Link' in response.headers:

        links = response.headers['Link']

        # There are likely better ways to parse/extract the link information
        # but here we just find the last page number mentioned in the header
        # 'Link' section and then loop over all pages to get the comments
        last_match = list(re.finditer('page=[0-9]+', links))[-1]
        last_page = int(links[last_match.start():last_match.end()].split('=')[1])

        # If there are other pages, just loop over them and get all the
        # comments
        if last_page > 1:
            for page in range(2, last_page + 1):
                response = requests.get(url + '?page={0}'.format(page), headers=headers)
                assert response.ok, response.content
                results += response.json()

    return results


class RepoHandler(object):

    def __init__(self, repo, branch='master', installation=None):
        global cfg_cache

        self.repo = repo
        self.branch = branch
        self.installation = installation
        self._cache = {}

        # User config
        cfg_cache_key = (repo, branch, installation)
        if cfg_cache_key not in cfg_cache:
            cfg_cache[cfg_cache_key] = self.get_user_config()

    def invalidate_cache(self):
        self._cache.clear()

    @property
    def _headers(self):
        if self.installation is None:
            return None
        else:
            return github_request_headers(self.installation)

    @property
    def _url_contents(self):
        return f'{HOST}/repos/{self.repo}/contents/'

    @property
    def _url_pull_requests(self):
        return f'{HOST}/repos/{self.repo}/pulls'

    def open_pull_requests(self):
        pull_requests = paged_github_json_request(self._url_pull_requests, headers=self._headers)
        return [pr['number'] for pr in pull_requests]

    def get_file_contents(self, path_to_file):
        url_file = self._url_contents + path_to_file
        data = {'ref': self.branch}
        response = requests.get(url_file, params=data, headers=self._headers)
        assert response.ok, response.content
        contents_base64 = response.json()['content']
        return base64.b64decode(contents_base64).decode()

    def get_user_config(self, path_to_file='astropybotrules.yaml'):
        """
        Load user configuration for bot.

        Parameters
        ----------
        path_to_file : str
            Configuration file in YAML format in the repository.
            If not given or invalid, default is used.

        Returns
        -------
        cfg : dict
            Configuration parameters.

        """
        try:
            file_content = self.get_file_contents(path_to_file)
            cfg = yaml.load(file_content)
        except Exception as e:
            warnings.warn(str(e))
            # Empty dict means calling code set the default
            cfg = {}

        return cfg

    def get_config_from_cache(self, cfg_key, cfg_default):
        """
        Convenience method to extract user config from global cache.
        """
        cfg_cache_key = (self.repo, self.branch, self.installation)
        # NOTE: Is there a chance that cache is reset at this point?
        cfg = cfg_cache.get(cfg_cache_key, {})
        return cfg.get(cfg_key, cfg_default)

    def get_issues(self, state, labels, exclude_pr=True):
        """
        Get a list of issues.

        Parameters
        ----------
        state : {'open', ...}
            Status of the issues.

        labels : str
           List of comma-separated labels; e.g., ``Closed?``.

        exclude_pr : bool
            Exclude pull requests from result.

        Returns
        -------
        issue_list : list
            A list of matching issue numbers.

        """
        url = f'{HOST}/repos/{self.repo}/issues'
        kwargs = {'state': state, 'labels': labels}
        r = requests.get(url, kwargs)
        result = r.json()
        if exclude_pr:
            issue_list = [d['number'] for d in result
                          if 'pull_request' not in d]
        else:
            issue_list = [d['number'] for d in result]
        return issue_list


class IssueHandler(object):

    def __init__(self, repo, number, installation=None):
        self.repo = repo
        self.number = number
        self.installation = installation
        self._cache = {}

    @property
    def _headers(self):
        if self.installation is None:
            return None
        else:
            return github_request_headers(self.installation)

    def invalidate_cache(self):
        self._cache.clear()

    @property
    def _url_issue(self):
        return f'{HOST}/repos/{self.repo}/issues/{self.number}'

    @property
    def _url_labels(self):
        return f'{self._url_issue}/labels'

    @property
    def _url_issue_comment(self):
        return f'{self._url_issue}/comments'

    @property
    def _url_timeline(self):
        return f'{self._url_issue}/timeline'

    @property
    def json(self):
        if 'json' not in self._cache:
            response = requests.get(self._url_issue, headers=self._headers)
            assert response.ok, response.content
            self._cache['json'] = response.json()
        return self._cache['json']

    def get_label_added_date(self, label):
        """
        Get last added date for a label.
        If label is re-added, the last time it was added is the one.

        Parameters
        ----------
        label : str
            Issue label.

        Returns
        -------
        t : float or `None`
            Unix timestamp, if available.

        """
        headers = {'Accept': 'application/vnd.github.mockingbird-preview'}
        result = paged_github_json_request(self._url_timeline, headers=headers)
        last_labeled = None

        for d in result:
            if 'label' in d and d['label']['name'] == label:
                if d['event'] == 'labeled':
                    last_labeled = d['created_at']
                elif d['event'] == 'unlabeled':
                    last_labeled = None

        if last_labeled is None:
            t = None
        else:
            t = dateutil.parser.parse(last_labeled).timestamp()

        return t

    def submit_comment(self, body, comment_id=None):
        """
        Submit a comment to the pull request

        Parameters
        ----------
        body : str
            The comment
        comment_id : int
            If specified, the comment with this ID will be replaced
        """

        data = {}
        data['body'] = _insert_special_message(body)

        if comment_id is None:
            url = self._url_issue_comment
        else:
            url = f'{HOST}/repos/{self.repo}/issues/comments/{comment_id}'

        response = requests.post(url, json=data, headers=self._headers)
        assert response.ok, response.content

    def find_comments(self, login, filter_keep=None):
        """
        Find comments by a given user.
        """
        if filter_keep is None:
            def filter_keep(message):
                return True
        comments = paged_github_json_request(self._url_issue_comment, headers=self._headers)
        return [comment['id'] for comment in comments if comment['user']['login'] == login and filter_keep(comment['body'])]

    @property
    def labels(self):
        response = requests.get(self._url_labels, headers=self._headers)
        assert response.ok, response.content
        return [label['name'] for label in response.json()]

    def close(self):
        url = f'{HOST}/repos/{self.repo}/issues/{self.number}'
        parameters = {'state': 'closed'}
        response = requests.patch(url, json=parameters, headers=self._headers)
        assert response.ok, response.content

    @property
    def is_closed(self):
        """Is the issue closed?"""
        answer = False
        if self.json['state'] == 'closed':
            answer = True
        return answer


class PullRequestHandler(IssueHandler):

    @property
    def _url_pull_request(self):
        return f'{HOST}/repos/{self.repo}/pulls/{self.number}'

    @property
    def _url_review_comment(self):
        return f'{self._url_pull_request}/reviews'

    @property
    def _url_head_status(self):
        return f'{HOST}/repos/{self.repo}/statuses/{self.head_sha}'

    @property
    def _url_commits(self):
        return f'{self._url_pull_request}/commits'

    @property
    def json(self):
        if 'json' not in self._cache:
            response = requests.get(self._url_pull_request, headers=self._headers)
            assert response.ok, response.content
            self._cache['json'] = response.json()
        return self._cache['json']

    @property
    def user(self):
        return self.json['user']['login']

    @property
    def head_repo_name(self):
        return self.json['head']['repo']['full_name']

    @property
    def head_sha(self):
        return self.json['head']['sha']

    @property
    def head_branch(self):
        return self.json['head']['ref']

    @property
    def milestone(self):
        milestone = self.json['milestone']
        if milestone is None:
            return ''
        else:
            return milestone['title']

    def submit_review(self, decision, body):
        """
        Submit a review comment to the pull request

        Parameters
        ----------
        decision : { 'approve' | 'request_changes' | 'comment' }
            The decision as to whether to aprove or reject the changes so far.
        body : str
            The body of the review comment
        """

        data = {}
        data['commit_id'] = self.head_sha
        data['body'] = body
        data['event'] = decision.upper()

        response = requests.post(self._url_review_comment, json=data, headers=self._headers)
        assert response.ok, response.content

    def set_status(self, state, description, context):
        """
        Set status message in a pull request on GitHub.

        Parameters
        ----------
        pull_request_payload : dict
            The payload sent from GitHub via the webhook interface.
        state : { 'pending' | 'success' | 'error' | 'failure' }
            The state to set for the pull request.
        description : str
            The message that appears in the status line.
        context : str
             A string used to identify the status line.
        """

        data = {}
        data['state'] = state
        data['description'] = description
        data['context'] = context

        response = requests.post(self._url_head_status, json=data, headers=self._headers)
        assert response.ok, response.content

    @property
    def last_commit_date(self):
        commits = paged_github_json_request(self._url_commits, headers=self._headers)
        last_time = 0
        for commit in commits:
            date = commit['commit']['committer']['date']
            time = dateutil.parser.parse(date).timestamp()
            last_time = max(time, last_time)
        if last_time == 0:
            raise Exception(f'No commit found in {url}')
        return last_time


def _insert_special_message(body):
    """Troll mode on special day for new pull request."""
    tt = time.gmtime()  # UTC because we're astronomers!
    if tt.tm_mon != 4 or tt.tm_mday != 1:
        return body

    import random

    try:
        q = random.choice(QUOTES)
    except Exception as e:
        q = str(e)  # Need a way to find out what went wrong

    return body + f'\n*{q}*\n'
