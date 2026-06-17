"""
╔══════════════════════════════════════════════════════╗
║   DOOG — Funding Trade Alert Bot                     ║
║   Notifications via ntfy.sh (zéro compte requis)    ║
║   Surveille : APR live, variation APR 12h,           ║
║               variation prix 12h, liquidation        ║
╚══════════════════════════════════════════════════════╝

SETUP (3 étapes) :
  1. Installe ntfy sur ton téléphone (iOS / Android)
  2. Abonne-toi au topic : doog-funding-XXXX (choisis un nom unique)
  3. Remplace NTFY_TOPIC ci-dessous par ce nom
  4. Lance : python doog_alert_bot.py
"""

import requests
import time
import json
from datetime import datetime, timedelta
from collections import defaultdict

# ============================================================
# ⚙️  CONFIG — MODIFIE UNIQUEMENT CETTE SECTION
# ============================================================

# Ton topic ntfy — choisis un nom unique (ex: doog-funding-alerts-x7k2)
# Plus il est unique, moins il y a de chances que quelqu'un tombe dessus
NTFY_TOPIC = "doog-funding-alerts-z4g7"

# Priorité des notifications ntfy : low / default / high / urgent
NTFY_PRIORITY = "high"

# ── Tes positions actives ─────────────────────────────────────
# Ajoute une entrée par position ouverte.
# apr_entry    : APR au moment où tu es entré en position
# apr_min_abs  : alerte si APR live descend sous ce seuil absolu
# apr_delta    : alerte si APR a perdu X points vs ton entrée
# lever        : ton levier (utilisé pour calculer la distance de liquidation)
POSITIONS = [
    # ── Exemple Hyperliquid ──────────────────────────────────
    {
        "coin":         "BTC",
        "protocol":     "Hyperliquid",
        "apr_entry":    19.0,   # APR au moment de ton entrée en position
        "apr_min_abs":  10.0,   # Alerte si APR live descend sous 10%
        "apr_delta":    7.0,    # Alerte si APR perd 7 points vs entrée
        "lever":        1.0,    # Ton levier (1x = pas de levier)
    },
    # ── Exemple dYdX ────────────────────────────────────────
    {
        "coin":         "ETH",
        "protocol":     "dYdX",
        "apr_entry":    15.0,
        "apr_min_abs":  10.0,
        "apr_delta":    7.0,
        "lever":        1.0,
    },
]

# ── Seuils de variation sur 12h ───────────────────────────────
PRICE_CHANGE_THRESHOLD_PCT  = 5.0   # Alerte si prix ±5% sur 12h
APR_CHANGE_THRESHOLD_PTS    = 15.0  # Alerte si APR varie de ±15 pts sur 12h
WINDOW_HOURS                = 12    # Fenêtre de comparaison

# ── Intervalle de vérification ────────────────────────────────
CHECK_INTERVAL_SECONDS = 300  # Toutes les 5 minutes

# ============================================================
# FIN CONFIG
# ============================================================

# ── Historique des valeurs sur la fenêtre ────────────────────
# Structure : { "BTC_Hyperliquid": [ (timestamp, apr, price), ... ] }
history = defaultdict(list)

# ── Cooldown anti-spam ───────────────────────────────────────
# Ne répète pas la même alerte avant 30 min
alert_memory = {}
ALERT_COOLDOWN = 1800  # secondes


def now_str() -> str:
    return datetime.now().strftime("%H:%M:%S")


def can_alert(key: str) -> bool:
    return (time.time() - alert_memory.get(key, 0)) > ALERT_COOLDOWN


def mark_alerted(key: str):
    alert_memory[key] = time.time()


# ── ntfy ─────────────────────────────────────────────────────
def strip_emoji(text: str) -> str:
    """Supprime les emojis pour les headers HTTP (latin-1 uniquement)."""
    return text.encode('ascii', 'ignore').decode('ascii').strip()

def send_ntfy(title: str, message: str, priority: str = NTFY_PRIORITY, tags: str = ""):
    """Envoie une notification push via ntfy.sh."""
    try:
        headers = {
            "Title":        strip_emoji(title),   # headers = ASCII uniquement
            "Priority":     priority,
            "Tags":         tags,
            "Content-Type": "text/plain; charset=utf-8",
        }
        # Le body contient le titre complet avec emojis + le message
        body = f"{title}\n\n{message}"
        r = requests.post(
            f"https://ntfy.sh/{NTFY_TOPIC}",
            data=body.encode("utf-8"),
            headers=headers,
            timeout=10
        )
        if r.status_code == 200:
            print(f"[{now_str()}] ✅ ntfy envoyé : {strip_emoji(title)}")
        else:
            print(f"[{now_str()}] ❌ ntfy erreur {r.status_code} : {r.text}")
    except Exception as e:
        print(f"[{now_str()}] ❌ ntfy exception : {e}")


