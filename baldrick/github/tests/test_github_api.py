from datetime import UTC, datetime
from unittest.mock import MagicMock, Mock, PropertyMock, patch

import pytest

from baldrick.config import loads
from baldrick.github.github_api import FILE_CACHE, ORG_CONFIG_CACHE, IssueHandler, PullRequestHandler, RepoHandler

# TODO: Add more tests to increase coverage.


class TestRepoHandler:
    def setup_class(self):
        self.repo = RepoHandler("fakerepo/doesnotexist")

    @patch("requests.get")
    def test_get_issues(self, mock_get):
        # http://engineroom.trackmaven.com/blog/real-life-mocking/
        mock_response = Mock()
        mock_response.json.return_value = [
            {"number": 42, "state": "open"},
            {"number": 55, "state": "open", "pull_request": {"diff_url": "blah"}},
        ]
        mock_get.return_value = mock_response

        assert self.repo.get_issues("open", "Close?") == [42]
        assert self.repo.get_issues("open", "Close?", exclude_pr=False) == [42, 55]

    @patch("requests.get")
    def test_get_pull_requests_by(self, mock_get):
        mock_response = Mock()
        mock_response.json.return_value = {"items": [{"number": 3}, {"number": 17}]}
        mock_get.return_value = mock_response

        assert self.repo.get_pull_requests_by("contributor") == [3, 17]

        args = mock_get.call_args[0]
        assert args[1]["q"] == "repo:fakerepo/doesnotexist type:pr author:contributor"
        assert args[1]["order"] == "asc"

    @patch("requests.get")
    def test_count_opened_by(self, mock_get):
        mock_response = Mock()
        mock_response.json.return_value = {"total_count": 7, "items": [{"number": 3}]}
        mock_get.return_value = mock_response

        since = datetime(2026, 10, 4, 12, 30, 0, tzinfo=UTC)
        assert self.repo.count_opened_by("contributor", "issue", since) == 7

        args = mock_get.call_args[0]
        assert args[0] == "https://api.github.com/search/issues"
        assert args[1]["q"] == "author:contributor type:issue created:>=2026-10-04T12:30:00Z"
        assert args[1]["per_page"] == 1

    @patch("requests.get")
    def test_get_all_labels(self, mock_get):
        mock_response = Mock()
        mock_response.json.return_value = [{"name": "io.fits"}, {"name": "Documentation"}]
        mock_response.headers = {}
        mock_get.return_value = mock_response

        assert self.repo.get_all_labels() == ["io.fits", "Documentation"]

    def test_urls(self):
        assert self.repo._url_contents == "https://api.github.com/repos/fakerepo/doesnotexist/contents/"
        assert self.repo._url_pull_requests == "https://api.github.com/repos/fakerepo/doesnotexist/pulls"
        assert self.repo._headers == {}


TEST_CONFIG = """
[tool.testbot]
[tool.testbot.pr]
setting1 = 2
setting2 = 3
"""


TEST_GLOBAL_CONFIG = """
[tool.testbot]
[tool.testbot.pr]
setting1 = 1
setting2 = 5
setting3 = 6
[tool.testbot.other]
setting4 = 5
"""


TEST_FALLBACK_CONFIG = """
[tool.nottestbot]
[tool.nottestbot.pr]
setting1 = 5
setting3 = 4
"""


TEST_ORG_CONFIG = """
[tool.testbot]
[tool.testbot.pr]
setting1 = 10
setting3 = 30
[tool.testbot.org_vetting]
enabled = true
"""


TEST_REPO_OPT_OUT_CONFIG = """
[tool.testbot]
[tool.testbot.pr]
setting1 = 2
setting2 = 3
[tool.testbot.org_vetting]
enabled = false
"""


