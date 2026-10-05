"""Module to handle GitHub API."""

import base64
import os
import re
from datetime import UTC, datetime

import dateutil.parser
import requests
from cachetools import TTLCache
from flask import current_app
from loguru import logger

from baldrick.config import Config, loads
from baldrick.github.github_auth import github_request_headers

__all__ = ["GitHubHandler", "IssueHandler", "OrgHandler", "PullRequestHandler", "RepoHandler"]

HOST = "https://api.github.com"
HOST_NONAPI = "https://github.com"

FILE_CACHE = TTLCache(maxsize=512, ttl=float(os.environ.get("BALDRICK_FILE_CACHE_TTL", 60)))
ORG_CONFIG_CACHE = TTLCache(maxsize=512, ttl=float(os.environ.get("BALDRICK_FILE_CACHE_TTL", 60)))


def paged_github_json_request(url, headers=None):

    response = requests.get(url, headers=headers)
    response.raise_for_status()
    results = response.json()

    if "Link" in response.headers:
        links = response.headers["Link"]

        # There are likely better ways to parse/extract the link information
        # but here we just find the last page number mentioned in the header
        # 'Link' section and then loop over all pages to get the comments
        last_match = list(re.finditer("page=[0-9]+", links))[-1]
        last_page = int(links[last_match.start() : last_match.end()].split("=")[1])

        # If there are other pages, just loop over them and get all the
        # comments
        if last_page > 1:
            for page in range(2, last_page + 1):
                response = requests.get(url + f"?page={page}", headers=headers)
                response.raise_for_status()
                results += response.json()

    return results


class GitHubHandler:
    """
    A base class for things that represent things the github app can operate on.
    """

    def __init__(self, installation=None):
        self.installation = installation
        self._cache = {}

    def invalidate_cache(self):
        self._cache.clear()

    @property
    def _headers(self):
        if self.installation is None:
            return {}
        return github_request_headers(self.installation)


class OrgHandler(GitHubHandler):
    def __init__(self, org_name, installation=None):
        self.org_name = org_name
        super().__init__(installation=installation)

    def is_member(self, user):
        response = requests.get(f"{HOST}/orgs/{self.org_name}/members/{user}", headers=self._headers)
        if response.status_code == 204:
            return True
        if response.status_code == 404:
            return False
        raise Exception(
            f"An error occurred when trying to determine organization membership (status code {response.status_code})"
        )


