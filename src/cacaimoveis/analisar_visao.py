"""Analisa as fotos dos anúncios com um modelo de visão.

Envia as fotos de cada anúncio ao Claude Code CLI (padrão) ou à DeepSeek e
grava, no banco, parâmetros que o texto do anúncio quase nunca informa:
quintal com terra, árvores, se é cimentado, iluminação, arejamento, cuidado,
janelas grandes, fachada, piso, cômodos visíveis, extras e problemas.

Provedores (`--provedor`, ou `CACA_VISAO`):
    claude   (padrão) o Claude Code CLI logado nesta máquina; sem custo por
             token. Faça login uma vez rodando o `claude` num terminal.
    deepseek a API da DeepSeek; precisa de DEEPSEEK_API_KEY (ambiente ou
             .env) e respeita o teto de gasto em reais (`--teto`).

Uso:
    caca-visao                         # anúncios ainda não analisados
    caca-visao --limite 5              # só 5 (para testar)
    caca-visao --provedor deepseek     # pela API da DeepSeek
    caca-visao --refazer               # ignora o que já foi analisado
    caca-visao --bairro "Lapa"         # só um bairro

O progresso fica salvo em `foto_ok`, então pode interromper com Ctrl+C e
rodar de novo depois — ele retoma de onde parou.
"""

from __future__ import annotations

import argparse
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from cacaimoveis import config, logs
from cacaimoveis.progresso import Barra
from cacaimoveis.storage import DB
from cacaimoveis.visao import AnaliseFoto, analisar_anuncio, provedor_pronto, verificar_login_claude

log = logs.obter(__name__)

# ---------------------------------------------------------------------------
# Custo em tokens, MEDIDO na API (2026-10-03) — não estimado.
# ---------------------------------------------------------------------------
TOK_FOTO = {"low": 203, "original": 458}
TOK_PROMPT = 907
TOK_SAIDA = 265
# Preços `deepseek-flash` em US$/1M. Off-peak é metade do peak; usamos o
# OFF-PEAK como referência para o TETO (é o que a automação noturna paga) e
# mostramos o peak como pior caso na estimativa.
USD_ENTRADA_OFF = 0.15 / 1e6
USD_SAIDA_OFF = 0.60 / 1e6
USD_ENTRADA_PEAK = 0.30 / 1e6
USD_SAIDA_PEAK = 1.20 / 1e6
BRL = 5.42


def _e_peak(quando=None) -> bool:
    """Estamos no horário CARO da DeepSeek?

    Medido: peak = 01–04h e 06–10h UTC, de segunda a sexta. Fora disso a
    entrada e a saída custam METADE. A diferença é grande: o banco inteiro
    custa R$ 10,82 off-peak e R$ 21,63 peak.

    Serve para o teto acumular pelo preço REAL e não pelo pior caso — senão
    um teto de R$ 20 abortaria faltando R$ 1,63 de trabalho, mesmo rodando
    no horário barato.
    """
    import datetime

    utc = quando or datetime.datetime.now(datetime.UTC)
    if utc.weekday() >= 5:                      # sábado/domingo: sempre barato
        return False
    return (1 <= utc.hour < 4) or (6 <= utc.hour < 10)


# Teto de gasto padrão da DeepSeek, em reais. Era 20; uma rodada normal custa
# centavos a poucos reais (o lote de 620 chamadas custou ~R$ 2,5) e a base
# INTEIRA ~R$ 11, então 10 já protege de um engano sem atrapalhar o dia a dia.
TETO_PADRAO_BRL = 10.0
# Mostra o gasto acumulado a cada tantas análises concluídas
AVISO_DE_GASTO_A_CADA = 25

# Falhas de TRANSPORTE seguidas (login, rede, timeout) que abortam o lote. O
# anúncio não tem culpa e a próxima falha seria igual: insistir só gasta tempo.
FALHAS_SEGUIDAS_MAX = 5


class _TetoAtingido(Exception):
    """Levantada para parar o envio quando o teto de gasto e' atingido.

    Fica no escopo do MODULO (nao dentro de `main`) porque o `except` precisa
    alcancar a mesma classe: definida dentro da funcao, o `except` de fora
    nao a pegaria.
    """


