from loguru import logger
from baldrick.plugins.github_pull_requests import pull_request_handler


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

    pr_handler.submit_comment("Your PR has been closed. But fear not, there is a way out!")
    pr_handler.close()