class RepoHandler(GitHubHandler):
    def __init__(self, repo, installation=None):
        self.repo = repo
        super().__init__(installation=installation)

    @property
    def org_handler(self):
        if "org_handler" not in self._cache:
            if self.repo_info["owner"]["type"] != "Organization":
                raise Exception("Repository does not belong to an organization")
            self._cache["org_handler"] = OrgHandler(self.repo.split("/")[0], installation=self.installation)
        return self._cache["org_handler"]

    @property
    def repo_info(self):
        """
        The return of GET /repos/{org}/{repo}
        """
        response = requests.get(f"{HOST}/repos/{self.repo}", headers=self._headers)
        if not response.ok:
            raise ValueError(f"Unable to fetch repo information {response.json()}")
        return response.json()

    @property
    def default_branch(self):
        return self.repo_info["default_branch"]

    @property
    def _url_contents(self):
        return f"{HOST}/repos/{self.repo}/contents/"

    def get_file_contents(self, path_to_file, branch=None):
        if branch is None:
            branch = self.default_branch
        cache_key = f"{self.repo}:{path_to_file}@{branch}"

        # TTLCache raises KeyError for expired as well as missing keys, so
        # we access the cache via try/except
        try:
            return FILE_CACHE[cache_key]
        except KeyError:
            pass

        url_file = self._url_contents + path_to_file
        data = {"ref": branch}
        response = requests.get(url_file, params=data, headers=self._headers)
        if not response.ok and response.json()["message"] == "Not Found":
            raise FileNotFoundError(url_file)
        response.raise_for_status()
        contents_base64 = response.json()["content"]
        contents = base64.b64decode(contents_base64).decode()

        FILE_CACHE[cache_key] = contents
        return contents

    def get_org_config(self, path_to_file="pyproject.toml"):
        """
        Load configuration shared by all repositories of the owner.

        This is read from the ``.github`` repository of the organization (or
        user) that owns this repository, following the same ``[tool.<bot>]``
        layout as the per-repository configuration. The bot needs to be
        installed on the ``.github`` repository for this to work; if it is not,
        or the repository or file does not exist, an empty configuration is
        returned.

        Parameters
        ----------
        path_to_file : `str`
            Path to the ``pyproject.toml`` file in the ``.github`` repository.

        Returns
        -------
        cfg : `baldrick.config.Config`
            Configuration parameters.
        """
        owner = self.repo.split("/")[0]
        cache_key = f"{owner}:{path_to_file}"

        # The parsed configuration is cached (not just the file contents) to
        # avoid looking up the default branch of the .github repository on
        # every call. TTLCache raises KeyError for expired as well as missing
        # keys, so we access the cache via try/except.
        try:
            return ORG_CONFIG_CACHE[cache_key].copy()
        except KeyError:
            pass

        org_repo = RepoHandler(f"{owner}/.github", installation=self.installation)

        try:
            file_content = org_repo.get_file_contents(path_to_file)
        except (FileNotFoundError, ValueError):
            logger.debug(f"No {path_to_file} found in {org_repo.repo}.")
            org_config = Config()
        else:
            org_config = loads(file_content, tool=current_app.bot_username) or Config()
            logger.trace(f"Got the following config from {org_repo.repo}: {org_config.summary()}")

        ORG_CONFIG_CACHE[cache_key] = org_config
        return org_config.copy()

    def get_repo_config(self, branch=None, path_to_file="pyproject.toml"):
        """
        Load configuration from the repository.

        The configuration is built up in layers, each overriding individual
        settings from the previous one: the global app configuration, the
        owner's ``.github`` repository configuration (see `get_org_config`),
        the fallback configuration in the repository, and finally the
        repository's own configuration.

        Parameters
        ----------
        branch : `str`
            The branch to read the config file from. (Will default to the default branch)

        path_to_file : `str`
            Path to the ``pyproject.toml`` file in the repository. Will default
            to the root of the repository.

        Returns
        -------
        cfg : `baldrick.config.Config`
            Configuration parameters.

        """
        branch = branch or self.default_branch
        app_config = current_app.conf.copy()
        org_config = self.get_org_config(path_to_file=path_to_file)
        fallback_config = Config()
        repo_config = Config()

        try:
            file_content = self.get_file_contents(path_to_file, branch=branch)
        except FileNotFoundError:
            logger.debug(f"No config file found in {self.repo}@{branch}.")
            file_content = None

        if file_content:
            repo_config = loads(file_content, tool=current_app.bot_username) or Config()
            logger.trace(f"Got the following config from {self.repo}@{branch}: {repo_config.summary()}")
            if len(repo_config) == 0:
                logger.exception(
                    f"Failed to load config in {self.repo} on branch {branch}, despite finding a pyproject.toml file."
                )

            if getattr(current_app, "fall_back_config", None):
                fallback_config = loads(file_content, tool=current_app.fall_back_config) or Config()
                if len(fallback_config) == 0:
                    logger.trace(f"Didn't find a fallback config in {self.repo}@{branch}.")

        # Priority is 1) repo_config 2) fallback_config 3) org_config 4) app_config
        app_config.update_from_config(org_config)
        app_config.update_from_config(fallback_config)
        app_config.update_from_config(repo_config)

        logger.debug(f"Got this combined config from {self.repo}@{branch}: {app_config.summary()}")

        return app_config

    def get_config_value(self, cfg_key, cfg_default=None, branch=None):
        """
        Convenience method to extract user configuration values.

        Values are extracted from the repository configuration, and if not
        defined, they are extracted from the global app configuration. If this
        does not exist either, the value is set to the ``cfg_default`` argument.
        """
        cfg = self.get_repo_config(branch=branch)

        config = current_app.conf.get(cfg_key, {}).copy()
        config.update(cfg.get(cfg_key, {}))

        if len(config) > 0:
            return config
        return cfg_default

    def set_status(self, state, description, context, commit_hash, target_url=None):
        """
        Set status message on a commit on GitHub.

        Parameters
        ----------
        state : { 'pending' | 'success' | 'error' | 'failure' }
            The state to set for the pull request.

        description : str
            The message that appears in the status line.

        context : str
            A string used to identify the status line.

        commit_hash: str
            The commit hash to set the status on.

        target_url : str or `None`
            Link to bot comment that is relevant to this status, if given.
        """

        data = {}
        data["state"] = state
        data["description"] = description
        data["context"] = context

        if target_url is not None:
            data["target_url"] = target_url

        url = f"{HOST}/repos/{self.repo}/statuses/{commit_hash}"
        response = requests.post(url, json=data, headers=self._headers)
        response.raise_for_status()

    def list_statuses(self, commit_hash):
        """
        List status messages on a commit on GitHub.

        Parameters
        ----------
        commit_hash : str
            The commit has to get the statuses for
        """

        url = f"{HOST}/repos/{self.repo}/commits/{commit_hash}/statuses"
        results = paged_github_json_request(url, headers=self._headers)

        statuses = {}
        for result in results:
            context = result["context"]
            statuses[context] = {
                "state": result["state"],
                "description": result["description"],
                "target_url": result.get("target_url"),
            }

        return statuses

    def list_checks(self, commit_hash, only_ours=True):
        """
        List check messages on a commit on GitHub.

        Parameters
        ----------
        commit_hash : str
            The commit has to get the statuses for

        only_ours : `bool`, optional
            Only return status that this app has posted.
        """
        url = f"{HOST}/repos/{self.repo}/commits/{commit_hash}/check-runs"
        headers = self._headers
        headers["Accept"] = "application/vnd.github.antiope-preview+json"
        results = paged_github_json_request(url, headers=headers)

        checks = {}
        for result in results.get("check_runs", []):
            # Skip checks from other apps if specified.
            if only_ours and result["app"]["id"] != current_app.integration_id:
                continue

            context = result["external_id"]
            # These keys match the kwargs to set_check
            checks[context] = {
                "external_id": result["external_id"],
                "title": result["output"]["title"],
                "summary": result["output"]["summary"],
                "name": result["name"],
                "text": result["output"].get("text"),
                "commit_hash": result["head_sha"],
                "details_url": result.get("details_url"),
                "status": result["status"],
                "conclusion": result["conclusion"],
                "check_id": result["id"],
            }

        return checks

    @property
    def _url_pull_requests(self):
        return f"{HOST}/repos/{self.repo}/pulls"

    def open_pull_requests(self):
        pull_requests = paged_github_json_request(self._url_pull_requests, headers=self._headers)
        return [pr["number"] for pr in pull_requests]

    def get_pull_requests_by(self, user):
        """
        Get the numbers of pull requests opened by the given user, oldest first.
        """
        url = f"{HOST}/search/issues"
        params = {"q": f"repo:{self.repo} type:pr author:{user}", "sort": "created", "order": "asc", "per_page": 100}
        response = requests.get(url, params, headers=self._headers)
        response.raise_for_status()
        return [item["number"] for item in response.json()["items"]]

    def count_opened_by(self, user, kind, since):
        """
        Count the issues or pull requests opened by the given user anywhere on
        GitHub since the given time.

        Parameters
        ----------
        user : str
            The GitHub username.
        kind : {'issue', 'pr'}
            Whether to count issues or pull requests.
        since : `datetime.datetime`
            Only count issues or pull requests created at or after this time.
        """
        url = f"{HOST}/search/issues"
        query = f"author:{user} type:{kind} created:>={since:%Y-%m-%dT%H:%M:%SZ}"
        response = requests.get(url, {"q": query, "per_page": 1}, headers=self._headers)
        response.raise_for_status()
        return response.json()["total_count"]

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
        url = f"{HOST}/repos/{self.repo}/issues"
        kwargs = {"state": state, "labels": labels}
        r = requests.get(url, kwargs, headers=self._headers)
        result = r.json()
        if exclude_pr:
            issue_list = [d["number"] for d in result if "pull_request" not in d]
        else:
            issue_list = [d["number"] for d in result]
        return issue_list

    def get_all_labels(self):
        """Get all label options for this repo"""
        url = f"{HOST}/repos/{self.repo}/labels"
        result = paged_github_json_request(url, headers=self._headers)
        return [label["name"] for label in result]


