"""Testa o TETO DE GASTO da analise visual.

O teto e' a unica protecao contra uma conta inesperada, entao precisa ser
confiavel: se ele calcular errado, ou aborta cedo (perdendo trabalho) ou
deixa passar do combinado.

Uso: python testar_teto_gasto.py
"""

from __future__ import annotations

import datetime
import os
import sys

try:
    sys.stdout.reconfigure(errors="replace")
except Exception:  # noqa: BLE001
    pass

_RAIZ = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _RAIZ)
sys.path.insert(0, os.path.join(_RAIZ, "src"))
os.chdir(_RAIZ)

import analisar_visao as av  # noqa: E402

OK = FALHA = 0
falhas: list[str] = []


def checar(nome: str, cond: bool, extra: str = "") -> None:
    global OK, FALHA
    if cond:
        OK += 1
        print(f"  PASSA  {nome}" + (f"   {extra}" if extra else ""))
    else:
        FALHA += 1
        falhas.append(nome)
        print(f"  FALHA  {nome}   {extra}")


UTC = datetime.timezone.utc

print("=" * 74)
print("1. _e_peak(): o horario caro da DeepSeek")
print("=" * 74)
print("  regra: 01-04h e 06-10h UTC, de segunda a sexta")
casos = [
    (datetime.datetime(2026, 10, 6, 0, 30, tzinfo=UTC), False, "ter 00:30 off"),
    (datetime.datetime(2026, 10, 6, 1, 0, tzinfo=UTC), True, "ter 01:00 PEAK"),
    (datetime.datetime(2026, 10, 6, 2, 0, tzinfo=UTC), True, "ter 02:00 PEAK"),
    (datetime.datetime(2026, 10, 6, 4, 0, tzinfo=UTC), False, "ter 04:00 off"),
    (datetime.datetime(2026, 10, 6, 5, 0, tzinfo=UTC), False, "ter 05:00 off"),
    (datetime.datetime(2026, 10, 6, 6, 0, tzinfo=UTC), True, "ter 06:00 PEAK"),
    (datetime.datetime(2026, 10, 6, 9, 59, tzinfo=UTC), True, "ter 09:59 PEAK"),
    (datetime.datetime(2026, 10, 6, 10, 0, tzinfo=UTC), False, "ter 10:00 off"),
    (datetime.datetime(2026, 10, 6, 23, 0, tzinfo=UTC), False, "ter 23:00 off"),
    # fim de semana e' sempre barato, mesmo dentro da faixa de hora
    (datetime.datetime(2026, 10, 10, 2, 0, tzinfo=UTC), False, "sab 02:00 off"),
    (datetime.datetime(2026, 10, 11, 7, 0, tzinfo=UTC), False, "dom 07:00 off"),
]
for quando, esperado, desc in casos:
    checar(f"{desc}", av._e_peak(quando) == esperado,
           f"obtido {av._e_peak(quando)}")

print()
print("=" * 74)
print("2. _custo_brl(): a conta bate com os tokens MEDIDOS na API?")
print("=" * 74)
# conferencia a mao, com os numeros medidos em 2026-10-03
n_fotos, n_ads = 1000, 10
tf = 458
ent = n_fotos * tf + n_ads * av.TOK_PROMPT
sai = n_ads * av.TOK_SAIDA
off_esperado = (ent * 0.15 / 1e6 + sai * 0.60 / 1e6) * 5.42
peak_esperado = (ent * 0.30 / 1e6 + sai * 1.20 / 1e6) * 5.42
checar("off-peak bate com a conta a mao",
       abs(av._custo_brl(n_fotos, n_ads, "original") - off_esperado) < 0.01,
       f"{av._custo_brl(n_fotos, n_ads, 'original'):.4f} vs {off_esperado:.4f}")
checar("peak bate com a conta a mao",
       abs(av._custo_brl(n_fotos, n_ads, "original", peak=True) - peak_esperado) < 0.01,
       f"{av._custo_brl(n_fotos, n_ads, 'original', peak=True):.4f}")
checar("peak e' exatamente o dobro do off-peak",
       abs(av._custo_brl(n_fotos, n_ads, "original", peak=True)
           / av._custo_brl(n_fotos, n_ads, "original") - 2.0) < 0.001)

# os tokens por foto tem de bater com o que esta no comentario do modulo
checar("TOK_FOTO['original'] = 458 (medido)",
       av.TOK_FOTO["original"] == 458, str(av.TOK_FOTO))
checar("TOK_FOTO['low'] = 203 (medido)",
       av.TOK_FOTO["low"] == 203, str(av.TOK_FOTO))
checar("'low' e' ~56% mais barato que 'original'",
       0.4 < av._custo_brl(1000, 10, "low") / av._custo_brl(1000, 10, "original") < 0.5)

print()
print("=" * 74)
print("3. A estimativa confere com o que foi medido no banco?")
print("=" * 74)
# Medido (medir_custo_analise.py): 1.602 chamadas, 22.165 fotos.
# Off-peak R$ 10,82 · peak R$ 21,63.
est_off = av._custo_brl(22165, 1602, "original")
est_peak = av._custo_brl(22165, 1602, "original", peak=True)
print(f"  com 22.165 fotos em 1.602 chamadas:")
print(f"     off-peak: R$ {est_off:.2f}   (medido: R$ 10,82)")
print(f"     peak    : R$ {est_peak:.2f}   (medido: R$ 21,63)")
checar("off-peak confere com a medicao", abs(est_off - 10.82) < 0.05,
       f"{est_off:.2f}")
checar("peak confere com a medicao", abs(est_peak - 21.63) < 0.05,
       f"{est_peak:.2f}")

print()
print("=" * 74)
print("4. O teto de R$ 20 cobre a lacuna?")
print("=" * 74)
# a lacuna real, com reaproveitamento: 1.602 chamadas / 22.165 fotos
print(f"  ...se rodar em OFF-PEAK: R$ {est_off:.2f} -> CABE no teto de R$ 20")
print(f"  ...se rodar em PEAK    : R$ {est_peak:.2f} -> NAO cabe (para em R$ 20)")
checar("o teto cobre a lacuna no horario barato", est_off < 20.0)
checar("o teto freia no horario caro", est_peak > 20.0)

print()
print("  a recomendacao operacional:")
if av._e_peak():
    print("     AGORA e' PEAK -- rodar tudo estouraria o teto de R$ 20.")
    print("     Espere o horario barato (fora de 01-04h e 06-10h UTC).")
else:
    print("     AGORA e' off-peak -- o teto de R$ 20 cobre a lacuna inteira.")

print()
print("=" * 74)
print(f"{OK} passaram, {FALHA} falharam")
if falhas:
    for f in falhas:
        print(f"  - {f}")
    sys.exit(1)
print("TODAS AS VERIFICACOES PASSARAM")
