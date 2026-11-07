# ruyi-captcha

This is the application layer around ff-ruyijs. Keep browser-standard behavior,
native bindings and transport implementations in `/root/ff-ruyijs`.
Provider drivers, SDK functions, HTTP APIs, task lifecycles and profiles live here.

Platform release wheels embed a compiled, pinned kernel artifact; lightweight
wheels keep the external dependency. Preserve upstream build scripts and native
notices, platform tags and private binding imports when changing the bundler.
Release tags must match pyproject.toml, and all platform checks gate publication.

The first supported provider is Turnstile using `site_url + sitekey`.
Support both direct Python calls and the optional HTTP service with the same
solver implementation. Keep API concurrency and pending work bounded, and never
report token acquisition as backend acceptance.

Run `.venv/bin/python -B -m unittest discover -s tests -v` after changes.
Native tests may skip when a compatible runtime is absent; report that explicitly.
Live tests must validate real tokens at the demo's own backend and save only
lengths and validation results, not tokens or proxy credentials.

Commit author and committer: Starktomy
<37720599+Starktomy@users.noreply.github.com>.
Include `Co-authored-by: Codex <codex@openai.com>` in each new commit.
