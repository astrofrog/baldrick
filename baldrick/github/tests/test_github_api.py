import warnings
from unittest.mock import patch, Mock, PropertyMock

import pytest

from changebot.github import github_api
from changebot.github.github_api import (RepoHandler, IssueHandler,
                                         PullRequestHandler)


# TODO: Add more tests to increase coverage.

class TestRepoHandler:
    def setup_class(self):
        self.repo = RepoHandler('fakerepo/doesnotexist', branch='awesomebot')

    @patch('requests.get')
    def test_get_issues(self, mock_get):
        # http://engineroom.trackmaven.com/blog/real-life-mocking/
        mock_response = Mock()
        mock_response.json.return_value = [
            {'number': 42, 'state': 'open'},
            {'number': 55, 'state': 'open',
             'pull_request': {'diff_url': 'blah'}}]
        mock_get.return_value = mock_response

        assert self.repo.get_issues('open', 'Close?') == [42]
        assert self.repo.get_issues('open', 'Close?',
                                    exclude_pr=False) == [42, 55]

    def test_urls(self):
        assert self.repo._url_contents == 'https://api.github.com/repos/fakerepo/doesnotexist/contents/'
        assert self.repo._url_pull_requests == 'https://api.github.com/repos/fakerepo/doesnotexist/pulls'
        assert self.repo._headers is None


# NOTE: Might hit API limit?
class TestRealRepoHandler:
    def setup_class(self):
        # TODO: Use astropy/astropy-bot when #42 is merged.
        self.repo = RepoHandler('pllim/astropy-bot', branch='changelog-onoff')

    def test_get_config(self):
        # These are set to False in YAML; defaults must not be used.
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter('always')
            do_changelog_check = self.repo.get_config_value(
                'changelog_check', True)
            do_autoclose_pr = self.repo.get_config_value(
                'autoclose_stale_pull_request', True)

        hit_api_limit = False
        if len(w) > 0:
            hit_api_limit = True

        if hit_api_limit:
            pytest.xfail(str(w[-1].message))
        else:
            assert not (do_changelog_check or do_autoclose_pr)


class TestIssueHandler:
    def setup_class(self):
        self.issue = IssueHandler('fakerepo/doesnotexist', 1234)

    def test_urls(self):
        assert self.issue._url_issue == 'https://api.github.com/repos/fakerepo/doesnotexist/issues/1234'
        assert self.issue._url_issue_nonapi == 'https://github.com/fakerepo/doesnotexist/issues/1234'
        assert self.issue._url_labels == 'https://api.github.com/repos/fakerepo/doesnotexist/issues/1234/labels'
        assert self.issue._url_issue_comment == 'https://api.github.com/repos/fakerepo/doesnotexist/issues/1234/comments'
        assert self.issue._url_timeline == 'https://api.github.com/repos/fakerepo/doesnotexist/issues/1234/timeline'

    @pytest.mark.parametrize(('state', 'answer'),
                             [('open', False), ('closed', True)])
    def test_is_closed(self, state, answer):
        with patch('changebot.github.github_api.IssueHandler.json', new_callable=PropertyMock) as mock_json:  # noqa
            mock_json.return_value = {'state': state}
            assert self.issue.is_closed is answer


class TestPullRequestHandler:
    def setup_class(self):
        self.pr = PullRequestHandler('fakerepo/doesnotexist', 1234)

    def test_urls(self):
        assert self.pr._url_pull_request == 'https://api.github.com/repos/fakerepo/doesnotexist/pulls/1234'
        assert self.pr._url_review_comment == 'https://api.github.com/repos/fakerepo/doesnotexist/pulls/1234/reviews'
        assert self.pr._url_commits == 'https://api.github.com/repos/fakerepo/doesnotexist/pulls/1234/commits'


@patch('time.gmtime')
def test_special_msg(mock_time):
    import random
    random.seed(1234)
    body = 'Hello World\n'

    mock_time.return_value = Mock(tm_mon=4, tm_mday=2)
    assert github_api._insert_special_message(body) == body

    mock_time.return_value = Mock(tm_mon=4, tm_mday=1)
    body2 = github_api._insert_special_message(body)
    assert '\n*Greetings from Skynet!*\n' in body2
