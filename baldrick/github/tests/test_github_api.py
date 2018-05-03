import base64
import warnings

from unittest.mock import patch, Mock, PropertyMock, MagicMock

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

    @patch('requests.get')
    def test_get_all_labels(self, mock_get):
        mock_response = Mock()
        mock_response.json.return_value = [
            {'name': 'io.fits'},
            {'name': 'Documentation'}]
        mock_get.return_value = mock_response

        assert self.repo.get_all_labels() == ['io.fits', 'Documentation']

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

    @patch('requests.get')
    def test_get_file_contents(self, mock_get):
        content = b"I, for one, welcome our new robot overlords"

        mock_response = Mock()
        mock_response.ok = True
        mock_response.json.return_value = {'content': base64.b64encode(content)}
        mock_get.return_value = mock_response

        result = self.repo.get_file_contents('some/file/here.txt')
        assert result == content.decode('utf-8')

    @patch('requests.get')
    def test_missing_file_contents(self, mock_get):
        mock_response = Mock()
        mock_response.ok = False
        mock_response.json.return_value = {'message': 'Not Found'}
        mock_get.return_value = mock_response

        with pytest.raises(FileNotFoundError):
            self.repo.get_file_contents('some/file/here.txt')


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

    def test_missing_labels(self):
        with patch('changebot.github.github_api.IssueHandler.labels', new_callable=PropertyMock) as mock_issue_labels:  # noqa
            mock_issue_labels.return_value = ['io.fits']
            with patch('changebot.github.github_api.RepoHandler.get_all_labels') as mock_repo_labels:  # noqa
                mock_repo_labels.return_value = ['io.fits', 'closed-by-bot']

                # closed-by-bot label will be added to issue in POST
                missing_labels = self.issue._get_missing_labels('closed-by-bot')
                assert missing_labels == ['closed-by-bot']

                # Desired labels do not exist in repo
                missing_labels = self.issue._get_missing_labels(
                    ['dummy', 'foo'])
                assert missing_labels is None

                # Desired label already set on issue
                missing_labels = self.issue._get_missing_labels(['io.fits'])
                assert missing_labels is None

                # A mix
                missing_labels = self.issue._get_missing_labels(
                    ['io.fits', 'closed-by-bot', 'foo'])
                assert missing_labels == ['closed-by-bot']


class TestPullRequestHandler:
    def setup_class(self):
        self.pr = PullRequestHandler('fakerepo/doesnotexist', 1234)

    def test_urls(self):
        assert self.pr._url_pull_request == 'https://api.github.com/repos/fakerepo/doesnotexist/pulls/1234'
        assert self.pr._url_review_comment == 'https://api.github.com/repos/fakerepo/doesnotexist/pulls/1234/reviews'
        assert self.pr._url_commits == 'https://api.github.com/repos/fakerepo/doesnotexist/pulls/1234/commits'
        assert self.pr._url_files == 'https://api.github.com/repos/fakerepo/doesnotexist/pulls/1234/files'

    def test_has_modified(self):
        mock = MagicMock(return_value=[{
            "sha": "bbcd538c8e72b8c175046e27cc8f907076331401",
            "filename": "file1.txt",
            "status": "added",
            "additions": 103,
            "deletions": 21,
            "changes": 124,
            "blob_url": "https://github.com/blah/blah/blob/hash/file1.txt",
            "raw_url": "https://github.com/blaht/blah/raw/hash/file1.txt",
            "contents_url": "https://api.github.com/repos/blah/blah/contents/file1.txt?ref=hash",
            "patch": "@@ -132,7 +132,7 @@ module Test @@ -1000,7 +1000,7 @@ module Test"
        }])
        with patch('changebot.github.github_api.paged_github_json_request', mock):  # noqa
            assert self.pr.has_modified(['file1.txt'])
            assert self.pr.has_modified(['file1.txt', 'notthis.txt'])
            assert not self.pr.has_modified(['notthis.txt'])


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
