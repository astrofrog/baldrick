from datetime import UTC, datetime, timedelta

from loguru import logger

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


def previous_pull_requests_notes(pr_handler, repo_handler):
    """
    Describe the other pull requests the author has opened on this repository.
    """
    previous_prs = [n for n in repo_handler.get_pull_requests_by(pr_handler.user) if n != int(pr_handler.number)]
    if not previous_prs:
        return "This user has not made any other pull requests to this repository prior to this one."
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


@pull_request_handler(actions=["opened"])
def close_if_not_in_org(pr_handler, repo_handler):

    # When a PR is first opened, we check if the contributor is in the
    # organization, and if not, we close the pull request and post a friendly
    # message encouraging contributors to re-open

    vet_config = pr_handler.get_config_value("org_vetting", {})
    if not vet_config.get("enabled", False):
        logger.debug("Skipping org vetting plugin as disabled in config")
        return None

    logger.debug(f"Checking if {pr_handler.user} is a member of org")

    if repo_handler.org_handler.is_member(pr_handler.user):
        logger.debug(f"Yes they are")
        return

    logger.debug(f"No they are not, posting comment")

    # The contributor-facing text comes from the configuration (with a generic
    # fallback) and is not passed through str.format, so that it can contain
    # braces; the maintainer notes are always appended to it.
    notes = MAINTAINER_NOTES.format(
        previous_prs=previous_pull_requests_notes(pr_handler, repo_handler),
        **activity_counts(pr_handler, repo_handler),
    )
    message = vet_config.get("message", DEFAULT_MESSAGE).strip() + "\n\n" + notes

    pr_handler.submit_comment(message)
    pr_handler.close()