# ── Fetch Hyperliquid ─────────────────────────────────────────
def fetch_hyperliquid() -> dict:
    """Retourne { coin: { apr, price } } pour tous les perps Hyperliquid."""
    try:
        r = requests.post(
            "https://api.hyperliquid.xyz/info",
            json={"type": "metaAndAssetCtxs"},
            timeout=15
        )
        data = r.json()
        universe = data[0]["universe"]
        ctxs     = data[1]
        result   = {}
        for i, asset in enumerate(universe):
            ctx = ctxs[i]
            funding = float(ctx.get("funding", 0))
            apr     = funding * 8760 * 100
            price   = float(ctx.get("markPx", 0))
            result[asset["name"]] = {"apr": apr, "price": price}
        print(f"[{now_str()}] ✅ Hyperliquid : {len(result)} marchés")
        return result
    except Exception as e:
        print(f"[{now_str()}] ❌ Hyperliquid : {e}")
        return {}


# ── Fetch dYdX ───────────────────────────────────────────────
def fetch_dydx() -> dict:
    """Retourne { coin: { apr, price } } pour tous les perps dYdX."""
    try:
        r = requests.get(
            "https://indexer.dydx.trade/v4/perpetualMarkets?limit=100",
            timeout=15
        )
        data    = r.json()
        markets = data.get("markets", {})
        result  = {}
        for ticker, m in markets.items():
            coin    = m.get("baseAsset") or ticker.split("-")[0]
            funding = float(m.get("nextFundingRate", 0))
            apr     = funding * 8760 * 100
            price   = float(m.get("oraclePrice", 0))
            result[coin] = {"apr": apr, "price": price}
        print(f"[{now_str()}] ✅ dYdX : {len(result)} marchés")
        return result
    except Exception as e:
        print(f"[{now_str()}] ❌ dYdX : {e}")
        return {}


# ── Enregistre l'historique et purge > fenêtre ───────────────
def record_history(key: str, apr: float, price: float):
    ts = time.time()
    history[key].append((ts, apr, price))
    # Purge les entrées trop anciennes (garde WINDOW_HOURS + 1h de marge)
    cutoff = ts - (WINDOW_HOURS + 1) * 3600
    history[key] = [(t, a, p) for t, a, p in history[key] if t >= cutoff]


def get_reference(key: str) -> tuple | None:
    """Retourne (apr, price) il y a ~WINDOW_HOURS, ou None si pas assez d'historique."""
    cutoff = time.time() - WINDOW_HOURS * 3600
    older  = [(t, a, p) for t, a, p in history[key] if t <= cutoff]
    if not older:
        return None
    # Prend le point le plus récent parmi ceux qui datent d'au moins WINDOW_HOURS
    return older[-1][1], older[-1][2]  # (apr_ref, price_ref)


