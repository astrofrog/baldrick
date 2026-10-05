# For these tests, we patch the repo and pull request handler directly rather
# than the requests to the server, as we assume the repo and pull request
# handlers are tested inside baldrick.

from copy import copy
from unittest.mock import MagicMock, patch

import pytest
import requests

from baldrick.plugins.github_org_vetting import ALLOWLIST_CACHE, close_if_not_in_org, load_allowlist
from baldrick.plugins.github_pull_requests import PULL_REQUEST_CHECKS


def setup_module(module):
    # Importing the plugin registers close_if_not_in_org in the global
    # PULL_REQUEST_CHECKS registry, so remove it here to stop it leaking into
    # other test modules.
    module.PULL_REQUEST_CHECKS_ORIG = copy(PULL_REQUEST_CHECKS)
    module.PULL_REQUEST_CHECKS_ORIG.pop(close_if_not_in_org, None)
    PULL_REQUEST_CHECKS.pop(close_if_not_in_org, None)


def teardown_module(module):
    PULL_REQUEST_CHECKS.clear()
    PULL_REQUEST_CHECKS.update(module.PULL_REQUEST_CHECKS_ORIG)


@pytest.mark.parametrize("is_member", [True, False])
def test_close_if_not_in_org(is_member):
    pr_handler = MagicMock()
    pr_handler.user = "contributor"

    repo_handler = MagicMock()
    repo_handler.org_handler.is_member.return_value = is_member

    close_if_not_in_org(pr_handler, repo_handler)

    repo_handler.org_handler.is_member.assert_called_once_with("contributor")
    assert pr_handler.submit_comment.called is not is_member
    assert pr_handler.close.called is not is_member


def make_handlers(previous_prs, counts=(0, 0, 0, 0), config=None):
    pr_handler = MagicMock()
    pr_handler.user = "contributor"
    pr_handler.number = 42
    pr_handler.get_config_value.return_value = {"enabled": True, **(config or {})}

    repo_handler = MagicMock()
    repo_handler.org_handler.is_member.return_value = False
    repo_handler.get_pull_requests_by.return_value = previous_prs
    repo_handler.count_opened_by.side_effect = list(counts)

    return pr_handler, repo_handler


def test_close_message_lists_previous_pull_requests():
    pr_handler, repo_handler = make_handlers([3, 17, 42])

    close_if_not_in_org(pr_handler, repo_handler)

    repo_handler.get_pull_requests_by.assert_called_once_with("contributor")

    message = pr_handler.submit_comment.call_args[0][0]
    assert (
        "This user has made other pull requests to this repository prior to this one, here is a full list:\n\n* #3\n* #17\n"
        in message
    )
    assert "* #42" not in message


def test_close_message_without_previous_pull_requests():
    pr_handler, repo_handler = make_handlers([42])

    close_if_not_in_org(pr_handler, repo_handler)

    message = pr_handler.submit_comment.call_args[0][0]
    assert "This user has not made any other pull requests to this repository prior to this one." in message
    assert "here is a full list" not in message


def test_close_message_activity_table():
    # count_opened_by is called for (pr, day), (issue, day), (pr, week), (issue, week)
    pr_handler, repo_handler = make_handlers([42], counts=(3, 1, 12, 5))

    close_if_not_in_org(pr_handler, repo_handler)

    kinds = [call.args[1] for call in repo_handler.count_opened_by.call_args_list]
    assert kinds == ["pr", "issue", "pr", "issue"]
    for call in repo_handler.count_opened_by.call_args_list:
        assert call.args[0] == "contributor"
    day_since, week_since = (
        repo_handler.count_opened_by.call_args_list[0].args[2],
        repo_handler.count_opened_by.call_args_list[2].args[2],
    )
    assert (week_since - day_since).days == -6

    message = pr_handler.submit_comment.call_args[0][0]
    assert "| Pull requests opened | 3 | 12 |" in message
    assert "| Issues opened        | 1 | 5 |" in message
    assert message.startswith("This pull request has been closed automatically")
    assert pr_handler.close.called


def test_close_message_from_config():
    pr_handler, repo_handler = make_handlers([42], config={"message": "Hi {there}! Please join our Slack.\n"})

    close_if_not_in_org(pr_handler, repo_handler)

    message = pr_handler.submit_comment.call_args[0][0]
    assert message.startswith("Hi {there}! Please join our Slack.\n\n### Notes for maintainers\n")
    assert "closed automatically" not in message


ALLOWLIST = """
# Contributors vetted by hand
@Alice
bob

carol  # trailing comments are not supported, so this line is a different name
"""


class TestAllowlist:
    def setup_method(self, method):
        ALLOWLIST_CACHE.clear()

    def make_response(self, text=ALLOWLIST, error=None):
        response = MagicMock()
        response.text = text
        if error:
            response.raise_for_status.side_effect = error
        return response

    @pytest.mark.parametrize(
        ("user", "closed"), [("alice", False), ("ALICE", False), ("bob", False), ("carol", True), ("contributor", True)]
    )
    def test_allowlisted_users_are_not_closed(self, user, closed):
        pr_handler, repo_handler = make_handlers([42], config={"allowlist": "https://example.org/allowlist.txt"})
        pr_handler.user = user

        with patch("baldrick.plugins.github_org_vetting.requests.get") as mock_get:
            mock_get.return_value = self.make_response()
            close_if_not_in_org(pr_handler, repo_handler)

        mock_get.assert_called_once_with("https://example.org/allowlist.txt", timeout=30)
        assert pr_handler.close.called is closed

    def test_allowlist_is_cached(self):
        pr_handler, repo_handler = make_handlers([42], config={"allowlist": "https://example.org/allowlist.txt"})
        pr_handler.user = "alice"

        with patch("baldrick.plugins.github_org_vetting.requests.get") as mock_get:
            mock_get.return_value = self.make_response()
            close_if_not_in_org(pr_handler, repo_handler)
            close_if_not_in_org(pr_handler, repo_handler)

        assert mock_get.call_count == 1
        assert not pr_handler.close.called

    def test_unreachable_allowlist_is_treated_as_empty(self):
        pr_handler, repo_handler = make_handlers([42], config={"allowlist": "https://example.org/allowlist.txt"})
        pr_handler.user = "alice"

        with patch("baldrick.plugins.github_org_vetting.requests.get") as mock_get:
            mock_get.return_value = self.make_response(error=requests.HTTPError("404"))
            close_if_not_in_org(pr_handler, repo_handler)
            assert pr_handler.close.called

            # Failures are not cached, so the allowlist is tried again
            assert load_allowlist("https://example.org/allowlist.txt") == set()
            assert mock_get.call_count == 2

    def test_no_allowlist_configured(self):
        pr_handler, repo_handler = make_handlers([42])
        pr_handler.user = "alice"

        with patch("baldrick.plugins.github_org_vetting.requests.get") as mock_get:
            close_if_not_in_org(pr_handler, repo_handler)

        assert not mock_get.called
        assert pr_handler.close.called