def _custo_brl(n_fotos: int, n_ads: int, detalhe: str,
               peak: bool = False) -> float:
    """Custo em reais de analisar `n_ads` anúncios com `n_fotos` fotos.

    A conta é a mesma usada na estimativa e no teto, de propósito: se fossem
    duas fórmulas diferentes, o teto poderia ser ultrapassado sem o script
    perceber.
    """
    tf = TOK_FOTO.get(detalhe, TOK_FOTO["original"])
    ent = n_fotos * tf + n_ads * TOK_PROMPT
    sai = n_ads * TOK_SAIDA
    usd = (ent * (USD_ENTRADA_PEAK if peak else USD_ENTRADA_OFF)
           + sai * (USD_SAIDA_PEAK if peak else USD_SAIDA_OFF))
    return usd * BRL


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


def main() -> int:
    parser = argparse.ArgumentParser(description="Análise visual das fotos")
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
    parser.add_argument("--teto", type=float, default=TETO_PADRAO_BRL, metavar="R$",
                        help="(deepseek) teto de gasto em REAIS; aborta ao atingir (0 = sem teto)")
    parser.add_argument("--sim", action="store_true",
                        help="(deepseek) não pergunta antes de gastar")
    parser.add_argument("--provedor", choices=["claude", "deepseek"],
                        default=config.VISAO_PROVEDOR,
                        help=f"quem analisa as fotos (padrão: {config.VISAO_PROVEDOR})")
    args = parser.parse_args()
    deepseek = args.provedor == "deepseek"

    # Cópias de imóveis já analisados herdam o resultado: custa zero e não
    # depende de login nem de chave, então vem ANTES de checar o provedor.
    if not args.sem_agrupar and not args.refazer:
        db_herda = DB()
        try:
            herdadas = db_herda.herdar_analise_visual()
        finally:
            db_herda.close()
        if herdadas:
            print(f"Herdaram a análise de uma cópia já analisada: {herdadas} anúncios (sem chamada)")

    falta = provedor_pronto(args.provedor)
    if falta:
        print(falta)
        return 1
    if not deepseek:
        sem_login = verificar_login_claude()
        if sem_login:
            print(f"Análise das fotos não iniciada: {sem_login}")
            return 1

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


    print(f"Banco        : {config.DB_PATH} ({db.total()} anúncios)")
    if deepseek:
        print(f"Modelo       : {config.VISAO_MODELO}  (detail={config.VISAO_DETALHE})")
    else:
        print(f"Modelo       : Claude Code CLI ({config.VISAO_CLAUDE_MODELO}), "
              f"até {config.VISAO_CLAUDE_MAX_FOTOS} fotos por anúncio")
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
        return 0

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
    #
    # Só contam as fotos dos anúncios que REALMENTE vão à API (o principal de
    # cada grupo + os sem grupo). Somar as fotos de todos os pendentes
    # superestimava: medido, dava 27.339 fotos onde seriam 22.701 — quase 20%
    # a mais, e a diferença aparecia justamente no teto, que abortaria cedo
    # sem necessidade.
    # -----------------------------------------------------------------------
    tf = TOK_FOTO.get(config.VISAO_DETALHE, TOK_FOTO["original"])
    # o Claude Code usa a assinatura: não há conta por token nem teto em reais
    teto_brl = args.teto if deepseek else 0.0
    if not args.sem_agrupar:
        _ids_que_vai = {
            r["url"] for r in pendentes
            if principal_por_dup.get(r["url"], r["url"]) == r["url"]
        }
    else:
        _ids_que_vai = {r["url"] for r in pendentes}
    n_fotos_total = 0
    for r in pendentes:
        if r["url"] not in _ids_que_vai:
            continue        # é cópia: herda a análise, não envia foto
        fotos = db.fotos_do_anuncio(r["url"])
        limite = config.VISAO_MAX_FOTOS
        n_fotos_total += len(fotos) if limite <= 0 else min(len(fotos), limite)
    n_chamadas = len(_ids_que_vai)
    tok_entrada = n_chamadas * TOK_PROMPT + n_fotos_total * tf
    tok_saida = n_chamadas * TOK_SAIDA
    custo_peak = (tok_entrada * USD_ENTRADA_PEAK + tok_saida * USD_SAIDA_PEAK) * BRL
    custo_off = (tok_entrada * USD_ENTRADA_OFF + tok_saida * USD_SAIDA_OFF) * BRL
    print(f"Chamadas     : {n_chamadas:,d} ({n_fotos_total:,d} fotos em disco)")
    if deepseek:
        print(f"Custo        : R$ {custo_off:.2f} off-peak · R$ {custo_peak:.2f} peak "
              f"(detail={config.VISAO_DETALHE}, ~{tf} tok/foto)")
    if teto_brl > 0:
        horario = "PEAK (dobro)" if _e_peak() else "off-peak (metade)"
        print(f"Teto         : R$ {teto_brl:.2f}   horário agora: {horario}")
        if custo_off > teto_brl:
            print("\n*** ABORTADO: a estimativa off-peak já passa do teto. ***")
            print("    Reduza o escopo (--limite/--bairro) ou aumente --teto.\n")
            db.close()
            return 1
    # Gasto de verdade: mostra a estimativa e pergunta, a menos que o chamador
    # (ex.: caca-atualizar) já tenha autorizado com --sim, ou não haja teclado.
    if deepseek and not args.sim and custo_off >= 0.50 and sys.stdin.isatty():
        horario_txt = "pico" if _e_peak() else "fora do pico"
        resp = input(f"\nIsto vai chamar a DeepSeek ({n_chamadas} chamadas) e custar cerca de "
                     f"R$ {custo_peak if _e_peak() else custo_off:.2f} ({horario_txt}), com teto de "
                     f"R$ {teto_brl:.2f}. Continuar? [s/N] ").strip().lower()
        if resp not in ("s", "sim", "y", "yes"):
            print("Cancelado. Nada foi gasto.")
            db.close()
            return 0
    print()

    n_ok = n_falha = falhas_seguidas = 0
    destaques_quintal = 0
    reaproveitados = 0
    gasto_brl = 0.0     # acumulado em tempo real, pelo que foi REALMENTE enviado
    t0 = time.time()

    # -----------------------------------------------------------------------
    # TETO DE GASTO. O acumulado usa as fotos de cada anúncio, não a média:
    # a cauda é longa (há anúncio com 102 fotos), e uma média subestimaria a
    # conta justamente nos últimos.
    #
    # O acumulado usa o preço do HORÁRIO em vigor (`_e_peak`): peak custa o
    # dobro do off-peak, e o banco inteiro vai de R$ 10,82 a R$ 21,63 conforme
    # a hora. Acumular sempre no pior caso faria um teto de R$ 20 abortar
    # faltando R$ 1,63 mesmo rodando no horário barato; acumular sempre no
    # melhor caso poderia estourar o teto se a execução atravessasse o peak.
    # `_custo_brl` é a MESMA função da estimativa: se fossem duas fórmulas, o
    # teto poderia ser furado sem o script perceber.
    # -----------------------------------------------------------------------
    def _soma_gasto(row, fotos, ok: bool) -> None:
        nonlocal gasto_brl
        if not ok or teto_brl <= 0:
            return
        limite = config.VISAO_MAX_FOTOS
        n = len(fotos) if limite <= 0 else min(len(fotos), limite)
        # o preço é reavaliado a CADA anúncio: uma execução de 30 min pode
        # cruzar a fronteira do horário caro
        gasto_brl += _custo_brl(n, 1, config.VISAO_DETALHE, peak=_e_peak())
        feitas = n_ok + n_falha
        if feitas and feitas % AVISO_DE_GASTO_A_CADA == 0:
            barra.escrever(f"  [gasto] ~R$ {gasto_brl:.2f} de R$ {teto_brl:.2f} "
                           f"(teto) · {feitas} de {len(pendentes)} anúncios")

    def _verifica_teto() -> None:
        if teto_brl > 0 and gasto_brl >= teto_brl:
            raise _TetoAtingido(
                f"teto de R$ {teto_brl:.2f} atingido (gasto ~R$ {gasto_brl:.2f})"
            )

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
            row["url"], fotos, max_fotos=args.max_fotos or None, verbose=False,
            provedor=args.provedor,
        )
        if a.transitorio:
            # login/rede/timeout: não grava "falhou", senão a próxima rodada
            # pularia o anúncio como se a culpa fosse dele
            return a
        meu_db = DB(migrar=False)
        try:
            meu_db.salvar_analise_visual(a)
        finally:
            meu_db.close()
        return a

    resultados: dict[str, AnaliseFoto] = {}

    def _registra(row, a) -> None:
        """Atualiza contadores e, se houver algo marcante, escreve na tela."""
        nonlocal n_ok, n_falha, destaques_quintal, falhas_seguidas
        resultados[row["url"]] = a
        if a.ok:
            falhas_seguidas = 0
        elif a.transitorio:
            falhas_seguidas += 1
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

    paralelo = config.VISAO_PARALELO if deepseek else config.VISAO_CLAUDE_PARALELO
    workers = max(1, min(paralelo, len(tarefas_fotos)))
    if args.verbose:
        print(f"[verbose] {len(tarefas_fotos)} chamada(s) à API, {workers} em paralelo")

    interrompido_por_teto = False
    abortado = False
    try:
        if workers == 1:
            for row, fotos in tarefas_fotos:
                _verifica_teto()
                a = _analisa_e_salva(row, fotos)
                _soma_gasto(row, fotos, a.ok)
                _registra(row, a)
                barra.passo(a.ok)
                if falhas_seguidas >= FALHAS_SEGUIDAS_MAX:
                    abortado = True
                    break
                if deepseek and config.VISAO_DELAY_S:
                    time.sleep(config.VISAO_DELAY_S)
        else:
            # No modo paralelo não dá para "parar no meio": as tarefas já
            # estão todas submetidas. Então o teto é checado a cada resultado
            # que chega e, ao estourar, o pool é encerrado sem esperar o
            # restante (cancel_futures). O que já foi enviado foi cobrado —
            # por isso o teto vale como FREIO, não como garantia exata.
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futuros = {
                    pool.submit(_analisa_e_salva, row, fotos): (row, fotos)
                    for row, fotos in tarefas_fotos
                }
                try:
                    for fut in as_completed(futuros):
                        row, fotos = futuros[fut]
                        try:
                            a = fut.result()
                        except Exception as e:  # noqa: BLE001
                            log.warning("erro tratado, a execução segue: %s", e, exc_info=True)
                            a = AnaliseFoto(url=row["url"], ok=False, erro=str(e)[:300])
                        _soma_gasto(row, fotos, a.ok)
                        _registra(row, a)
                        barra.passo(a.ok)
                        if falhas_seguidas >= FALHAS_SEGUIDAS_MAX:
                            abortado = True
                            for f in futuros:
                                f.cancel()
                            break
                        if teto_brl > 0 and gasto_brl >= teto_brl:
                            interrompido_por_teto = True
                            pendentes_no_pool = [
                                f for f in futuros if not f.done()]
                            for f in pendentes_no_pool:
                                f.cancel()
                            break
                except _TetoAtingido:
                    interrompido_por_teto = True

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

    except _TetoAtingido as e:
        interrompido_por_teto = True
        barra.escrever(f"  [TETO] {e}")
    except KeyboardInterrupt:
        barra.encerrar()
        print("Interrompido. Progresso salvo — rode de novo para continuar.")
        db.close()
        return 130

    barra.encerrar()

    decorrido = time.time() - t0
    print(f"\n{'=' * 58}")
    print(f"Analisados com sucesso : {n_ok}")
    print(f"Falhas                 : {n_falha}")
    print(f"Reaproveitados (dup)   : {reaproveitados}")
    print(f"Com quintal de terra   : {destaques_quintal}")
    print(f"Tempo                  : {decorrido:.0f}s")
    if deepseek:
        print(f"Gasto (no horário)     : R$ {gasto_brl:.2f}"
              + (f"  de R$ {teto_brl:.2f}" if teto_brl > 0 else ""))
    if abortado:
        print("")
        print(f"*** ABORTADO: {FALHAS_SEGUIDAS_MAX} falhas seguidas de login/rede ***")
        print("    Nada foi gravado como falha: o que faltou será tentado na próxima rodada.")
    if interrompido_por_teto:
        print("")
        print("*** PARADO PELO TETO DE GASTO ***")
        print("    O que já foi analisado está salvo. Para continuar depois,")
        print("    rode de novo — ele retoma de onde parou — com --teto maior.")
    if pendentes:
        print(f"Ritmo                  : {decorrido / len(pendentes):.1f}s por anúncio")
    print(f"Restantes              : {len(db.sem_analise_visual(args.incluir_falhas))}")
    db.close()
    # tudo falhou = algo está errado (login, chave, rede): sinaliza para quem
    # chamou (o `caca-atualizar`) em vez de seguir como se tivesse dado certo
    return 1 if (n_falha and not n_ok) or abortado else 0


if __name__ == "__main__":
    sys.exit(main())
