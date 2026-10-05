import os

from baldrick import create_app

# Configure the App
app = create_app('<your-bot-name>')

# Import plugins
import baldrick.plugins.circleci_artifacts
import baldrick.plugins.github_milestones
import baldrick.plugins.github_pull_requests
import baldrick.plugins.github_pushes
import baldrick.plugins.github_towncrier_changelog
import baldrick.plugins.github_org_vetting

# Bind to PORT if defined, otherwise default to 5000.
port = int(os.environ.get('PORT', 5000))
app.run(host='0.0.0.0', port=port, debug=False)
