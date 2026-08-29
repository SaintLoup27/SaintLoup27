"""
Moteur de conseils — analyse le portefeuille et sort des recommandations.

Ce sont des règles de bon sens en gestion de portefeuille (diversification,
concentration, exposition devise, rééquilibrage vers l'allocation cible,
frais, régularité des versements). Rien n'est prédictif : aucun conseil ne
dit quoi acheter en particulier, ils pointent les déséquilibres de TON
portefeuille et ce qu'il faudrait ajuster.

Chaque conseil : {level, title, detail, action}
  level = "good" | "info" | "warn" | "danger"
"""

from datetime import date, datetime

# Seuils (modifiables librement)
MAX_LINE_WEIGHT = 25.0        # % max sur une seule ligne
MAX_STOCK_WEIGHT = 15.0       # % max sur une action individuelle
MIN_LINES = 5                 # nombre de lignes minimum pour diversifier
MAX_CURRENCY_WEIGHT = 70.0    # % max sur une devise autre que la référence
REBALANCE_BAND = 5.0          # écart en points au-delà duquel on rééquilibre
BIG_LOSS = -20.0              # % de moins-value qui mérite une revue
BIG_GAIN = 60.0               # % de plus-value : penser à écrêter
MAX_FEE_RATIO = 1.0           # % de frais sur le montant investi
MIN_CASH_RATIO = 2.0          # % de liquidités de confort
MAX_CASH_RATIO = 30.0         # % au-delà duquel le cash dort


def _pct(part: float, whole: float) -> float:
    return (part / whole * 100) if whole > 0 else 0.0


def _tip(level, title, detail, action=""):
    return {"level": level, "title": title, "detail": detail, "action": action}


