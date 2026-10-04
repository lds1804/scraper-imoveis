"""Analisa as fotos dos anúncios com o modelo de visão da DeepSeek.

Envia as fotos de cada anúncio para o `deepseek-flash` e grava, no banco,
parâmetros que o texto do anúncio quase nunca informa:
quintal com terra, árvores, se é cimentado, iluminação, arejamento, cuidado,
janelas grandes, fachada, piso, cômodos visíveis, extras e problemas.

Antes de rodar, exporte a chave (o código NUNCA guarda a chave):
    PowerShell:  $env:DEEPSEEK_API_KEY = "sk-..."
    permanente:  setx DEEPSEEK_API_KEY "sk-..."   (reabra o terminal)
    ou crie um arquivo .env com  DEEPSEEK_API_KEY=sk-...

Uso:
    python analisar_visual.py                 # anúncios ainda não analisados
    python analisar_visual.py --limite 5      # só 5 (para testar)
    python analisar_visual.py --refazer       # ignora o que já foi analisado
    python analisar_visual.py --bairro "Lapa" # só um bairro
    python analisar_visual.py --sem-fotos     # filtra por outros critérios

O progresso fica salvo em `foto_ok`, então pode interromper com Ctrl+C e
rodar de novo depois — ele retoma de onde parou.
"""

from __future__ import annotations

import argparse
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import config
from visao import AnaliseFoto, analisar_anuncio, obter_api_key, tem_api_key
from progresso import Barra
from storage import DB


def _copiar_analise(origem: AnaliseFoto, nova_url: str) -> AnaliseFoto:
    """Copia o resultado de um anúncio duplicado para outro (mesmo imóvel).

    As fotos são as mesmas (foi assim que o grupo foi detectado), então não
    faz sentido gastar tokens reenviando. A URL é a única coisa que muda.
    """
    from dataclasses import replace

    return replace(origem, url=nova_url, analise_reaproveitada=True)


def _fmt_nota(v) -> str:
    return f"{v}/5" if v is not None else "-"


def _fmt_sim(v) -> str:
    if v is True:
        return "sim"
    if v is False:
        return "nao"
    return "-"


def _resumo_ganhos(a) -> str:
    """Monta uma linha curta com o que a análise trouxe de relevante."""
    partes: list[str] = []

    if a.tem_quintal:
        partes.append(f"quintal({a.piso_quintal or '?'})")
    if a.quintal_terra:
        partes.append("TERRA")
    if a.arvores:
        partes.append("arvores")
    if a.parece_cimentado:
        partes.append("cimentado")
    if a.cuidado_nota is not None:
        partes.append(f"cuidado {a.cuidado_nota}/5")
    if a.arejamento_nota is not None:
        partes.append(f"arej {a.arejamento_nota}/5")
    if a.extras:
        partes.append("+".join(a.extras[:3]))
    if a.problemas:
        partes.append("PROBLEMA:" + "+".join(a.problemas[:2]))

    return " | ".join(partes) if partes else "sem destaques"


