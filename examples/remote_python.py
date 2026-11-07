"""python examples/remote_python.py API_URL SITE_URL SITEKEY"""
import os
import sys

from ruyi_captcha import ApiClient

client = ApiClient(sys.argv[1], api_key=os.getenv("RUYI_CAPTCHA_API_KEY"))
print(client.solve_turnstile(sys.argv[2], sys.argv[3], timeout=30, attempts=2))
