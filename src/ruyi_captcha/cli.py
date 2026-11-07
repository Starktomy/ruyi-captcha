import argparse
from dataclasses import asdict, replace
import json
from pathlib import Path
import sys


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "profile":
        from .profiles.generator import main as generate
        return generate(argv[1:])
    parser = argparse.ArgumentParser(prog="ruyi-captcha")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("profile", help="Generate Firefox profiles; use profile --help for options")
    solve = commands.add_parser("solve", help="Solve Turnstile with site_url + sitekey")
    solve.add_argument("site_url")
    solve.add_argument("sitekey")
    solve.add_argument("--timeout", type=float, default=60)
    solve.add_argument("--attempts", type=int, default=3)
    solve.add_argument("--proxy")
    profile = solve.add_mutually_exclusive_group()
    profile.add_argument("--profile", choices=("linux", "default"), default="linux")
    profile.add_argument("--profile-file", type=Path, help="Use an exported Firefox profile JSON object")
    solve.add_argument("--action")
    solve.add_argument("--cdata")
    solve.add_argument("--json", action="store_true")
    serve = commands.add_parser("serve", help="Start the HTTP API")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--concurrency", type=int)
    serve.add_argument("--queue-size", type=int)
    commands.add_parser("check-runtime", help="Check required kernel capabilities")
    args = parser.parse_args(argv)
    if args.command == "serve":
        try:
            import uvicorn
            from .api import Settings, create_app
        except ImportError:
            parser.error('Install the API extra: pip install "ruyi-captcha[api]"')
        settings = Settings.from_env()
        updates = {name: value for name, value in
                   (("concurrency", args.concurrency), ("queue_size", args.queue_size)) if value is not None}
        settings = replace(settings, **updates)
        if args.host not in ("127.0.0.1", "localhost", "::1") and not settings.api_key:
            parser.error("Set RUYI_CAPTCHA_API_KEY before listening on a remote interface")
        uvicorn.run(create_app(settings), host=args.host, port=args.port, workers=1)
        return 0
    try:
        if args.command == "check-runtime":
            from .runtime import check_runtime
            print(json.dumps(check_runtime().to_dict(), indent=2))
        else:
            from .sdk import solve_turnstile_details
            profile = args.profile
            if args.profile_file:
                profile = json.loads(args.profile_file.read_text(encoding="utf-8"))
                if not isinstance(profile, dict):
                    raise ValueError("profile file must contain a JSON object")
            result = solve_turnstile_details(args.site_url, args.sitekey, timeout=args.timeout,
                attempts=args.attempts, proxy=args.proxy, profile=profile, action=args.action, cdata=args.cdata)
            print(json.dumps(asdict(result)) if args.json else result.token)
        return 0
    except Exception as error:
        print(json.dumps({"error": {"code": getattr(error, "code", None) or "solver_error",
                                    "message": str(error)}}), file=sys.stderr)
        return 1
