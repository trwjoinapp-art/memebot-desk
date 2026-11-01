---
title: Memebot Desk
emoji: 📈
colorFrom: indigo
colorTo: pink
sdk: docker
app_port: 7860
pinned: false
---

# Memebot desk (PAPER ONLY)

Copy-trading desk that mirrors watched Solana wallets with **fake money** ($100 paper bankroll) plus its live dashboard.
No wallets, private keys or signing anywhere (`assert PAPER is True`).

* `/` dashboard · `/api/state` JSON · `/healthz` health
* Secrets: `HELIUS_API_KEY` (required). Optional: `HF_TOKEN` + `STATE_REPO` (private dataset that stores state snapshots), `PUBLIC_URL` (self keep-alive ping).
