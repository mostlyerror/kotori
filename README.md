# 🐦 kotori (小鳥)

A portfolio companion for options traders running iron condors.

## Run

```bash
pip install -e .
cp .env.example .env  # fill in API keys
kotorid               # background sync daemon
kotori                # TUI
```

Requires Python 3.13+. Data lives at `~/.kotori/kotori.db` (override with `KOTORI_DB`).
