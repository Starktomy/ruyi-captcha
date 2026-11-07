"""python examples/local_python.py SITE_URL SITEKEY"""
import sys

from ruyi_captcha import solve_turnstile

token = solve_turnstile(sys.argv[1], sys.argv[2], timeout=30, attempts=2)
# Submit this token in the existing business flow.
print(token)