class TestOrgConfig:
    """
    Configuration from the owner's .github repository is layered between the
    app configuration and the repository's own configuration.
    """

    def setup_method(self, method):
        FILE_CACHE.clear()
        ORG_CONFIG_CACHE.clear()
        self.repo = RepoHandler("fakeorg/fakerepo")

    def fake_file_contents(self, files):
        """
        Return a get_file_contents replacement that serves files per repository
        and raises the error get_file_contents would raise otherwise.
        """

        def get_file_contents(handler, path_to_file, branch=None):
            result = files.get(handler.repo)
            if isinstance(result, Exception):
                raise result
            if result is None:
                raise FileNotFoundError(path_to_file)
            return result

        return patch.object(RepoHandler, "get_file_contents", autospec=True, side_effect=get_file_contents)

    def test_org_config_used_when_repo_has_none(self, app):
        with app.app_context():
            with self.fake_file_contents({"fakeorg/.github": TEST_ORG_CONFIG}):
                assert self.repo.get_config_value("org_vetting", branch="main") == {"enabled": True}
                assert self.repo.get_config_value("pr", branch="main") == {"setting1": 10, "setting3": 30}

    def test_repo_config_overrides_org_config_per_setting(self, app):
        with app.app_context():
            files = {"fakeorg/.github": TEST_ORG_CONFIG, "fakeorg/fakerepo": TEST_REPO_OPT_OUT_CONFIG}
            with self.fake_file_contents(files):
                assert self.repo.get_config_value("org_vetting", branch="main") == {"enabled": False}
                assert self.repo.get_config_value("pr", branch="main") == {"setting1": 2, "setting2": 3, "setting3": 30}

    def test_org_config_overrides_app_config(self, app):
        with app.app_context():
            app.conf = loads(TEST_GLOBAL_CONFIG, tool="testbot")
            with self.fake_file_contents({"fakeorg/.github": TEST_ORG_CONFIG}):
                assert self.repo.get_config_value("pr", branch="main") == {
                    "setting1": 10,
                    "setting2": 5,
                    "setting3": 30,
                }
                assert self.repo.get_config_value("other", branch="main") == {"setting4": 5}

    @pytest.mark.parametrize(
        "org_result", [None, ValueError("Unable to fetch repo information")], ids=["no-file", "no-repo"]
    )
    def test_missing_org_config_is_ignored(self, app, org_result):
        with app.app_context():
            with self.fake_file_contents({"fakeorg/.github": org_result, "fakeorg/fakerepo": TEST_CONFIG}):
                assert self.repo.get_org_config() == {}
                assert self.repo.get_config_value("pr", branch="main") == {"setting1": 2, "setting2": 3}
                assert self.repo.get_config_value("org_vetting", branch="main") is None

    def test_org_config_is_cached(self, app):
        with app.app_context():
            with self.fake_file_contents({"fakeorg/.github": TEST_ORG_CONFIG}) as mock_get:
                first = self.repo.get_org_config()
                second = RepoHandler("fakeorg/otherrepo").get_org_config()
                assert first == second
                assert mock_get.call_count == 1

                # Modifying the returned config must not modify the cached copy
                first["org_vetting"]["enabled"] = False
                assert self.repo.get_org_config()["org_vetting"]["enabled"] is True

    def test_missing_org_config_is_cached(self, app):
        with app.app_context():
            with self.fake_file_contents({}) as mock_get:
                assert self.repo.get_org_config() == {}
                assert self.repo.get_org_config() == {}
                assert mock_get.call_count == 1

    def test_org_config_read_from_owner_dot_github_repo(self, app):
        with app.app_context():
            with self.fake_file_contents({"fakeorg/.github": TEST_ORG_CONFIG}) as mock_get:
                self.repo.get_org_config()
                handler, path = mock_get.call_args[0]
                assert handler.repo == "fakeorg/.github"
                assert path == "pyproject.toml"


