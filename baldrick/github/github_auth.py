import netrc
import datetime
from collections import defaultdict

import dateutil.parser

import jwt

import requests

TEN_MIN = datetime.timedelta(minutes=9)
ONE_MIN = datetime.timedelta(minutes=1)

# TODO: need to change global variable to use redis

json_web_token = None
json_web_token_expiry = None


def get_json_web_token():
    """
    Prepares the JSON Web Token (JWT) based on the private key.
    """

    global json_web_token
    global json_web_token_expiry

    from changebot.webapp import app

    now = datetime.datetime.now()

    # Include a one-minute buffer otherwise token might expire by the time we
    # make the request with the token.
    if json_web_token_expiry is None or now + ONE_MIN > json_web_token_expiry:

        json_web_token_expiry = now + TEN_MIN

        payload = {}

        # Issued at time
        payload['iat'] = int(now.timestamp())

        # JWT expiration time (10 minute maximum)
        payload['exp'] = int(json_web_token_expiry.timestamp())

        # Integration's GitHub identifier
        payload['iss'] = app.integration_id

        json_web_token = jwt.encode(payload,
                                    app.private_key.encode('ascii'),
                                    algorithm='RS256').decode('ascii')

    return json_web_token


installation_token = defaultdict(lambda: None)
installation_token_expiry = defaultdict(lambda: None)


def netrc_exists():
    try:
        my_netrc = netrc.netrc()
    except FileNotFoundError:
        return False
    else:
        return my_netrc.authenticators('api.github.com') is not None


def get_installation_token(installation):
    """
    Get access token for installation
    """

    now = datetime.datetime.now().timestamp()

    if installation_token_expiry[installation] is None or now + 60 > installation_token_expiry[installation]:

        # FIXME: if .netrc file is present, Authorization header will get
        # overwritten, so need to figure out how to ignore that file.
        if netrc_exists():
            raise Exception("Authentication does not work properly if a ~/.netrc "
                            "file exists. Rename that file temporarily and try again.")

        headers = {}
        headers['Authorization'] = 'Bearer {0}'.format(get_json_web_token())
        headers['Accept'] = 'application/vnd.github.machine-man-preview+json'

        url = 'https://api.github.com/installations/{0}/access_tokens'.format(installation)

        req = requests.post(url, headers=headers)
        resp = req.json()

        if not req.ok:
            if 'message' in resp:
                raise Exception(resp['message'])
            else:
                raise Exception("An error occurred when requesting token")

        installation_token[installation] = resp['token']
        installation_token_expiry[installation] = dateutil.parser.parse(resp['expires_at']).timestamp()

    return installation_token[installation]


def github_request_headers(installation):

    token = get_installation_token(installation)

    headers = {}
    headers['Authorization'] = 'token {0}'.format(token)
    headers['Accept'] = 'application/vnd.github.machine-man-preview+json'

    return headers
