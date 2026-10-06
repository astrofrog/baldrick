from datetime import UTC, datetime, timedelta

from loguru import logger

from baldrick.github.github_api import RepoHandler
from baldrick.plugins.github_pull_requests import pull_request_handler

DEFAULT_MESSAGE = """\
This pull request has been closed automatically because the author is not a \
member of the organization. A maintainer can re-open it if appropriate.
"""

MAINTAINER_NOTES = """\
### Notes for maintainers

{previous_prs}

In addition, here are some statistics on the user's activity on GitHub:

|                      | Last 24 hours | Last 7 days |
| -------------------- | ------------: | ----------: |
| Pull requests opened | {pr_day} | {pr_week} |
| Issues opened        | {issue_day} | {issue_week} |
"""


def allowlist_location(repo_handler, vet_config):
    """
    The ``(repository, path)`` of the allowlist file, or `None` if no
    allowlist is configured. The repository defaults to the ``.github``
    repository of the owner of the repository being handled.
    """
    if "allowlist_file" not in vet_config:
        return None
    owner = repo_handler.repo.split("/")[0]
    return vet_config.get("allowlist_repo", f"{owner}/.github"), vet_config["allowlist_file"]


def parse_allowlist(contents):
    """
    The set of (lower-case) GitHub usernames in the allowlist, one per line,
    ignoring blank lines and lines starting with ``#``.
    """
    allowlist = set()
    for line in contents.splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            allowlist.add(line.lstrip("@").lower())
    return allowlist


def load_allowlist(repo_handler, vet_config):
    """
    The allowlist as a set of lower-case usernames, read from the default
    branch of the repository it lives in (and cached for a short time along
    with other files read from repositories). Raises if the file cannot be
    read, for example if the bot is not installed on that repository.
    """
    repo, path = allowlist_location(repo_handler, vet_config)
    contents = RepoHandler(repo, installation=repo_handler.installation).get_file_contents(path)
    return parse_allowlist(contents)


def previous_pull_requests_notes(pr_handler, repo_handler):
    """
    Describe the other pull requests the author has opened on this repository.
    """
    previous_prs = [n for n in repo_handler.get_pull_requests_by(pr_handler.user) if n != int(pr_handler.number)]
    if not previous_prs:
        return "This user has not made any pull requests to this repository prior to this one."
    pr_list = "\n".join(f"* #{n}" for n in previous_prs)
    return f"This user has made other pull requests to this repository prior to this one, here is a full list:\n\n{pr_list}"


def activity_counts(pr_handler, repo_handler):
    """
    Count the issues and pull requests the author has opened GitHub-wide over
    the last day and the last week.
    """
    now = datetime.now(UTC)
    counts = {}
    for period, delta in (("day", timedelta(days=1)), ("week", timedelta(days=7))):
        for kind in ("pr", "issue"):
            counts[f"{kind}_{period}"] = repo_handler.count_opened_by(pr_handler.user, kind, now - delta)
    return counts


CHECK_ID = "org_vetting"


def vetting_decision(pr_handler, repo_handler, vet_config, reopened_override):
    """
    Decide whether the pull request author passes vetting.

    Returns
    -------
    passed : bool
    reason : str
        A short explanation, used as the title of the status check.
    """
    user = pr_handler.user

    logger.debug(f"Checking if {user} is a member of org")
    if repo_handler.org_handler.is_member(user):
        logger.debug(f"Passing org-vetting as {user} is a member of the org.")
        return True, "Author is a member of the organization"

    if allowlist_location(repo_handler, vet_config) is not None:
        logger.debug(f"Checking if {user} is on the allowlist")
        if user.lower() in load_allowlist(repo_handler, vet_config):
            logger.debug(f"Passing org-vetting as {user} is on the allowlist.")
            return True, "Author is on the allowlist"

    if reopened_override:
        # Only users with write access can re-open a pull request closed by
        # someone else, so a re-open is an explicit override of the bot's
        # decision and there is no need to check who did it.
        reopened_by = pr_handler.last_reopened_by
        if reopened_by is not None:
            logger.debug(f"Passing org-vetting as the pull request was re-opened by {reopened_by}.")
            return True, f"Re-opened by @{reopened_by}"

    logger.debug(f"Failing org-vetting as {user} is not in the org or on the allowlist.")
    return False, "Author is not a member of the organization or on the allowlist"


def vet_pull_request(pr_handler, repo_handler, close):
    """
    Vet the pull request author and report the outcome as a status check:
    success if they pass, failure if not, and neutral if an error occurred
    while checking, for example because the allowlist could not be read (in
    which case the pull request is left open).

    Parameters
    ----------
    close : bool
        Whether to comment on and close the pull request if the author fails
        vetting (done when the pull request is first opened). Otherwise a
        pull request that has been re-opened passes.
    """
    vet_config = pr_handler.get_config_value("org_vetting", {})
    if not vet_config.get("enabled", False):
        logger.debug("Skipping org vetting plugin as disabled in config")
        return None

    # Show the check as running while the lookups below happen; the result
    # returned from this function completes it.
    pr_handler.set_check(
        CHECK_ID, title="Vetting the author of this pull request", status="in_progress", conclusion=None
    )

    try:
        passed, reason = vetting_decision(pr_handler, repo_handler, vet_config, reopened_override=not close)
    except Exception as exc:  # noqa: BLE001 - any failure to decide is reported on the pull request
        logger.exception(f"Could not vet the author of {pr_handler.repo}#{pr_handler.number}")
        return {
            CHECK_ID: {
                "conclusion": "neutral",
                "title": "Could not vet the author of this pull request",
                "summary": f"An error occurred while checking the author; the pull request has been left open.\n\n{type(exc).__name__}: {exc}",
            }
        }

    if passed:
        return {CHECK_ID: {"conclusion": "success", "title": reason}}

    if close:
        # The contributor-facing text comes from the configuration (with a
        # generic fallback) and is not passed through str.format, so that it
        # can contain braces; the maintainer notes are appended to it unless
        # disabled.
        message = vet_config.get("message", DEFAULT_MESSAGE).strip()
        if vet_config.get("maintainer_notes", True):
            notes = MAINTAINER_NOTES.format(
                previous_prs=previous_pull_requests_notes(pr_handler, repo_handler),
                **activity_counts(pr_handler, repo_handler),
            )
            message += "\n\n" + notes

        pr_handler.submit_comment(message)
        pr_handler.close()

    return {CHECK_ID: {"conclusion": "failure", "title": reason}}


@pull_request_handler(actions=["opened"])
def close_if_not_in_org(pr_handler, repo_handler):
    """
    When a pull request is first opened, close it with an explanatory comment
    if the author is neither an organization member nor on the allowlist.
    """
    return vet_pull_request(pr_handler, repo_handler, close=True)


@pull_request_handler(actions=["reopened", "synchronize"])
def update_vetting_status(pr_handler, repo_handler):
    """
    Re-post the vetting status when a pull request is re-opened or updated,
    so that it is present on the current head commit, without closing it. A
    pull request that has been re-opened passes.
    """
    return vet_pull_request(pr_handler, repo_handler, close=False)
