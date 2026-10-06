# For these tests, we patch the repo and pull request handler directly rather
# than the requests to the server, as we assume the repo and pull request
# handlers are tested inside baldrick.

from copy import copy
from unittest.mock import MagicMock, patch

import pytest

from baldrick.plugins.github_org_vetting import (
    allowlist_location,
    close_if_not_in_org,
    parse_allowlist,
    update_vetting_status,
)
from baldrick.plugins.github_pull_requests import PULL_REQUEST_CHECKS

ALLOWLIST = """
# Contributors vetted by hand
@Alice
bob

carol  # trailing comments are not supported, so this line is a different name
"""


def setup_module(module):
    # Importing the plugin registers the handlers in the global
    # PULL_REQUEST_CHECKS registry, so remove them here to stop them leaking
    # into other test modules.
    module.PULL_REQUEST_CHECKS_ORIG = copy(PULL_REQUEST_CHECKS)
    for handler in (close_if_not_in_org, update_vetting_status):
        module.PULL_REQUEST_CHECKS_ORIG.pop(handler, None)
        PULL_REQUEST_CHECKS.pop(handler, None)


def teardown_module(module):
    PULL_REQUEST_CHECKS.clear()
    PULL_REQUEST_CHECKS.update(module.PULL_REQUEST_CHECKS_ORIG)


def make_handlers(previous_prs=(42,), counts=(0, 0, 0, 0), config=None, is_member=False, reopened_by=None):
    pr_handler = MagicMock()
    pr_handler.user = "contributor"
    pr_handler.number = 42
    pr_handler.last_reopened_by = reopened_by
    pr_handler.get_config_value.return_value = {"enabled": True, **(config or {})}

    repo_handler = MagicMock()
    repo_handler.repo = "fakeorg/fakerepo"
    repo_handler.installation = 1234
    repo_handler.org_handler.is_member.return_value = is_member
    repo_handler.get_pull_requests_by.return_value = list(previous_prs)
    repo_handler.count_opened_by.side_effect = list(counts)

    return pr_handler, repo_handler


def patch_allowlist_repo(contents=ALLOWLIST, error=None):
    """
    Patch the RepoHandler used to read the allowlist file, returning the
    mocked class so that tests can check which repository and file were read.
    """
    allowlist_repo = MagicMock()
    if error:
        allowlist_repo.get_file_contents.side_effect = error
    else:
        allowlist_repo.get_file_contents.return_value = contents
    return patch("baldrick.plugins.github_org_vetting.RepoHandler", return_value=allowlist_repo)


def assert_running_check_posted(pr_handler):
    pr_handler.set_check.assert_called_once()
    assert pr_handler.set_check.call_args.args == ("org_vetting",)
    assert pr_handler.set_check.call_args.kwargs["status"] == "in_progress"
    assert pr_handler.set_check.call_args.kwargs["conclusion"] is None


def test_disabled():
    pr_handler, repo_handler = make_handlers()
    pr_handler.get_config_value.return_value = {}

    assert close_if_not_in_org(pr_handler, repo_handler) is None

    assert not pr_handler.set_check.called
    assert not pr_handler.close.called


def test_member_passes():
    pr_handler, repo_handler = make_handlers(is_member=True)

    result = close_if_not_in_org(pr_handler, repo_handler)

    repo_handler.org_handler.is_member.assert_called_once_with("contributor")
    assert_running_check_posted(pr_handler)
    assert result == {"org_vetting": {"conclusion": "success", "title": "Author is a member of the organization"}}
    assert not pr_handler.submit_comment.called
    assert not pr_handler.close.called


def test_non_member_is_closed_when_opened():
    pr_handler, repo_handler = make_handlers()

    result = close_if_not_in_org(pr_handler, repo_handler)

    assert_running_check_posted(pr_handler)
    assert result["org_vetting"]["conclusion"] == "failure"
    assert result["org_vetting"]["title"] == "Author is not a member of the organization or on the allowlist"
    assert pr_handler.submit_comment.called
    assert pr_handler.close.called


def test_non_member_is_not_closed_on_update():
    pr_handler, repo_handler = make_handlers()

    result = update_vetting_status(pr_handler, repo_handler)

    assert_running_check_posted(pr_handler)
    assert result["org_vetting"]["conclusion"] == "failure"
    assert not pr_handler.submit_comment.called
    assert not pr_handler.close.called


@pytest.mark.parametrize(("reopened_by", "conclusion"), [("maintainer", "success"), (None, "failure")])
def test_reopened_pull_request_passes(reopened_by, conclusion):
    pr_handler, repo_handler = make_handlers(reopened_by=reopened_by)

    result = update_vetting_status(pr_handler, repo_handler)

    assert result["org_vetting"]["conclusion"] == conclusion
    if conclusion == "success":
        assert result["org_vetting"]["title"] == "Re-opened by @maintainer"
    assert not pr_handler.close.called