def main() -> None:
    parser = argparse.ArgumentParser(description="Análise visual das fotos (DeepSeek)")
    parser.add_argument("--limite", type=int, default=0, help="máximo de anúncios (0 = todos)")
    parser.add_argument("--refazer", action="store_true", help="reanalisa tudo")
    parser.add_argument("--incluir-falhas", action="store_true", help="refaz os que falharam")
    parser.add_argument("--bairro", default="",
                        help="filtra por bairro (parte do nome; use '|' para vários)")
    parser.add_argument("--max-fotos", type=int, default=0, help="fotos por anúncio")
    parser.add_argument("--so-com-fotos", action="store_true", default=True,
                        help="apenas anúncios com fotos (padrão)")
    parser.add_argument("--sem-agrupar", action="store_true",
                        help="não reaproveita duplicatas (analisa cada anúncio)")
    parser.add_argument("--verbose", action="store_true", help="mostra cada requisição")
    args = parser.parse_args()

    if not tem_api_key():
        try:
            obter_api_key()
        except RuntimeError as e:
            print(e)
            sys.exit(1)

    db = DB()

    if args.refazer:
        print("[refazer] limpando análise anterior...")
        db.conn.execute(
            "UPDATE anuncios SET foto_ok = NULL, foto_tem_quintal = NULL, "
            "foto_piso_quintal = NULL, foto_quintal_terra = NULL, "
            "foto_cimentado = NULL, foto_arvores = NULL, foto_area_externa = NULL, "
            "foto_vegetacao = NULL, foto_iluminacao = NULL, foto_arejamento = NULL, "
            "foto_cuidado = NULL, foto_janelas_grandes = NULL, foto_reformado = NULL, "
            "foto_planta_baixa = NULL, foto_fachada = NULL, foto_piso = NULL, "
            "foto_comodos = NULL, foto_extras = NULL, foto_problemas = NULL, "
            "foto_resumo = NULL, foto_confianca = NULL, foto_analisada_em = NULL"
        )
        db.conn.commit()

    pendentes = db.sem_analise_visual(incluir_falhas=args.incluir_falhas)

    if args.bairro:
        # '|' separa vários bairros: --bairro "mangalot|domitila|sao domingos"
        alvos = [b.strip().lower() for b in args.bairro.split("|") if b.strip()]
        pendentes = [
            r for r in pendentes
            if any(a in (r["bairro"] or "").lower() for a in alvos)
        ]

    # separa os que têm fotos dos que não têm
    com_fotos, sem_fotos = [], []
    for r in pendentes:
        (com_fotos if db.fotos_do_anuncio(r["url"]) else sem_fotos).append(r)

    if args.so_com_fotos:
        pendentes = com_fotos
    else:
        pendentes = com_fotos + sem_fotos

    if args.limite:
        pendentes = pendentes[: args.limite]

    # -----------------------------------------------------------------------
    # Economia de tokens: anúncios DUPLICADOS (mesmo imóvel, outra imobiliária)
    # compartilham as fotos. Analisamos só o "principal" de cada grupo e
    # copiamos o resultado para os demais — mesma análise, um envio a menos.
    # -----------------------------------------------------------------------
    grupos_dup: dict[int, list[str]] = {}
    principal_por_dup: dict[str, str] = {}
    if not args.sem_agrupar:
        for g in db.duplicatas():
            membros = [m["url"] for m in g["membros"]]
            grupos_dup[g["grupo"]] = membros
            principal = next(
                (m["url"] for m in g["membros"] if m.get("dup_melhor")), membros[0]
            )
            for u in membros:
                principal_por_dup[u] = principal

    urls_pendentes = {r["url"] for r in pendentes}

    print(f"Banco        : {config.DB_PATH} ({db.total()} anúncios)")
    print(f"Modelo       : {config.VISAO_MODELO}  (detail={config.VISAO_DETALHE})")
    print(f"A analisar   : {len(pendentes)}")
    if grupos_dup:
        poupados = sum(
            1 for r in pendentes
            if principal_por_dup.get(r["url"], r["url"]) != r["url"]
        )
        print(f"Duplicatas   : {len(grupos_dup)} grupos ({poupados} reaproveitam o envio)")
    if sem_fotos:
        print(f"  (sem fotos : {len(sem_fotos)} — não dá para analisar)")
    if not pendentes:
        print("\nNada a fazer. Use --refazer para reprocessar tudo.")
        db.close()
        return

    # -----------------------------------------------------------------------
    # ESTIMATIVA DE CUSTO — com tokens MEDIDOS na API, não chutados.
    #
    # Medido em 2026-10-03: "low" = 203 tokens/imagem, "original" = 458;
    # prompt ~907, saída ~265. Preços `deepseek-flash` off-peak: entrada
    # US$ 0,15/1M e saída US$ 0,60/1M. Off-peak é o dobro do peak, então
    # mostramos o pior caso (peak) como teto.
    #
    # A contagem de FOTOS vem do disco (o que será enviado de fato), não da
    # constante de teto — com `VISAO_MAX_FOTOS = 0` (todas) a constante não
    # diria nada.
    # -----------------------------------------------------------------------
    tf = {"low": 203, "original": 458}.get(config.VISAO_DETALHE, 458)
    n_fotos_total = 0
    for r in pendentes:
        fotos = db.fotos_do_anuncio(r["url"])
        limite = config.VISAO_MAX_FOTOS
        n_fotos_total += len(fotos) if limite <= 0 else min(len(fotos), limite)
    tok_entrada = len(pendentes) * 907 + n_fotos_total * tf
    tok_saida = len(pendentes) * 265
    custo_peak = tok_entrada / 1e6 * 0.30 + tok_saida / 1e6 * 1.20
    custo_off = tok_entrada / 1e6 * 0.15 + tok_saida / 1e6 * 0.60
    print(f"Fotos        : {n_fotos_total:,d} em {len(pendentes):,d} anúncios "
          f"({n_fotos_total/max(len(pendentes),1):.1f}/anúncio)")
    print(f"Custo        : US$ {custo_off:.2f} off-peak · US$ {custo_peak:.2f} peak "
          f"(detail={config.VISAO_DETALHE}, ~{tf} tok/foto)")
    print()

    n_ok = n_falha = 0
    destaques_quintal = 0
    reaproveitados = 0
    t0 = time.time()

    # -----------------------------------------------------------------------
    # Quem realmente precisa de uma chamada à API: só o "principal" de cada
    # grupo de duplicatas. Os outros membros recebem a análise copiada.
    # Se o principal não estiver nesta rodada (já analisado antes), o membro
    # é tratado como principal — senão ele ficaria sem análise nenhuma.
    # -----------------------------------------------------------------------
    ids_principais = {
        r["url"]
        for r in pendentes
        if principal_por_dup.get(r["url"], r["url"]) == r["url"]
    }
    tarefas: list = []
    copias: dict[str, list] = {}
    for r in pendentes:
        principal = principal_por_dup.get(r["url"], r["url"])
        if r["url"] == principal or principal not in ids_principais:
            tarefas.append(r)
        else:
            copias.setdefault(principal, []).append(r)

    # As fotos são lidas aqui, na thread principal: a conexão do sqlite3 não
    # pode ser usada de dentro de outras threads.
    tarefas_fotos = [(r, db.fotos_do_anuncio(r["url"])) for r in tarefas]

    barra = Barra(len(pendentes))
    barra.desenhar(forcar=True)

    def _analisa_e_salva(row, fotos):
        """Analisa um anúncio e grava o resultado (roda em thread separada)."""
        a = analisar_anuncio(
            row["url"], fotos, max_fotos=args.max_fotos or None, verbose=False
        )
        meu_db = DB(migrar=False)
        try:
            meu_db.salvar_analise_visual(a)
        finally:
            meu_db.close()
        return a

    resultados: dict[str, AnaliseFoto] = {}

    def _registra(row, a) -> None:
        """Atualiza contadores e, se houver algo marcante, escreve na tela."""
        nonlocal n_ok, n_falha, destaques_quintal
        resultados[row["url"]] = a
        if a.ok:
            n_ok += 1
            if a.quintal_terra:
                destaques_quintal += 1
        else:
            n_falha += 1
            barra.escrever(f"  [falhou] {(row['titulo'] or row['url'])[:44]} :: {a.erro[:90]}")
            return

        # só interrompe a barra para o que é realmente interessante
        if a.problemas:
            barra.escrever(
                f"  [problema] {(row['titulo'] or '')[:38]:40s} "
                f"{'+'.join(a.problemas[:3])}"
            )
        elif a.tem_quintal and a.piso_quintal in ("terra", "grama"):
            barra.escrever(
                f"  [quintal]  {(row['titulo'] or '')[:38]:40s} {a.piso_quintal}"
            )

    workers = max(1, min(config.VISAO_PARALELO, len(tarefas_fotos)))
    if args.verbose:
        print(f"[verbose] {len(tarefas_fotos)} chamada(s) à API, {workers} em paralelo")

    try:
        if workers == 1:
            for row, fotos in tarefas_fotos:
                a = _analisa_e_salva(row, fotos)
                _registra(row, a)
                barra.passo(a.ok)
                if config.VISAO_DELAY_S:
                    time.sleep(config.VISAO_DELAY_S)
        else:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futuros = {
                    pool.submit(_analisa_e_salva, row, fotos): row
                    for row, fotos in tarefas_fotos
                }
                for fut in as_completed(futuros):
                    row = futuros[fut]
                    try:
                        a = fut.result()
                    except Exception as e:  # noqa: BLE001
                        a = AnaliseFoto(url=row["url"], ok=False, erro=str(e)[:300])
                    _registra(row, a)
                    barra.passo(a.ok)

        # duplicatas: copia a análise do principal (não gasta token)
        for principal, membros in copias.items():
            base = resultados.get(principal)
            if base is None or not base.ok:
                continue
            for row in membros:
                copia = _copiar_analise(base, row["url"])
                db.salvar_analise_visual(copia)
                n_ok += 1
                reaproveitados += 1
                if copia.quintal_terra:
                    destaques_quintal += 1
                barra.passo(True, reaproveitado=True)

    except KeyboardInterrupt:
        barra.encerrar()
        print("Interrompido. Progresso salvo — rode de novo para continuar.")
        db.close()
        return

    barra.encerrar()

    decorrido = time.time() - t0
    print(f"\n{'=' * 58}")
    print(f"Analisados com sucesso : {n_ok}")
    print(f"Falhas                 : {n_falha}")
    print(f"Reaproveitados (dup)   : {reaproveitados}")
    print(f"Com quintal de terra   : {destaques_quintal}")
    print(f"Tempo                  : {decorrido:.0f}s")
    if pendentes:
        print(f"Ritmo                  : {decorrido / len(pendentes):.1f}s por anúncio")
    print(f"Restantes              : {len(db.sem_analise_visual(args.incluir_falhas))}")
    db.close()


if __name__ == "__main__":
    main()
