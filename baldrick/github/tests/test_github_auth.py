from changebot.webapp import app
from unittest.mock import patch
from changebot.github.github_auth import get_json_web_token


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


@patch.object(app, 'private_key', PRIVATE_KEY)
@patch.object(app, 'integration_id', 'dummy id')
def test_get_json_web_token():

    # The first time we run this we should get a token
    token1 = get_json_web_token()

    # If we run it again immediately we should get the same token back
    token2 = get_json_web_token()

    assert token1 == token2