def analyse(snap: dict) -> dict:
    base = snap["base_currency"]
    tot = snap["totals"]
    positions = snap["open_positions"]
    settings = snap["settings"]
    total = tot["total"]
    tips: list[dict] = []

    def money(x):
        return f"{x:,.2f} {base}".replace(",", " ")

    # ── Portefeuille vide ─────────────────────────────────────────────────
    if not positions:
        return {
            "score": None,
            "tips": [_tip(
                "info", "Commence par saisir tes lignes",
                "Ajoute tes achats (ticker, quantité, prix payé) pour que le suivi "
                "et les conseils se mettent en route.",
                "Astuce : cherche le ticker par le nom, ex. « Amundi MSCI World » → CW8.PA.",
            )],
            "checklist": [],
        }

    # ── 1. Concentration par ligne ────────────────────────────────────────
    heaviest = max(positions, key=lambda p: p["weight"])
    if heaviest["weight"] > MAX_LINE_WEIGHT:
        lvl = "danger" if heaviest["weight"] > MAX_LINE_WEIGHT * 1.6 else "warn"
        tips.append(_tip(
            lvl, f"Concentration sur {heaviest['symbol']}",
            f"{heaviest['symbol']} pèse {heaviest['weight']:.1f} % du portefeuille "
            f"({money(heaviest['value_base'])}). Au-delà de {MAX_LINE_WEIGHT:.0f} %, "
            "une mauvaise nouvelle sur cette seule ligne fait mal.",
            f"Allège d'environ {money(heaviest['value_base'] - total * MAX_LINE_WEIGHT / 100)} "
            "ou dilue en investissant ailleurs lors des prochains versements.",
        ))

    stocks = [p for p in positions if p["asset_class"] == "Action"]
    for p in sorted(stocks, key=lambda p: -p["weight"])[:2]:
        if MAX_STOCK_WEIGHT < p["weight"] <= MAX_LINE_WEIGHT:
            tips.append(_tip(
                "info", f"{p['symbol']} : ligne action importante",
                f"Une action individuelle à {p['weight']:.1f} % concentre le risque "
                "spécifique (résultats, gouvernance, secteur).",
                f"Cible plutôt {MAX_STOCK_WEIGHT:.0f} % max par action ; un ETF large "
                "encaisse mieux les accidents.",
            ))

    # ── 2. Nombre de lignes / diversification ─────────────────────────────
    etf_weight = sum(p["weight"] for p in positions if p["asset_class"] == "ETF")
    if len(positions) < MIN_LINES and etf_weight < 60:
        tips.append(_tip(
            "warn", "Portefeuille peu diversifié",
            f"{len(positions)} ligne(s) seulement, dont {etf_weight:.0f} % d'ETF. "
            "Un portefeuille d'actions en direct demande une dizaine de lignes "
            "sur des secteurs différents pour lisser le risque.",
            "Un ETF monde (type MSCI World / S&P 500) fait le travail de "
            "diversification en une seule ligne.",
        ))
    elif etf_weight >= 60:
        tips.append(_tip(
            "good", "Socle ETF solide",
            f"{etf_weight:.0f} % du portefeuille est en ETF : la diversification "
            "de fond est assurée, les lignes en direct jouent le rôle de satellites.",
        ))

    # ── 3. Exposition devise ──────────────────────────────────────────────
    by_cur = snap["allocation"]["by_currency"]
    for cur, val in by_cur.items():
        w = _pct(val, total)
        if cur != base and w > MAX_CURRENCY_WEIGHT:
            tips.append(_tip(
                "warn", f"Exposition {cur} élevée ({w:.0f} %)",
                f"Tes performances dépendent aussi du taux {cur}/{base} : une baisse "
                f"du {cur} rogne tes gains même si les cours montent.",
                "Rien de grave sur le long terme, mais garde-le en tête ; des ETF "
                f"couverts en {base} (« hedged ») existent si la volatilité te gêne.",
            ))

    # ── 4. Rééquilibrage vers l'allocation cible ──────────────────────────
    targets = settings.get("targets") or {}
    by_class = snap["allocation"]["by_class"]
    for cls, target in targets.items():
        if target <= 0:
            continue
        actual = _pct(by_class.get(cls, 0.0), total)
        gap = actual - target
        if abs(gap) >= REBALANCE_BAND:
            amount = abs(gap) / 100 * total
            sens = "au-dessus" if gap > 0 else "en dessous"
            tips.append(_tip(
                "info", f"Rééquilibrage : {cls} à {actual:.0f} % (cible {target:.0f} %)",
                f"Poche {sens} de la cible de {abs(gap):.1f} points.",
                (f"Oriente environ {money(amount)} de tes prochains versements vers "
                 f"les autres poches." if gap > 0 else
                 f"Renforce {cls} d'environ {money(amount)} pour revenir à ta cible."),
            ))

    # ── 5. Lignes en forte moins-value / plus-value ───────────────────────
    for p in positions:
        if p["pnl_pct"] is None:
            continue
        if p["pnl_pct"] <= BIG_LOSS:
            tips.append(_tip(
                "warn", f"{p['symbol']} : {p['pnl_pct']:.1f} %",
                f"Moins-value latente de {money(p['pnl_base'])} (PRU {p['pru']:.2f} "
                f"{p['currency']} vs {p['price']:.2f} aujourd'hui).",
                "Question à te poser : la raison de l'achat tient-elle toujours ? "
                "Si oui, une baisse est une occasion de renforcer ; sinon, coupe. "
                "Ne renforce jamais juste pour « faire baisser le PRU ».",
            ))
        elif p["pnl_pct"] >= BIG_GAIN and p["weight"] > MAX_STOCK_WEIGHT:
            tips.append(_tip(
                "good", f"{p['symbol']} : +{p['pnl_pct']:.0f} %",
                f"Belle performance ({money(p['pnl_base'])}), mais la ligne pèse "
                f"maintenant {p['weight']:.1f} % du portefeuille.",
                "Tu peux prendre une partie des gains pour revenir à ton poids cible "
                "— attention à la fiscalité sur les plus-values (hors PEA/AV).",
            ))

    # ── 6. Liquidités ─────────────────────────────────────────────────────
    cash_w = _pct(tot["cash"], total)
    if cash_w > MAX_CASH_RATIO:
        tips.append(_tip(
            "warn", f"Beaucoup de liquidités ({cash_w:.0f} %)",
            f"{money(tot['cash'])} dorment sur le compte espèces et perdent de la "
            "valeur avec l'inflation.",
            "Étale l'investissement sur quelques mois (DCA) plutôt que tout d'un coup "
            "si le marché te fait peur.",
        ))
    elif cash_w < MIN_CASH_RATIO and tot["value"] > 0:
        tips.append(_tip(
            "info", "Peu de liquidités disponibles",
            "Tout est investi : aucune munition pour saisir une baisse ni pour "
            "encaisser un imprévu sans vendre.",
            "Garde ton épargne de précaution hors de ce portefeuille, et éventuellement "
            "quelques % de cash ici.",
        ))

    # ── 7. Frais ──────────────────────────────────────────────────────────
    fee_ratio = _pct(tot["fees"], tot["invested"])
    if fee_ratio > MAX_FEE_RATIO:
        tips.append(_tip(
            "warn", f"Frais de courtage élevés ({fee_ratio:.2f} % de l'investi)",
            f"{money(tot['fees'])} de frais cumulés. Sur le long terme, les frais "
            "grignotent la performance de façon certaine, contrairement aux gains.",
            "Regroupe tes ordres (moins d'ordres, plus gros) ou compare les courtiers.",
        ))

    # ── 8. Régularité des versements ──────────────────────────────────────
    last_buy = max((t["date"] for t in snap["transactions"] if t["type"] == "BUY"), default=None)
    if last_buy:
        days = (date.today() - datetime.strptime(last_buy, "%Y-%m-%d").date()).days
        if days > 90:
            tips.append(_tip(
                "info", f"Aucun achat depuis {days} jours",
                "L'investissement programmé (une somme fixe chaque mois) est ce qui "
                "marche le mieux sur la durée : ça évite d'essayer de timer le marché.",
                f"Même {money(settings.get('monthly_contribution') or 100)} par mois "
                "font une grosse différence sur 10 ans.",
            ))

    # ── 9. Fraîcheur des cours ────────────────────────────────────────────
    if snap.get("stale"):
        tips.append(_tip(
            "info", "Certains cours ne sont pas à jour",
            "Le marché n'a pas répondu pour au moins une ligne : c'est le dernier "
            "cours connu qui s'affiche.",
            "Clique « Actualiser » ou saisis un prix à la main sur la ligne concernée.",
        ))

    order = {"danger": 0, "warn": 1, "info": 2, "good": 3}
    tips.sort(key=lambda t: order[t["level"]])

    return {
        "score": _score(snap, tips),
        "tips": tips,
        "checklist": _checklist(snap),
    }