@pytest.mark.github_api
class TestRealRepoHandler:
    def setup_method(self, method):
        FILE_CACHE.clear()
        ORG_CONFIG_CACHE.clear()

    def setup_class(self):
        self.repo = RepoHandler("OpenAstronomy/baldrick")

    def test_get_config(self, app):

        with app.app_context():
            with patch.object(self.repo, "get_file_contents") as mock_get:
                mock_get.return_value = TEST_CONFIG

                # These are set to False in YAML; defaults must not be used.
                assert self.repo.get_config_value("pr")["setting1"] == 2
                assert self.repo.get_config_value("pr")["setting2"] == 3

    def test_get_fallback_config(self, app):

        with app.app_context():
            app.fall_back_config = "nottestbot"
            with patch.object(self.repo, "get_file_contents") as mock_get:
                mock_get.return_value = TEST_FALLBACK_CONFIG

                # These are set to False in YAML; defaults must not be used.
                assert self.repo.get_config_value("pr")["setting1"] == 5
                assert self.repo.get_config_value("pr")["setting3"] == 4

    def test_get_fallback_with_primary_config(self, app):

        with app.app_context():
            app.fall_back_config = "nottestbot"
            with patch.object(self.repo, "get_file_contents") as mock_get:
                mock_get.return_value = TEST_CONFIG + TEST_FALLBACK_CONFIG

                # These are set to False in YAML; defaults must not be used.
                assert self.repo.get_config_value("pr")["setting1"] == 2
                assert self.repo.get_config_value("pr")["setting2"] == 3
                assert self.repo.get_config_value("pr")["setting3"] == 4

    def test_get_config_with_app_defaults(self, app):

        with app.app_context():
            with patch.object(self.repo, "get_file_contents") as mock_get:
                mock_get.return_value = TEST_CONFIG

                # These are set to False in YAML; defaults must not be used.
                assert self.repo.get_config_value("pr") == {"setting1": 2, "setting2": 3}
                assert self.repo.get_config_value("other") is None

                app.conf = loads(TEST_GLOBAL_CONFIG, tool="testbot")

                assert self.repo.get_config_value("pr") == {"setting1": 2, "setting2": 3, "setting3": 6}
                assert self.repo.get_config_value("other") == {"setting4": 5}

    def test_get_file_contents(self):
        result = self.repo.get_file_contents("README.rst", branch="main")
        assert "Baldrick" in result
        assert "cunning plan" in result

    def test_missing_file_contents(self):
        with pytest.raises(FileNotFoundError):
            self.repo.get_file_contents("this/file/does/not/exist.txt", branch="main")


class TestIssueHandler:
    def setup_class(self):
        self.issue = IssueHandler("fakerepo/doesnotexist", 1234)

    def test_urls(self):
        assert self.issue._url_issue == "https://api.github.com/repos/fakerepo/doesnotexist/issues/1234"
        assert self.issue._url_issue_nonapi == "https://github.com/fakerepo/doesnotexist/issues/1234"
        assert self.issue._url_labels == "https://api.github.com/repos/fakerepo/doesnotexist/issues/1234/labels"
        assert (
            self.issue._url_issue_comment == "https://api.github.com/repos/fakerepo/doesnotexist/issues/1234/comments"
        )
        assert self.issue._url_timeline == "https://api.github.com/repos/fakerepo/doesnotexist/issues/1234/timeline"

    @pytest.mark.parametrize(("state", "answer"), [("open", False), ("closed", True)])
    def test_is_closed(self, state, answer):
        with patch("baldrick.github.github_api.IssueHandler.json", new_callable=PropertyMock) as mock_json:
            mock_json.return_value = {"state": state}
            assert self.issue.is_closed is answer

    def test_missing_labels(self):
        with patch("baldrick.github.github_api.IssueHandler.labels", new_callable=PropertyMock) as mock_issue_labels:
            mock_issue_labels.return_value = ["io.fits"]
            with patch("baldrick.github.github_api.RepoHandler.get_all_labels") as mock_repo_labels:
                mock_repo_labels.return_value = ["io.fits", "closed-by-bot"]

                # closed-by-bot label will be added to issue in POST
                missing_labels = self.issue._get_missing_labels("closed-by-bot")
                assert missing_labels == ["closed-by-bot"]

                # Desired labels do not exist in repo
                missing_labels = self.issue._get_missing_labels(["dummy", "foo"])
                assert missing_labels is None

                # Desired label already set on issue
                missing_labels = self.issue._get_missing_labels(["io.fits"])
                assert missing_labels is None

                # A mix
                missing_labels = self.issue._get_missing_labels(["io.fits", "closed-by-bot", "foo"])
                assert missing_labels == ["closed-by-bot"]


