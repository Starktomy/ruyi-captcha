"""Manual live PerimeterX test with explicit proxy support.

The report stores cookie names, lengths and timings, never the cookie value or
proxy credentials. Run with a residential proxy for the Walmart challenge:

    PX_PROXY='socks5h://user:pass@proxy.example:7878' \
      .venv/bin/python tests/live_perimeterx.py \
      --url https://www.walmart.com/blocked --press-ms 6000
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path

from ruyi_captcha import ApiClient, solve_perimeterx_details
from ruyi_captcha.runtime import check_runtime


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="https://www.walmart.com/blocked")
    parser.add_argument("--proxy", default=os.getenv("PX_PROXY"))
    parser.add_argument("--proxy-file", type=Path)
    parser.add_argument("--proxy-index", type=int, default=1)
    parser.add_argument("--press-ms", type=int, default=6000)
    parser.add_argument("--timeout", type=float, default=120)
    parser.add_argument("--attempts", type=int, default=1)
    parser.add_argument("--api-url")
    parser.add_argument("--output", type=Path, default=Path("artifacts/live-perimeterx.json"))
    args = parser.parse_args()
    proxy = args.proxy
    if args.proxy_file:
        proxies = [line.strip() for line in args.proxy_file.read_text().splitlines()
                   if line.strip()]
        if not 1 <= args.proxy_index <= len(proxies):
            parser.error("--proxy-index is outside the proxy file")
        proxy = proxies[args.proxy_index - 1]

    report = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "site_url": args.url,
        "proxy_used": bool(proxy),
        "press_ms": args.press_ms,
        "runtime": check_runtime().to_dict(),
        "runs": [],
    }

    def save(row):
        report["runs"].append(row)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps(row), flush=True)

    solution = solve_perimeterx_details(
        args.url, proxy=proxy, press_ms=args.press_ms, timeout=args.timeout,
        attempts=args.attempts)
    save({
        "mode": "python_sdk",
        "token_length": len(solution.token),
        "token_sha256": hashlib.sha256(solution.token.encode()).hexdigest(),
        "cookie_names": sorted(solution.cookies),
        "verified": solution.verified,
        "elapsed": round(solution.elapsed, 3),
        "attempts": solution.attempts,
    })

    if args.api_url:
        client = ApiClient(args.api_url, os.getenv("RUYI_CAPTCHA_API_KEY"))
        result = client.solve_perimeterx_details(
            args.url, proxy=proxy, press_ms=args.press_ms, timeout=args.timeout,
            attempts=args.attempts, wait_timeout=args.timeout + 60)
        token = result["token"]
        save({
            "mode": "http_async_client",
            "token_length": len(token),
            "token_sha256": hashlib.sha256(token.encode()).hexdigest(),
            "cookie_names": sorted(result.get("cookies", {})),
            "verified": result.get("verified"),
            "elapsed": round(result.get("elapsed", 0), 3),
            "attempts": result.get("attempts"),
        })
    return 0 if all(row.get("verified") for row in report["runs"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
