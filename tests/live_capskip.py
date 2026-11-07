"""Manual live acceptance test; persist lengths and backend outcomes, never tokens."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
from time import monotonic
from urllib.parse import urlencode
import urllib.request

from ruyijs import Context
from ruyi_captcha import ApiClient, solve_turnstile_details
from ruyi_captcha.providers.turnstile import _linux_profile
from ruyi_captcha.runtime import check_runtime

URL = "https://capskip.com/captcha-demo/cloudflare-turnstile/"


def fetch_json(context, expression, timeout=30):
    context.eval("globalThis.__liveResult=null;" + expression)
    deadline = monotonic() + timeout
    while monotonic() < deadline:
        context.advance(100, max_tasks=400)
        result = context.eval("JSON.stringify(__liveResult)")
        if result != "null":
            return json.loads(result)
    raise TimeoutError("Native transport request timed out")


def config():
    with Context(timeout=30, tls="159", proxy=None) as context:
        context.configure_clock("system")
        context.configure_fingerprint(_linux_profile())
        context.load_page(URL, "<!doctype html>")
        body = fetch_json(context, "fetch(location.href).then(r=>r.text()).then(body=>__liveResult={body})")
    match = re.search(r"var capskipCaptcha = (\{.*?\});", body["body"])
    if not match:
        raise RuntimeError("CapSkip public widget configuration not found")
    return json.loads(match.group(1))


def verify(settings, token):
    with Context(timeout=20, tls="159", proxy=None) as context:
        context.configure_clock("system")
        context.configure_fingerprint(_linux_profile())
        context.load_page(URL, "<!doctype html>")
        context.set("request", {"url": settings["ajaxUrl"],
                    "body": urlencode({"action": "capskip_captcha_verify", "nonce": settings["nonce"],
                                       "provider": "turnstile", "token": token})})
        response = fetch_json(context, '''fetch(request.url,{method:'POST',credentials:'same-origin',
          headers:{'Content-Type':'application/x-www-form-urlencoded'},body:request.body})
          .then(async r=>__liveResult={status:r.status,body:await r.json()})
          .catch(e=>__liveResult={error:String(e)})''', timeout=20)
    body = response.get("body", {})
    data = body.get("data", {})
    result = data.get("result", {})
    accepted = (response.get("status") == 200 and body.get("success") is True
                and data.get("verified") is True and data.get("server") is True
                and result.get("success") in (True, "true") and result.get("hostname") == "capskip.com")
    return {"accepted": accepted, "http_status": response.get("status"),
            "hostname": result.get("hostname"), "verified": data.get("verified"),
            "server": data.get("server"), "success": result.get("success")}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-url")
    parser.add_argument("--output", type=Path, default=Path("artifacts/live-capskip.json"))
    args = parser.parse_args()
    settings = config()
    report = {"started_at": datetime.now(timezone.utc).isoformat(), "site_url": URL,
              "sitekey": settings["siteKey"], "runtime": check_runtime().to_dict(), "runs": []}

    def save(row, token):
        row["token_length"] = len(token)
        row["dummy_token"] = "DUMMY" in token
        row["backend"] = verify(settings, token)
        row["passed"] = len(token) >= 200 and not row["dummy_token"] and row["backend"]["accepted"]
        report["runs"].append(row)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps(row), flush=True)

    solution = solve_turnstile_details(surl=URL, sitekey=settings["siteKey"], timeout=30, attempts=1)
    save({"mode": "python_sdk", "token_elapsed": round(solution.elapsed, 3), "attempts": solution.attempts}, solution.token)

    if args.api_url:
        key = os.getenv("RUYI_CAPTCHA_API_KEY")
        client = ApiClient(args.api_url, key)
        payload = {"type": "turnstile", "site_url": URL, "sitekey": settings["siteKey"], "timeout": 30, "attempts": 1}
        headers = {"Content-Type": "application/json"}
        if key:
            headers["Authorization"] = "Bearer " + key
        request = urllib.request.Request(args.api_url + "/v1/solve?wait_timeout=30", method="POST",
                                         data=json.dumps(payload).encode(), headers=headers)
        with urllib.request.urlopen(request, timeout=40) as response:
            job = json.load(response)
        if job["status"] != "succeeded":
            raise RuntimeError("Synchronous HTTP task did not succeed: " + str(job.get("error")))
        result = job["result"]
        save({"mode": "http_sync", "token_elapsed": round(result["elapsed"], 3),
              "attempts": result["attempts"]}, result["token"])
        result = client.solve_turnstile_details(surl=URL, sitekey=settings["siteKey"], timeout=30,
                                                attempts=1, wait_timeout=45)
        save({"mode": "http_async_client", "token_elapsed": round(result["elapsed"], 3),
              "attempts": result["attempts"]}, result["token"])
    return 0 if all(row["passed"] for row in report["runs"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
