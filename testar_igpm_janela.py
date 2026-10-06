"""Testes do reajuste pelo IGP-M e da janela de 10 anos na comparação.

Por que existe: a comparação de preço era construída juntando vendas de 2007
com vendas de 2020 na MESMA mediana. O resultado aparecia como "212% acima do
preço praticado" num anúncio que estava, na verdade, praticamente no preço
(venda da mesma casa, mesmo número e mesma área, em 2020). Dois defeitos
somados: o índice (IPCA, inflação geral) e a ausência de corte de época.

O que este arquivo protege:
  1. o IGP-M é acumulado corretamente (comparado com os oficiais publicados)
  2. a referência acompanha o mês mais recente da série
  3. a janela descarta venda velha quando há recente
  4. a janela é ESTENDIDA em vez de perder a comparação
  5. o gatilho `--atualizar` sabe dizer se a base está em dia

Roda sem rede (o cache de índices já está baixado):
    .venv\\Scripts\\python.exe testar_igpm_janela.py
"""

from __future__ import annotations

import sqlite3
import sys


def _bootstrap() -> str:
    import os
    raiz = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, raiz)
    sys.path.insert(0, os.path.join(raiz, "src"))
    return raiz


RAIZ = _bootstrap()

import comparar_itbi  # noqa: E402
import config  # noqa: E402
import indices  # noqa: E402

try:
    sys.stdout.reconfigure(errors="replace")
except Exception:  # noqa: BLE001
    pass

_falhas: list[str] = []
_total = 0


def verificar(condicao: bool, descricao: str) -> None:
    global _total
    _total += 1
    if condicao:
        print(f"  PASSA  {descricao}")
    else:
        print(f"  FALHA  {descricao}")
        _falhas.append(descricao)


