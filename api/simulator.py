"""
api/simulator.py
=================
Moteur de simulation épidémique numérique.

Modèles supportés :
    SIR    — Susceptible → Infectieux → Rétabli
    SEIR   — + compartiment Exposé (incubation)
    SEIRS  — + perte d'immunité (ω)
    SEIRD  — + compartiment Décédés (μ)

Méthode numérique : scipy.integrate.solve_ivp (RK45, adaptatif)
"""

import math
from typing import Optional
import numpy as np
from scipy.integrate import solve_ivp


def run_simulation(
    formalism: str,
    N: int,
    beta: float,
    gamma: float,
    sigma: Optional[float] = None,
    mu: Optional[float] = None,
    omega: Optional[float] = None,
    I0: int = 10,
    R0_init: int = 0,
    days: int = 365,
    dt: float = 1.0,
) -> dict:
    """
    Lance une simulation épidémique et retourne les séries temporelles
    et les métriques clés.

    Paramètres :
        formalism : SIR, SEIR, SEIRS, SEIRD
        N         : taille population
        beta      : taux transmission (jour⁻¹)
        gamma     : taux guérison (jour⁻¹)
        sigma     : taux sortie latence (jour⁻¹) — SEIR seulement
        mu        : taux mortalité maladie (jour⁻¹) — SEIRD seulement
        omega     : taux perte immunité (jour⁻¹) — SEIRS seulement
        I0        : infectieux initiaux
        R0_init   : guéris initiaux
        days      : durée simulation
        dt        : pas de temps (jours)

    Retourne un dict avec time_series + métriques.
    """
    formalism = formalism.upper()
    if sigma is None:
        sigma = 0.196   # période incubation ~5 jours (valeur COVID typique)
    if mu is None:
        mu = 0.0
    if omega is None:
        omega = 0.0

    t_span = (0, days)
    t_eval = np.arange(0, days + dt, dt)

    # ── Conditions initiales ─────────────────────────────────────────────────
    E0 = 0
    D0 = 0
    S0 = N - I0 - R0_init - E0

    if formalism == "SIR":
        y0 = [S0, I0, R0_init]
        def deriv(t, y):
            S, I, R = y
            dS = -beta * S * I / N
            dI =  beta * S * I / N - gamma * I
            dR =  gamma * I
            return [dS, dI, dR]

    elif formalism == "SEIR":
        y0 = [S0, E0, I0, R0_init]
        def deriv(t, y):
            S, E, I, R = y
            dS = -beta * S * I / N
            dE =  beta * S * I / N - sigma * E
            dI =  sigma * E - gamma * I
            dR =  gamma * I
            return [dS, dE, dI, dR]

    elif formalism == "SEIRS":
        y0 = [S0, E0, I0, R0_init]
        def deriv(t, y):
            S, E, I, R = y
            dS = -beta * S * I / N + omega * R
            dE =  beta * S * I / N - sigma * E
            dI =  sigma * E - gamma * I
            dR =  gamma * I - omega * R
            return [dS, dE, dI, dR]

    elif formalism == "SEIRD":
        y0 = [S0, E0, I0, R0_init, D0]
        def deriv(t, y):
            S, E, I, R, D = y
            dS = -beta * S * I / N
            dE =  beta * S * I / N - sigma * E
            dI =  sigma * E - (gamma + mu) * I
            dR =  gamma * I
            dD =  mu * I
            return [dS, dE, dI, dR, dD]

    elif formalism == "SIS":
        y0 = [S0, I0]
        def deriv(t, y):
            S, I = y
            dS = -beta * S * I / N + gamma * I
            dI =  beta * S * I / N - gamma * I
            return [dS, dI]

    else:
        # Fallback SEIR pour tout formalisme inconnu
        formalism = "SEIR"
        y0 = [S0, E0, I0, R0_init]
        def deriv(t, y):
            S, E, I, R = y
            dS = -beta * S * I / N
            dE =  beta * S * I / N - sigma * E
            dI =  sigma * E - gamma * I
            dR =  gamma * I
            return [dS, dE, dI, dR]

    # ── Intégration numérique ────────────────────────────────────────────────
    sol = solve_ivp(
        deriv, t_span, y0,
        method="RK45",
        t_eval=t_eval,
        rtol=1e-6, atol=1e-8,
        dense_output=False,
    )

    if not sol.success:
        raise RuntimeError(f"Échec intégration numérique : {sol.message}")

    t_arr = sol.t.tolist()

    # ── Extraire les compartiments selon le formalisme ───────────────────────
    compartments = {}
    if formalism == "SIR":
        S_arr, I_arr, R_arr = sol.y
        compartments = {"S": S_arr, "I": I_arr, "R": R_arr}
    elif formalism in ("SEIR", "SEIRS"):
        S_arr, E_arr, I_arr, R_arr = sol.y
        compartments = {"S": S_arr, "E": E_arr, "I": I_arr, "R": R_arr}
    elif formalism == "SEIRD":
        S_arr, E_arr, I_arr, R_arr, D_arr = sol.y
        compartments = {"S": S_arr, "E": E_arr, "I": I_arr, "R": R_arr, "D": D_arr}
    elif formalism == "SIS":
        S_arr, I_arr = sol.y
        compartments = {"S": S_arr, "I": I_arr}
    else:
        S_arr, E_arr, I_arr, R_arr = sol.y
        compartments = {"S": S_arr, "E": E_arr, "I": I_arr, "R": R_arr}

    I_arr = compartments["I"]

    # ── Métriques clés ───────────────────────────────────────────────────────
    peak_idx     = int(np.argmax(I_arr))
    peak_day     = int(round(t_arr[peak_idx]))
    peak_infected= int(round(I_arr[peak_idx]))

    # Taux d'attaque = fraction qui a été infectée
    # = 1 - S_final/N (pour SIR/SEIR)
    S_final      = float(compartments["S"][-1])
    total_infected = int(round(N - S_final))
    attack_rate  = round((N - S_final) / N, 4)

    # Durée de l'épidémie : jours où I > 1% du pic
    threshold    = peak_infected * 0.01
    active_days  = [i for i, val in enumerate(I_arr) if val >= threshold]
    epidemic_duration = int(active_days[-1] - active_days[0]) if active_days else 0

    # R0 effectif = β / γ × S0/N
    R0_eff = round(beta / gamma * (S0 / N), 2)

    # ── Séries temporelles — downsample si trop long ─────────────────────────
    # Maximum 730 points dans la réponse JSON
    max_points = 730
    step = max(1, len(t_arr) // max_points)
    idx_sample = list(range(0, len(t_arr), step))

    time_series = {"t": [round(t_arr[i]) for i in idx_sample]}
    for comp, arr in compartments.items():
        time_series[comp] = [int(round(float(arr[i]))) for i in idx_sample]

    # ── Résumé textuel ───────────────────────────────────────────────────────
    summary_parts = [
        f"Modèle {formalism} — population N={N:,}.",
        f"Pic épidémique au jour {peak_day} avec {peak_infected:,} infectieux simultanés.",
        f"{total_infected:,} cas au total ({attack_rate*100:.1f}% de la population).",
    ]
    if "D" in compartments:
        total_dead = int(round(float(compartments["D"][-1])))
        summary_parts.append(f"Décès estimés : {total_dead:,}.")
    summary_parts.append(f"R₀ effectif = {R0_eff}.")
    if epidemic_duration > 0:
        summary_parts.append(f"Durée de l'épidémie : ~{epidemic_duration} jours.")

    return {
        "peak_infected":         peak_infected,
        "peak_day":              peak_day,
        "total_infected":        total_infected,
        "attack_rate":           attack_rate,
        "epidemic_duration_days":epidemic_duration,
        "R0_effective":          R0_eff,
        "time_series":           time_series,
        "summary":               " ".join(summary_parts),
    }


# ── Test rapide ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("Test SIR  :", end=" ")
    r = run_simulation("SIR",  N=1_000_000, beta=0.5,  gamma=0.25, days=180)
    print(f"pic={r['peak_infected']:,} au jour {r['peak_day']}, AR={r['attack_rate']*100:.1f}%")

    print("Test SEIR :", end=" ")
    r = run_simulation("SEIR", N=1_000_000, beta=0.31, gamma=0.143, sigma=0.196, days=365)
    print(f"pic={r['peak_infected']:,} au jour {r['peak_day']}, AR={r['attack_rate']*100:.1f}%")

    print("Test SEIRD:", end=" ")
    r = run_simulation("SEIRD",N=68_000_000,beta=0.31, gamma=0.143, sigma=0.196, mu=0.001, days=365)
    print(f"pic={r['peak_infected']:,} au jour {r['peak_day']}, D_final={r['time_series']['D'][-1]:,}")

    print("Test SEIRS:", end=" ")
    r = run_simulation("SEIRS",N=1_000_000, beta=0.4, gamma=0.2, sigma=0.3, omega=0.01, days=730)
    print(f"pic={r['peak_infected']:,}, durée={r['epidemic_duration_days']}j")