class IssueHandler(RepoHandler):
    def __init__(self, repo, number, installation=None):
        self.number = number
        super().__init__(repo, installation=installation)

    @property
    def _url_issue(self):
        return f"{HOST}/repos/{self.repo}/issues/{self.number}"

    @property
    def _url_issue_nonapi(self):
        return f"{HOST_NONAPI}/{self.repo}/issues/{self.number}"

    @property
    def _url_labels(self):
        return f"{self._url_issue}/labels"

    @property
    def _url_issue_comment(self):
        return f"{self._url_issue}/comments"

    @property
    def _url_timeline(self):
        return f"{self._url_issue}/timeline"

    @property
    def json(self):
        if "json" not in self._cache:
            response = requests.get(self._url_issue, headers=self._headers)
            response.raise_for_status()
            self._cache["json"] = response.json()
        return self._cache["json"]

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
        headers = {"Accept": "application/vnd.github.mockingbird-preview"}
        result = paged_github_json_request(self._url_timeline, headers=headers)
        last_labeled = None

        for d in result:
            if "label" in d and d["label"]["name"] == label:
                if d["event"] == "labeled":
                    last_labeled = d["created_at"]
                elif d["event"] == "unlabeled":
                    last_labeled = None

        if last_labeled is None:
            t = None
        else:
            t = dateutil.parser.parse(last_labeled).timestamp()

        return t

    def submit_comment(self, body, comment_id=None, return_url=False):
        """
        Submit a comment to the pull request

        Parameters
        ----------
        body : str
            The comment
        comment_id : int
            If specified, the comment with this ID will be replaced
        return_url : bool
            Return URL of posted comment.

        Returns
        -------
        url : str or `None`
            URL of the posted comment, if requested.
        """

        data = {}
        data["body"] = body

        if comment_id is None:
            url = self._url_issue_comment
        else:
            url = f"{HOST}/repos/{self.repo}/issues/comments/{comment_id}"

        response = requests.post(url, json=data, headers=self._headers)
        response.raise_for_status()

        if return_url:
            comment_id = response.json()["url"].split("/")[-1]
            return f"{self._url_issue_nonapi}#issuecomment-{comment_id}"
        return None

    def _find_comments(self, login, filter_keep=None):
        if filter_keep is None:

            def filter_keep(message):
                return True

        comments = paged_github_json_request(self._url_issue_comment, headers=self._headers)
        return [comment for comment in comments if filter_keep(comment["body"])]

    def find_comments(self, login, filter_keep=None):
        """
        Find comments by a given user.
        """
        comments = self._find_comments(login, filter_keep=filter_keep)
        return [comment["id"] for comment in comments if comment["user"]["login"] == login]

    def last_comment_date(self, login, filter_keep=None):
        """
        Find the last date on which a comment was made.
        """
        comments = self._find_comments(login, filter_keep=filter_keep)
        dates = [comment["created_at"] for comment in comments if comment["user"]["login"] == login]
        if len(dates) > 0:
            return dateutil.parser.parse(sorted(dates)[-1]).timestamp()
        return None

    @property
    def labels(self):
        """Get labels for this issue"""
        response = requests.get(self._url_labels, headers=self._headers)
        response.raise_for_status()
        return [label["name"] for label in response.json()]

    # We take this out of set_labels so we can test it without mock
    def _get_missing_labels(self, labels):
        if not isinstance(labels, list):
            labels = [labels]

        # If label already set, do nothing
        missing_labels = set(labels).difference(self.labels)
        if len(missing_labels) == 0:
            return None

        # If label does not already exist in the repo, give a warning
        repo_labels = self.get_all_labels()
        nonexistent_labels = missing_labels.difference(repo_labels)
        if len(nonexistent_labels) > 0:
            pass

        # Return labels to be set
        missing_labels = missing_labels.intersection(repo_labels)
        if len(missing_labels) > 0:
            return list(missing_labels)
        return None

    def set_labels(self, labels):
        """Set label(s) to issue"""

        missing_labels = self._get_missing_labels(labels)
        if missing_labels is None:
            return

        response = requests.post(self._url_labels, headers=self._headers, json=missing_labels)
        response.raise_for_status()

    def close(self):
        url = f"{HOST}/repos/{self.repo}/issues/{self.number}"
        parameters = {"state": "closed"}
        response = requests.patch(url, json=parameters, headers=self._headers)
        response.raise_for_status()

    @property
    def is_closed(self):
        """Is the issue closed?"""
        answer = False
        if self.json["state"] == "closed":
            answer = True
        return answer


