# Local integration

Source: https://github.com/AgIzT/astrbot_plugin_anysearch
Commit: 983a7acb2f6940ab441a521be684085e0913738e

2026-10-18: Retained the community search, extraction, and batch tools. Bounded
responses to 2 MB and tool text to 24000 characters; capped searches at 10 results.
Do not expose raw upstream failures: anonymous quota errors may contain newly
generated credentials. Reject URLs with embedded credentials and local hostnames.
No paid key is configured. Original MIT license is retained.

## Additional local integration (2026-10-18)

The client reuses the installed Codex OAuth plugin's already configured external
proxy in memory. It can retry connection failures, timeouts and transient server
errors once through the independently verified mainland gateway on loopback port
17898. Authentication, quota and HTTP 429 errors do not trigger route switching.
Proxy URLs and raw service error bodies are never included in user-facing errors.
The source gateway is unchanged; only public search traffic uses this fallback.
Keep this patch when updating the upstream plugin. Search tools are capped at ten
results per query, with bounded response and output sizes.

