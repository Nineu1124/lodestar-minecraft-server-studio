# Contributing

1. Use Python 3.11 or newer.
2. Keep the default installation dependency-free unless a dependency has a clear operational benefit.
3. Preserve the local-only security model and validate every path accepted from the browser.
4. Run `python -m py_compile server.py` and `node --check static/app.js` before opening a pull request.
5. Do not commit `data/settings.json`, server logs, worlds, Mods, FRP credentials, or player data.