class PullRequestHandler(IssueHandler):
    # https://developer.github.com/v3/checks/runs/#create-a-check-run
    def set_check(
        self,
        external_id,
        title,
        name=None,
        summary=None,
        text=None,
        commit_hash="head",
        details_url=None,
        status=None,
        conclusion="neutral",
        check_id=None,
        completed_at=None,
    ):
        """
        Set check status.

        .. note:: This method does not provide API access to full
                  check run capability (e.g., annotation and
                  image). Add them as needed.

        Parameters
        ----------
        external_id : `str`
            The internal reference for this check, used to reference the check
            later, to update it.

        title: `str`
            The short description of the check to be put in the status line of the PR.

        name : `str`, optional
            Name of the check, defaults to ``{bot_username}:{external_id}`` if
            not specified, is displayed first in the status line.

        summary : `str`
            Summary of the check run, displays at the top of the checks page.

        text : `str`, optional
            The full body of the check, displayed on the checks page.

        commit_hash: { 'head' | 'base' }, optional
            The SHA of the commit.

        details_url : `str` or `None`, optional
            The URL of the integrator's site that has the full details
            of the check.

        status : { 'queued' | 'in_progress' | 'completed' }
            The current status.

        conclusion : { 'success' | 'failure' | 'neutral' | 'cancelled' | 'timed_out' | 'action_required' }
            The final conclusion of the check.
            Required if you provide a status of ``'completed'``.
            When the conclusion is ``'action_required'``, additional details
            should be provided on the site specified by ``'details_url'``.
            Note: Providing conclusion will automatically set the status
            parameter to ``'completed'``.

        check_id : `str`, optional
            If specified this check will be updated rather than a new check
            being made.

        completed_at : `bool` or `datetime.datetime`
            The time the check completed. If `None` this will not be set, if
            `True` it will be set to the time this method is called, otherwise
            it should be a `datetime.datetime.`

        """
        url = f"{HOST}/repos/{self.repo}/check-runs"
        headers = self._headers
        headers["Accept"] = "application/vnd.github.antiope-preview+json"

        if commit_hash == "head":
            commit_hash = self.head_sha
        elif commit_hash == "base":
            commit_hash = self.base_sha

        if completed_at is True:
            completed_at = datetime.now(UTC)
        if completed_at is not None:
            completed_at = completed_at.isoformat(timespec="seconds") + "Z"

        # If name isn't specified revert to external_id
        name = name or f"{current_app.bot_username}:{external_id}"

        output = {"title": title, "summary": summary or ""}
        if text is not None:
            output["text"] = text

        parameters = {
            "external_id": external_id,
            "name": name,
            "head_sha": commit_hash,
            "status": status,
            "output": output,
        }

        if details_url is not None:
            parameters["details_url"] = details_url

        if status == "completed" and conclusion is None:
            logger.warning(
                "When a GitHub check status is completed, conclusion must be specified, setting it to 'neutral'"
            )
            conclusion = "neutral"

        if conclusion is not None:
            parameters["conclusion"] = conclusion
            if completed_at is not None:
                parameters["completed_at"] = completed_at
            # The GitHub API does this automatically, but we do it explicitly
            # here for consistency and for tests!
            parameters["status"] = "completed"

        logger.trace(f"Sending GitHub check with {parameters}")

        if not check_id:
            response = requests.post(url, headers=headers, json=parameters)
        else:
            response = requests.patch(url + f"/{check_id}", headers=headers, json=parameters)
        response.raise_for_status()

    def set_status(self, state, description, context, commit_hash="head", target_url=None):
        """
        Set status message on a commit on GitHub.

        Parameters
        ----------
        state : { 'pending' | 'success' | 'error' | 'failure' }
            The state to set for the pull request.

        description : str
            The message that appears in the status line.

        context : str
            A string used to identify the status line.

        commit_hash: { 'head' | 'base' }
            The commit hash to set the status on.
            Defaults to "head" can also be "base".

        target_url : str or `None`
            Link to bot comment that is relevant to this status, if given.

        """
        if commit_hash == "head":
            commit_hash = self.head_sha
        elif commit_hash == "base":
            commit_hash = self.base_sha
        super().set_status(state, description, context, commit_hash, target_url)

    def list_statuses(self, commit_hash="head"):
        """
        List status messages on a commit on GitHub.

        Parameters
        ----------
        commit_hash : str, optional
            The commit hash to set the status on. Defaults to "head" can also be "base".
        """
        if commit_hash == "head":
            commit_hash = self.head_sha
        elif commit_hash == "base":
            commit_hash = self.base_sha
        return super().list_statuses(commit_hash)

    def list_checks(self, commit_hash="head", only_ours=True):
        """
        List checks on a commit on GitHub.

        Parameters
        ----------
        commit_hash : `str`, optional
            The commit hash to set the check on. Defaults to "head" can also be "base".
        only_ours : `bool`, optional
            Only return checks which were posted by this GitHub app.
        """
        if commit_hash == "head":
            commit_hash = self.head_sha
        elif commit_hash == "base":
            commit_hash = self.base_sha
        return super().list_checks(commit_hash, only_ours=only_ours)

    @property
    def _url_pull_request(self):
        return f"{HOST}/repos/{self.repo}/pulls/{self.number}"

    @property
    def _url_review_comment(self):
        return f"{self._url_pull_request}/reviews"

    @property
    def _url_head_status(self):
        return f"{HOST}/repos/{self.repo}/statuses/{self.head_sha}"

    @property
    def _url_commits(self):
        return f"{self._url_pull_request}/commits"

    @property
    def _url_files(self):
        return f"{self._url_pull_request}/files"

    @property
    def json(self):
        if "json" not in self._cache:
            response = requests.get(self._url_pull_request, headers=self._headers)
            response.raise_for_status()
            self._cache["json"] = response.json()
        return self._cache["json"]

    @property
    def user(self):
        return self.json["user"]["login"]

    @property
    def head_repo_name(self):
        return self.json["head"]["repo"]["full_name"]

    @property
    def head_sha(self):
        return self.json["head"]["sha"]

    @property
    def head_branch(self):
        return self.json["head"]["ref"]

    @property
    def base_branch(self):
        return self.json["base"]["ref"]

    @property
    def base_sha(self):
        return self.json["base"]["sha"]

    @property
    def milestone(self):
        milestone = self.json["milestone"]
        if milestone is None:
            return ""
        return milestone["title"]

    @property
    def draft(self):
        return self.json["draft"]

    def get_modified_files(self):
        """Get all the filenames of the files modified by this PR."""
        files = paged_github_json_request(self._url_files, headers=self._headers)
        return [f["filename"] for f in files]

    def get_file_contents(self, path_to_file, branch=None):
        """
        Get the contents of a file.

        This will get the file from the head branch of the PR by default.
        """
        if not branch:
            branch = self.head_branch
        return super().get_file_contents(path_to_file, branch=branch)

    def get_repo_config(self, branch=None, path_to_file="pyproject.toml"):
        """
        Load user configuration for bot.

        Parameters
        ----------
        branch : `str`
            The branch to read the config file from. (Will default to the base
            branch of the PR i.e. the one the PR is opened against.)

        path_to_file : `str`
            Path to the ``pyproject.toml`` file in the repository. Will default
            to the root of the repository.

        Returns
        -------
        cfg : dict
            Configuration parameters.

        """
        if not branch:
            branch = self.base_branch
        return super().get_repo_config(branch=branch, path_to_file=path_to_file)

    def has_modified(self, filelist):
        """Check if PR has modified any of the given list of filename(s)."""
        found = False
        files = paged_github_json_request(self._url_files, headers=self._headers)
        for d in files:
            if d["filename"] in filelist:
                found = True
                break

        return found

    def submit_review(self, decision, body):
        """
        Submit a review comment to the pull request

        Parameters
        ----------
        decision : { 'approve' | 'request_changes' | 'comment' }
            The decision as to whether to approve or reject the changes so far.
        body : str
            The body of the review comment
        """

        data = {}
        data["commit_id"] = self.head_sha
        data["body"] = body
        data["event"] = decision.upper()

        response = requests.post(self._url_review_comment, json=data, headers=self._headers)
        response.raise_for_status()

    @property
    def last_commit_date(self):
        commits = paged_github_json_request(self._url_commits, headers=self._headers)
        last_time = 0
        for commit in commits:
            date = commit["commit"]["committer"]["date"]
            t = dateutil.parser.parse(date).timestamp()
            last_time = max(t, last_time)
        if last_time == 0:
            raise Exception(f"No commit found in {self._url_commits}")
        return last_time
