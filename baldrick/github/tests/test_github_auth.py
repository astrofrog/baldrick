import os

import pytest
from unittest.mock import patch, MagicMock

from baldrick import create_app
from baldrick.github.github_auth import (get_json_web_token, get_installation_token,
                                         github_request_headers, repo_to_installationid_mapping)


PRIVATE_KEY = """
-----BEGIN RSA PRIVATE KEY-----
MIIEogIBAAKCAQEA8QIt+M5C6ayAzQVSS4CfXr4VXJ47veAazH+YQ6ZfO6OQ/p3T
+8jrW43SLXS/DmOJzEMKAox9ar/4E2N4wh8uGI3qd2q68wkJk0EPRkFHqnFs5o8c
PjxOeVG2KlbtJu0VRoQ+uZSl2B+AZeQZdK9aWPXjCTZbPoIeMERJqZtZfmwZIosx
ExRtRPIftSpDzEdZZtvq8+DC+w5hudrlpQFqsUwUv/BjcQINAp+i5B8GuDzjYaJ0
2cm/j7moMYBaoBr0QCpAqBseE6mjXXbBmIlO70Wy2OGbqnBblrowIFAxh/28h/P9
4V0AVJrBT8gFoa1ODqLcL8C+ob0TRRuncU2q2QIDAQABAoIBADc+FqeHL9M8FTHp
XFmuG9mtnFvkcTEuozXosVAgXIfhECUsrCB0h24u7dQ5hGmZ60YEv9Chv0WuxwA6
tr1YREqgjPPeZQe8NJOqQAQMho7M/PdEKmchj6NDVYwS7L0VbuEBAxequPD3F4lD
ZYpXf1AQ3H+KFBQZ4y2RGYlk8HiHgMQ0h+faX6xO8cWtVgSb6C7/ibQPPTxMCB59
Fq2iJizJdY3kiWUmKpnl3yHoUasUi5WkgJqFg4RkVVN6MnCavhny6xvNgaSdKs+H
0NTus7lanC80xqXtXgsu53Oq1fN+4I2qX+e6Kv1CHPPCWcTgNy7/zw+vWhUlyKR0
tP8KSXkCgYEA+Txxheoi3OxDx1R/iD3SePFeIg0rW/zBFseVW6AFz/IUQwh4kQl/
pShVb/qRcj0YOmflIC/8S5vlJk9iC1ExFpypJXf3N33k2k1oyN6U40Mn73PkfCXn
59o2ecr2pgSauaO/x8XoP2M8dJS8VHOSaJaJ0lu8aQ6jvICUmVtDzSsCgYEA94yT
Rqz6TBmVDdOlLqgoG4tGfVhlAwAwYgvHjBYUHRmLjmEH+PU8RDNOtuMEtWG2oLCp
1LJsc3oWQkl3nAzfuyJyiNsgOqql4bFttJ14Esgx+4hrSHCYEBE6w1fvansXJBRQ
VmHntGA5Akt3+6GdILgoVz12uNEZTvVhTM3CjgsCgYBJOfgEp1jc3dHAI9RgfAF1
pTzJ9mKR4T396l+4jtiGUxKe60M5IbhOFv6bKtxG2ypeJp5MCa0vrbryuYoN1yn8
AcU0i/2nYSa2+N1bfwHxj46RLNSpoR10okk1GWvENUAcYL78++mTjh16ByUaDuaq
MeiGVIuTtkhnHsQKFqViBwKBgEgR3S7OXXCaYhLMc2LKAiNCwRrtCTt+apeg5k+a
ffCa505kYXXRr+ILLfeA0HYeJJVT2Z3a9EgKW0ChMvlzpg9NUBsX8KIj3HeAuHfF
AJg3QJYCeXl1jk/fNER67XEKtQoD//+mMVcKTI6meiAARUapVtVPR6k29y9NsS4z
GVlRAoGAV9jg9e94lqMNZqY2CY6LISnUPGOMSN0Z5xC+Q7C6lqWZOGRcxRv2WdO3
ZYQCTqpbe2XTdqx7a3jh5zq2hOlPv97Q9v09Jdmkcak8vb/5DfXo61fOGF+PCSHQ
IJVMoU0lvK0zKm5VlXh3jbRXt/M5cTNu/1+xZxUbGJ0b+Go3FYc=
-----END RSA PRIVATE KEY-----
""".strip()


def test_get_json_web_token():

    os.environ['GITHUB_APP_INTEGRATION_ID'] = '22223'
    os.environ['GITHUB_APP_PRIVATE_KEY'] = PRIVATE_KEY

    app = create_app('testbot')

    with app.app_context():

        # The first time we run this we should get a token
        token1 = get_json_web_token()

        # If we run it again immediately we should get the same token back
        token2 = get_json_web_token()

    assert token1 == token2


TOKEN_RESPONSE_VALID = {"token": "v1.1f699f1069f60xxx", "expires_at": "2016-07-11T22:14:10Z"}


def test_get_installation_token_valid():

    with patch('requests.post') as post:
        post.return_value.ok = True
        post.return_value.json.return_value = TOKEN_RESPONSE_VALID
        token = get_installation_token(12345)

    assert token == "v1.1f699f1069f60xxx"


TOKEN_RESPONSE_INVALID_WITH_MESSAGE = {"message": "This is the error message",
                                       "documentation_url": "https://developer.github.com/v3"}


def test_get_installation_token_invalid_with_message():

    with patch('requests.post') as post:
        post.return_value.ok = False
        post.return_value.json.return_value = TOKEN_RESPONSE_INVALID_WITH_MESSAGE
        with pytest.raises(Exception) as exc:
            get_installation_token(12345)
        assert exc.value.args[0] == TOKEN_RESPONSE_INVALID_WITH_MESSAGE['message']


TOKEN_RESPONSE_INVALID_WITHOUT_MESSAGE = {}


def test_get_installation_token_invalid_without_message():

    with patch('requests.post') as post:
        post.return_value.ok = False
        post.return_value.json.return_value = TOKEN_RESPONSE_INVALID_WITHOUT_MESSAGE
        with pytest.raises(Exception) as exc:
            get_installation_token(12345)
        assert exc.value.args[0] == "An error occurred when requesting token"


def test_github_request_headers():

    with patch('requests.post') as post:
        post.return_value.ok = True
        post.return_value.json.return_value = TOKEN_RESPONSE_VALID
        headers = github_request_headers(12345)

    assert headers['Authorization'] == 'token v1.1f699f1069f60xxx'


def requests_patch(url, headers=None):
    req = MagicMock()
    if url == 'https://api.github.com/app/installations':
        req.json.return_value = [{'id': 3331}]
    elif url == 'https://api.github.com/installation/repositories':
        req.json.return_value = {'repositories': [{'full_name': 'test1'},
                                                  {'full_name': 'test2'}]}
    return req


def test_repo_to_installationid_mapping(app):

    with app.app_context():
        with patch('requests.post') as post:
            post.return_value.ok = True
            post.return_value.json.return_value = TOKEN_RESPONSE_VALID
            with patch('requests.get', requests_patch):
                mapping = repo_to_installationid_mapping()

    assert mapping == {'test1': 3331, 'test2': 3331}
