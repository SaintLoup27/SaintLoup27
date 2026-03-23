# 🤖 Crypto DCA Bot

Bot de trading automatisé utilisant la stratégie **DCA (Dollar Cost Averaging)**.
Il achète périodiquement des cryptos à intervalle fixe et gère les prises de bénéfices automatiquement.

## Pourquoi le DCA ?

Le DCA est la stratégie passive la plus éprouvée :
- Pas besoin de "timer" le marché
- Réduit l'impact de la volatilité
- Convient aussi bien aux débutants qu'aux experts
- Historiquement rentable sur BTC/ETH sur le long terme

## Installation

```bash
cd crypto_bot
pip install -r requirements.txt
cp .env.example .env
# Éditez .env avec vos clés API
```

## Configuration

Éditez `config.py` :

```python
DCA_CONFIG = {
    "assets": {
        "BTCUSDT": 20,   # 20 $ de BTC par cycle
        "ETHUSDT": 15,   # 15 $ d'ETH par cycle
    },
    "interval_hours": 168,     # Une fois par semaine
    "take_profit_pct": 50,     # Vendre si +50% de profit
}
```

## Utilisation

```bash
# Simulation (sans argent réel) — testez d'abord !
python bot.py --dry-run

# Rapport du portfolio
python bot.py --report

# Production (après avoir mis TESTNET=false dans .env)
python bot.py
```

## Sécurité des clés API Binance

1. Activez **uniquement** "Spot Trading" — jamais les retraits
2. Restreignez l'IP à votre serveur si possible
3. Ne committez **jamais** votre fichier `.env`

## Notifications Telegram

Le bot envoie :
- Une alerte à chaque achat
- Une alerte à chaque prise de bénéfices
- Un rapport quotidien à 08h00

## Structure

```
crypto_bot/
├── bot.py              # Point d'entrée
├── config.py           # Configuration
├── strategies/
│   └── dca.py          # Logique DCA + take-profit
├── utils/
│   ├── notifier.py     # Notifications Telegram
│   └── portfolio.py    # Suivi local du portfolio
├── requirements.txt
└── .env.example
```

## Avertissement

> Le trading de cryptomonnaies comporte des risques de perte en capital.
> Testez toujours avec le testnet Binance avant d'utiliser de l'argent réel.
> N'investissez que ce que vous pouvez vous permettre de perdre.