def test_reopen_override_does_not_apply_when_opened():
    pr_handler, repo_handler = make_handlers(reopened_by="maintainer")

    result = close_if_not_in_org(pr_handler, repo_handler)

    assert result["org_vetting"]["conclusion"] == "failure"
    assert pr_handler.close.called


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
    assert "This user has not made any pull requests to this repository prior to this one." in message
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


def test_close_message_without_maintainer_notes():
    pr_handler, repo_handler = make_handlers(
        [3, 42], config={"message": "Please join our Slack.", "maintainer_notes": False}
    )

    close_if_not_in_org(pr_handler, repo_handler)

    assert pr_handler.submit_comment.call_args[0][0] == "Please join our Slack."
    assert not repo_handler.get_pull_requests_by.called
    assert not repo_handler.count_opened_by.called
    assert pr_handler.close.called


def test_close_message_from_config():
    pr_handler, repo_handler = make_handlers([42], config={"message": "Hi {there}! Please join our Slack.\n"})

    close_if_not_in_org(pr_handler, repo_handler)

    message = pr_handler.submit_comment.call_args[0][0]
    assert message.startswith("Hi {there}! Please join our Slack.\n\n### Notes for maintainers\n")
    assert "closed automatically" not in message


class TestAllowlist:
    def test_parse_allowlist(self):
        assert parse_allowlist(ALLOWLIST) == {
            "alice",
            "bob",
            "carol  # trailing comments are not supported, so this line is a different name",
        }

    def test_location_defaults_to_dot_github_repo(self):
        _, repo_handler = make_handlers()
        assert allowlist_location(repo_handler, {}) is None
        assert allowlist_location(repo_handler, {"allowlist_file": "allow.txt"}) == ("fakeorg/.github", "allow.txt")
        assert allowlist_location(repo_handler, {"allowlist_file": "allow.txt", "allowlist_repo": "other/repo"}) == (
            "other/repo",
            "allow.txt",
        )

    @pytest.mark.parametrize(
        ("user", "closed"), [("alice", False), ("ALICE", False), ("bob", False), ("carol", True), ("contributor", True)]
    )
    def test_allowlisted_users_are_not_closed(self, user, closed):
        pr_handler, repo_handler = make_handlers(config={"allowlist_file": "allow.txt"})
        pr_handler.user = user

        with patch_allowlist_repo() as mock_repo_handler:
            result = close_if_not_in_org(pr_handler, repo_handler)

        mock_repo_handler.assert_called_once_with("fakeorg/.github", installation=1234)
        mock_repo_handler.return_value.get_file_contents.assert_called_once_with("allow.txt")
        assert pr_handler.close.called is closed
        assert result["org_vetting"]["conclusion"] == ("failure" if closed else "success")
        if not closed:
            assert result["org_vetting"]["title"] == "Author is on the allowlist"

    def test_allowlist_in_other_repository(self):
        pr_handler, repo_handler = make_handlers(config={"allowlist_file": "allow.txt", "allowlist_repo": "other/repo"})
        pr_handler.user = "alice"

        with patch_allowlist_repo() as mock_repo_handler:
            result = close_if_not_in_org(pr_handler, repo_handler)

        mock_repo_handler.assert_called_once_with("other/repo", installation=1234)
        assert result["org_vetting"]["conclusion"] == "success"

    def test_unreadable_allowlist_gives_neutral_check_and_leaves_pr_open(self):
        pr_handler, repo_handler = make_handlers(config={"allowlist_file": "allow.txt"})

        with patch_allowlist_repo(error=FileNotFoundError("allow.txt")):
            result = close_if_not_in_org(pr_handler, repo_handler)

        assert_running_check_posted(pr_handler)
        assert result["org_vetting"]["conclusion"] == "neutral"
        assert result["org_vetting"]["title"] == "Could not vet the author of this pull request"
        assert "FileNotFoundError: allow.txt" in result["org_vetting"]["summary"]
        assert not pr_handler.submit_comment.called
        assert not pr_handler.close.called

    def test_no_allowlist_configured(self):
        pr_handler, repo_handler = make_handlers()

        with patch_allowlist_repo() as mock_repo_handler:
            close_if_not_in_org(pr_handler, repo_handler)

        assert not mock_repo_handler.called
        assert pr_handler.close.called


def test_membership_error_gives_neutral_check_and_leaves_pr_open():
    pr_handler, repo_handler = make_handlers()
    repo_handler.org_handler.is_member.side_effect = Exception("GitHub is down")

    result = close_if_not_in_org(pr_handler, repo_handler)

    assert result["org_vetting"]["conclusion"] == "neutral"
    assert "Exception: GitHub is down" in result["org_vetting"]["summary"]
    assert not pr_handler.close.called