def _score(snap: dict, tips: list[dict]) -> int:
    """Score de santé 0-100 : diversification, concentration, frais, devise."""
    penalty = sum({"danger": 22, "warn": 10, "info": 3, "good": 0}[t["level"]] for t in tips)
    bonus = 8 if any(t["level"] == "good" for t in tips) else 0
    return max(0, min(100, 100 - penalty + bonus))


def _checklist(snap: dict) -> list[dict]:
    """Repères rapides, vrais/faux, affichés sous les conseils."""
    tot = snap["totals"]
    positions = snap["open_positions"]
    total = tot["total"] or 1
    heaviest = max((p["weight"] for p in positions), default=0)
    etf_w = sum(p["weight"] for p in positions if p["asset_class"] == "ETF")
    return [
        {"label": f"Au moins {MIN_LINES} lignes", "ok": len(positions) >= MIN_LINES},
        {"label": f"Aucune ligne > {MAX_LINE_WEIGHT:.0f} %", "ok": heaviest <= MAX_LINE_WEIGHT},
        {"label": "Socle ETF ≥ 50 %", "ok": etf_w >= 50},
        {"label": f"Frais < {MAX_FEE_RATIO:.0f} % de l'investi",
         "ok": _pct(tot["fees"], tot["invested"]) < MAX_FEE_RATIO},
        {"label": f"Liquidités entre {MIN_CASH_RATIO:.0f} et {MAX_CASH_RATIO:.0f} %",
         "ok": MIN_CASH_RATIO <= _pct(tot["cash"], total) <= MAX_CASH_RATIO},
    ]