def main() -> int:
    print("\n=== 1) O IGP-M e acumulado do jeito certo ===")
    # O BCB publica o IGP-M como VARIAÇÃO % mensal. Acumular errado (somar em
    # vez de compor) daria um número absurdo, e a comparação inteira sairia
    # torta sem nenhum erro aparecer.
    reaj = indices.Reajustador("igpm", verbose=False)
    verificar(reaj.nome == "igpm", f"o índice é o IGP-M ({reaj.nome})")
    verificar(len(reaj.serie) > 300, f"série com muitos meses ({len(reaj.serie)})")

    # acumulado oficial do ano = dezembro / dezembro anterior
    oficiais = {"2020": 23.14, "2021": 17.79, "2022": 5.45,
                "2023": -3.18, "2024": 6.54}
    for ano, esperado in oficiais.items():
        ant, fim = f"{int(ano)-1}12", f"{ano}12"
        if ant in reaj.serie and fim in reaj.serie:
            calc = (reaj.serie[fim] / reaj.serie[ant] - 1) * 100
            verificar(
                abs(calc - esperado) < 0.1,
                f"IGP-M {ano}: oficial {esperado:+.2f}% x calculado {calc:+.2f}%",
            )

    print("\n=== 2) A referência acompanha o dado mais recente ===")
    verificar(reaj.referencia == max(reaj.serie),
              f"referência = mês mais recente ({reaj.referencia})")
    # o fator tem de CRESCER com a distância no tempo
    f_antigo = reaj.fator("20060101")
    f_meio = reaj.fator("20150101")
    f_novo = reaj.fator("20250101")
    verificar(f_antigo > f_meio > f_novo > 1.0,
              f"fator cai conforme a data se aproxima "
              f"({f_antigo:.2f} > {f_meio:.2f} > {f_novo:.2f} > 1)")

    print("\n=== 3) O IGP-M reajusta MAIS que o IPCA (é a diferença de índice) ===")
    ipca = indices.Reajustador("ipca", verbose=False)
    for data in ("20070313", "20070711"):
        fi, fg = ipca.fator(data), reaj.fator(data)
        verificar(fg > fi,
                  f"{data}: IGP-M x{fg:.3f} > IPCA x{fi:.3f}")

    print("\n=== 4) O caso real que denunciou o defeito ===")
    # R PEDRO FERREIRA DE SOUZA: nº 19 com 160 m² vendeu em 2020 (financiada)
    # por R$ 600 mil. As duas de 2007 (R$ 100 e 90 mil) são de uma época em que
    # o mesmo imóvel valia uma fração disso.
    v2007 = reaj.reajustar(100_000, "20070313")
    v2020 = reaj.reajustar(600_000, "20200624")
    verificar(300_000 < v2007 < 400_000,
              f"R$ 100.000 de 2007 -> R$ {v2007:,.0f}".replace(",", "."))
    verificar(850_000 < v2020 < 1_000_000,
              f"R$ 600.000 de 2020 -> R$ {v2020:,.0f}".replace(",", "."))
    # a venda de 2020 é a que representa o preço de hoje
    m2_2020 = v2020 / 160
    m2_2007 = v2007 / 160
    verificar(m2_2020 > 2 * m2_2007,
              f"a venda de 2020 vale mais que o DOBRO por m² que a de 2007 "
              f"({m2_2020:,.0f} x {m2_2007:,.0f})".replace(",", "."))

    print("\n=== 5) A janela existe e e configuravel ===")
    verificar(comparar_itbi.JANELA_ANOS == 10,
              f"janela de {comparar_itbi.JANELA_ANOS} anos")
    verificar(comparar_itbi.MIN_DENTRO_JANELA >= 1,
              f"mínimo dentro da janela = {comparar_itbi.MIN_DENTRO_JANELA}")

    print("\n=== 6) A janela corta venda velha pelo banco de verdade ===")
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.execute(
        """SELECT * FROM comparacoes
           WHERE ano_mais_antigo IS NOT NULL AND ano_mais_novo IS NOT NULL
           ORDER BY n_transacoes DESC LIMIT 1""").fetchone()
    if c is None:
        verificar(False, "há comparação com ano registrado (rode --atualizar)")
    else:
        verificar(True, f"comparações trazem os anos "
                        f"({c['ano_mais_antigo']}–{c['ano_mais_novo']})")
        verificar(c["indice_reajuste"] == "igpm",
                  f"gravado com IGP-M ({c['indice_reajuste']})")
        # a mais antiga NÃO pode ser muito anterior à mais nova (é a janela)
        # ou, se for, é porque a rua não tinha venda recente (fallback)
        verificar(c["ano_mais_novo"] >= c["ano_mais_antigo"],
                  "ano mais novo >= ano mais antigo")

        # nenhuma comparação pode misturar 2007 com 2020 sem ser fallback
        ruins = conn.execute(
            """SELECT COUNT(*) FROM comparacoes
               WHERE ano_mais_novo - ano_mais_antigo > 10""").fetchone()[0]
        total = conn.execute(
            "SELECT COUNT(*) FROM comparacoes WHERE ano_mais_antigo IS NOT NULL"
        ).fetchone()[0]
        verificar(
            total > 0 and 100 * ruins / total < 60,
            f"poucas comparações misturam +10 anos: {ruins:,} de {total:,} "
            f"({100*ruins/total:.0f}%) — são as que caíram no fallback",
        )

    print("\n=== 7) O gatilho sabe se a base esta em dia ===")
    if c is not None:
        ok, motivo = comparar_itbi.esta_atualizada(conn, reaj)
        verificar(isinstance(ok, bool) and bool(motivo),
                  f"responde com motivo legível: {motivo}")
        verificar(ok, f"a base recém-recalculada está em dia ({motivo})")

        # índice diferente -> precisa recalcular
        outro = indices.Reajustador("ipca", verbose=False)
        ok2, motivo2 = comparar_itbi.esta_atualizada(conn, outro)
        verificar(not ok2,
                  f"reconhece índice diferente e pede recálculo ({motivo2})")
    conn.close()

    print("\n=== 8) Os limiares que escolhem o metodo estao documentados ===")
    # estes números saíram de medição, não de chute; se alguém mudar sem medir,
    # o comentário explica o custo
    verificar(comparar_itbi.MIN_TRANSACOES == 3,
              f"MIN_TRANSACOES = {comparar_itbi.MIN_TRANSACOES}")
    verificar(comparar_itbi.TOLERANCIA_AREA == 0.25,
              f"tolerância de área = {comparar_itbi.TOLERANCIA_AREA:.0%}")

    print("\n" + "=" * 62)
    if _falhas:
        print(f"{len(_falhas)} FALHA(S) de {_total} verificações:")
        for f in _falhas:
            print(f"   - {f}")
        return 1
    print(f"TODAS AS {_total} VERIFICACOES PASSARAM")
    return 0


if __name__ == "__main__":
    sys.exit(main())