class TestPullRequestHandler:
    def setup_class(self):
        self.pr = PullRequestHandler("fakerepo/doesnotexist", 1234)

    def test_urls(self):
        assert self.pr._url_pull_request == "https://api.github.com/repos/fakerepo/doesnotexist/pulls/1234"
        assert self.pr._url_review_comment == "https://api.github.com/repos/fakerepo/doesnotexist/pulls/1234/reviews"
        assert self.pr._url_commits == "https://api.github.com/repos/fakerepo/doesnotexist/pulls/1234/commits"
        assert self.pr._url_files == "https://api.github.com/repos/fakerepo/doesnotexist/pulls/1234/files"

    def test_has_modified(self):
        mock = MagicMock(
            return_value=[
                {
                    "sha": "bbcd538c8e72b8c175046e27cc8f907076331401",
                    "filename": "file1.txt",
                    "status": "added",
                    "additions": 103,
                    "deletions": 21,
                    "changes": 124,
                    "blob_url": "https://github.com/blah/blah/blob/hash/file1.txt",
                    "raw_url": "https://github.com/blaht/blah/raw/hash/file1.txt",
                    "contents_url": "https://api.github.com/repos/blah/blah/contents/file1.txt?ref=hash",
                    "patch": "@@ -132,7 +132,7 @@ module Test @@ -1000,7 +1000,7 @@ module Test",
                }
            ]
        )
        with patch("baldrick.github.github_api.paged_github_json_request", mock):
            assert self.pr.has_modified(["file1.txt"])
            assert self.pr.has_modified(["file1.txt", "notthis.txt"])
            assert not self.pr.has_modified(["notthis.txt"])

    def test_set_check(self, app):
        with patch("baldrick.github.github_api.PullRequestHandler.json", new_callable=PropertyMock) as json:
            json.return_value = {"head": {"sha": 987654321}, "base": {"sha": 123456789}}
            with patch("requests.post") as post:
                self.pr.set_check("baldrick-1", "hello", name="test")
                expected_json = {
                    "external_id": "baldrick-1",
                    "name": "test",
                    "head_sha": 987654321,
                    "status": "completed",
                    "output": {"title": "hello", "summary": ""},
                    "conclusion": "neutral",
                }
                post.assert_called_once_with(
                    "https://api.github.com/repos/fakerepo/doesnotexist/check-runs",
                    headers={"Accept": "application/vnd.github.antiope-preview+json"},
                    json=expected_json,
                )

                post.reset_mock()

                self.pr.set_check(
                    "baldrick-1", "hello", name="test", commit_hash="base", text="hello world", summary="why hello"
                )
                expected_json = {
                    "external_id": "baldrick-1",
                    "name": "test",
                    "head_sha": 123456789,
                    "status": "completed",
                    "output": {"title": "hello", "summary": "why hello", "text": "hello world"},
                    "conclusion": "neutral",
                }
                post.assert_called_once_with(
                    "https://api.github.com/repos/fakerepo/doesnotexist/check-runs",
                    headers={"Accept": "application/vnd.github.antiope-preview+json"},
                    json=expected_json,
                )

                post.reset_mock()

                self.pr.set_check("baldrick-1", "hello", name="test", commit_hash="hello", details_url="this_is_a_url")
                expected_json = {
                    "external_id": "baldrick-1",
                    "name": "test",
                    "head_sha": "hello",
                    "details_url": "this_is_a_url",
                    "status": "completed",
                    "output": {"title": "hello", "summary": ""},
                    "conclusion": "neutral",
                }
                post.assert_called_once_with(
                    "https://api.github.com/repos/fakerepo/doesnotexist/check-runs",
                    headers={"Accept": "application/vnd.github.antiope-preview+json"},
                    json=expected_json,
                )

                post.reset_mock()

                self.pr.set_check("baldrick-1", "hello", name="test", status="completed", conclusion=None)
                expected_json = {
                    "external_id": "baldrick-1",
                    "name": "test",
                    "head_sha": 987654321,
                    "status": "completed",
                    "output": {"title": "hello", "summary": ""},
                    "conclusion": "neutral",
                }
                post.assert_called_once_with(
                    "https://api.github.com/repos/fakerepo/doesnotexist/check-runs",
                    headers={"Accept": "application/vnd.github.antiope-preview+json"},
                    json=expected_json,
                )
