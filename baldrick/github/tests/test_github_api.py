from unittest.mock import patch, Mock

from changebot.github import github_api
from changebot.github.github_api import RepoHandler


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