# ── Vérifie une position ──────────────────────────────────────
def check_position(pos: dict, hl: dict, dy: dict):
    coin     = pos["coin"]
    protocol = pos["protocol"]
    entry    = pos["apr_entry"]
    min_abs  = pos["apr_min_abs"]
    delta    = pos["apr_delta"]
    lever    = pos.get("lever", 1.0)
    key      = f"{coin}_{protocol}"

    source = hl if protocol == "Hyperliquid" else dy
    market = source.get(coin)

    if not market:
        print(f"[{now_str()}] ⚠ {coin}/{protocol} introuvable")
        return

    apr_live = market["apr"]
    price    = market["price"]

    print(f"[{now_str()}] {key} — APR: {apr_live:.2f}% | Prix: ${price:,.4f}")

    # Enregistre dans l'historique
    record_history(key, apr_live, price)

    # ── Alerte 1 : APR sous seuil absolu ─────────────────────
    k1 = f"{key}_apr_abs"
    if apr_live < min_abs and can_alert(k1):
        send_ntfy(
            title    = f"🔴 APR BAS — {coin}/{protocol}",
            message  = (f"APR live : {apr_live:.2f}%\n"
                        f"Seuil minimum : {min_abs}%\n\n"
                        f"⚠️ Envisage de fermer la position."),
            priority = "urgent",
            tags     = "rotating_light"
        )
        mark_alerted(k1)

    # ── Alerte 2 : APR a perdu X points vs entrée ────────────
    k2       = f"{key}_apr_delta"
    seuil_d  = entry - delta
    if apr_live < seuil_d and can_alert(k2):
        drop = entry - apr_live
        send_ntfy(
            title    = f"🟠 CHUTE APR — {coin}/{protocol}",
            message  = (f"APR entrée : {entry:.2f}%\n"
                        f"APR live : {apr_live:.2f}%\n"
                        f"Chute : -{drop:.1f} pts (seuil : -{delta} pts)\n\n"
                        f"Le funding rate s'est dégradé."),
            priority = "high",
            tags     = "warning"
        )
        mark_alerted(k2)

    # ── Alerte 3 : Variation APR sur 12h ─────────────────────
    ref = get_reference(key)
    if ref:
        apr_ref, price_ref = ref

        k3         = f"{key}_apr_12h"
        apr_change = apr_live - apr_ref
        if abs(apr_change) >= APR_CHANGE_THRESHOLD_PTS and can_alert(k3):
            direction = "monté" if apr_change > 0 else "chuté"
            emoji     = "📈" if apr_change > 0 else "📉"
            send_ntfy(
                title    = f"{emoji} APR {direction.upper()} — {coin}/{protocol}",
                message  = (f"APR il y a {WINDOW_HOURS}h : {apr_ref:.2f}%\n"
                            f"APR maintenant : {apr_live:.2f}%\n"
                            f"Variation : {apr_change:+.1f} pts sur {WINDOW_HOURS}h"),
                priority = "default",
                tags     = "chart_with_upwards_trend" if apr_change > 0 else "chart_with_downwards_trend"
            )
            mark_alerted(k3)

        # ── Alerte 4 : Variation prix sur 12h ────────────────
        if price_ref and price_ref > 0:
            k4           = f"{key}_price_12h"
            price_change = ((price - price_ref) / price_ref) * 100
            if abs(price_change) >= PRICE_CHANGE_THRESHOLD_PCT and can_alert(k4):
                if price_change > 0:
                    # Hausse = risque liquidation short → ROUGE URGENT
                    send_ntfy(
                        title    = f"🔴 PRIX EN FORTE HAUSSE — {coin} +{price_change:.1f}%/{WINDOW_HOURS}h",
                        message  = (f"Prix il y a {WINDOW_HOURS}h : ${price_ref:,.4f}\n"
                                    f"Prix maintenant : ${price:,.4f}\n"
                                    f"Hausse : +{price_change:.1f}%\n\n"
                                    f"🚨 Ton short se rapproche de la liquidation.\n"
                                    f"Vérifie ta marge sur {protocol} immédiatement."),
                        priority = "urgent",
                        tags     = "rotating_light,arrow_up"
                    )
                else:
                    # Baisse = ton spot perd de la valeur, short compense → INFO BLEUE
                    send_ntfy(
                        title    = f"🔵 PRIX EN BAISSE — {coin} {price_change:.1f}%/{WINDOW_HOURS}h",
                        message  = (f"Prix il y a {WINDOW_HOURS}h : ${price_ref:,.4f}\n"
                                    f"Prix maintenant : ${price:,.4f}\n"
                                    f"Baisse : {price_change:.1f}%\n\n"
                                    f"Info : ton short compense la perte du spot.\n"
                                    f"Position delta-neutral maintenue."),
                        priority = "low",
                        tags     = "arrow_down"
                    )
                mark_alerted(k4)

    # ── Alerte 5 : Liquidation proche ────────────────────────
    liq_dist = (1 / lever) * 100 * 0.85
    k5 = f"{key}_liq"
    if liq_dist < 20.0 and can_alert(k5):
        liq_price = price * (1 + liq_dist / 100) if price > 0 else None
        liq_line  = f"Prix de liquidation : ${liq_price:,.4f}" if liq_price else ""
        send_ntfy(
            title    = f"🚨 LIQUIDATION PROCHE — {coin}/{protocol}",
            message  = (f"Distance de liquidation : {liq_dist:.1f}%\n"
                        f"{liq_line}\n\n"
                        f"🚨 Rajoute de la marge ou ferme la position !"),
            priority = "urgent",
            tags     = "rotating_light,rotating_light"
        )
        mark_alerted(k5)


# ── Boucle principale ─────────────────────────────────────────
def main():
    print(f"\n{'='*55}")
    print(f"  DOOG — Funding Trade Alert Bot")
    print(f"  Topic ntfy : {NTFY_TOPIC}")
    print(f"  {len(POSITIONS)} position(s) surveillée(s)")
    print(f"  Intervalle : {CHECK_INTERVAL_SECONDS}s | Fenêtre : {WINDOW_HOURS}h")
    print(f"{'='*55}\n")

    if "CHANGE-MOI" in NTFY_TOPIC:
        print("❌ ERREUR : Remplace NTFY_TOPIC par ton topic unique dans la config.")
        return

    # Notification de démarrage
    send_ntfy(
        title   = "✅ Doog Alert Bot démarré",
        message = (f"Positions : {', '.join(p['coin'] for p in POSITIONS)}\n"
                   f"Vérification toutes les {CHECK_INTERVAL_SECONDS // 60} min\n"
                   f"Fenêtre d'analyse : {WINDOW_HOURS}h"),
        tags    = "white_check_mark"
    )

    # Note : les alertes de variation 12h ne se déclencheront qu'après
    # 12h de fonctionnement (le temps de constituer l'historique)
    print(f"[{now_str()}] ℹ️  Les alertes de variation {WINDOW_HOURS}h démarreront "
          f"après {WINDOW_HOURS}h de collecte de données.\n")

    while True:
        try:
            print(f"\n[{now_str()}] ── Vérification ──────────────────────")
            hl = fetch_hyperliquid()
            dy = fetch_dydx()
            for pos in POSITIONS:
                check_position(pos, hl, dy)
            print(f"[{now_str()}] Prochain check dans {CHECK_INTERVAL_SECONDS}s")
        except Exception as e:
            print(f"[{now_str()}] ❌ Erreur : {e}")
        time.sleep(CHECK_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
