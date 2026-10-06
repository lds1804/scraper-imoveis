"""Atualiza tudo: anúncios novos, fotos, análise visual e referências de valor.

Por que existe: o pipeline tem uma dúzia de etapas, cada uma com o seu
comando e as suas flags, e a ordem importa (a análise visual precisa das
fotos; a comparação com o ITBI precisa do endereço herdado das cópias). Este
script encadeia tudo, mostra o que está fazendo e, no fim, um resumo do que
deu certo — e segue adiante quando uma etapa falha, porque as seguintes
ainda valem para os dados que já estão no banco.

Etapas (na ordem):

  diárias
    backup       cópia consistente do banco (caca-backup), mantém as 3 últimas
    coleta       ZAP, QuintoAndar e OLX (o Imovelweb só com --imovelweb)
    fotos        baixa as fotos de anúncios que ficaram sem nenhuma
    duplicatas   agrupa o mesmo imóvel anunciado por várias imobiliárias
    endereco     herda número e CEP entre cópias do mesmo imóvel
    visao        análise das fotos (Claude Code CLI ou DeepSeek)
    itbi         comparação com o preço praticado (só o que é novo ou mudou)
    venal        valor venal de referência (VVR, base do ITBI) estimado
    venal-iptu   valor venal oficial do IPTU (fórmula da lei sobre o cadastro)
    area         área do anúncio contra o cadastro da prefeitura

  mensais (só com --completo)
    itbi-baixar  planilhas novas do ITBI e ingestão no banco
    iptu         cadastro anual do IPTU: ingere o arquivo novo, ou AVISA que o
                 do ano corrente ainda não foi baixado (o site da prefeitura
                 bloqueia download por script; ver `caca-iptu --situacao`)
    ajustes      refaz modelo terreno+construção, razão venal e ruas

Uso:
    caca-atualizar                       # o diário
    caca-atualizar --completo            # inclui as etapas mensais
    caca-atualizar --provedor deepseek   # fotos pela API da DeepSeek
    caca-atualizar --so visao itbi       # só estas etapas
    caca-atualizar --pular coleta        # tudo menos a coleta
    caca-atualizar --listar              # mostra o plano e sai
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from cacaimoveis import config, logs
from cacaimoveis.progresso import _duracao

log = logs.obter(__name__)

TRAVA = config.caminho("dados", "atualizar.trava")


@dataclass
class Etapa:
    nome: str
    descricao: str
    # módulo + argumentos (roda como `python -m cacaimoveis.<modulo>`)...
    comandos: list[list[str]] = field(default_factory=list)
    # ...ou uma função Python que devolve um texto de resumo
    funcao: Callable[[argparse.Namespace], str] | None = None
    mensal: bool = False
    opcional: str = ""   # flag que liga a etapa (ex.: "imovelweb")


# ---------------------------------------------------------------------------
# Etapas que rodam aqui mesmo (sem subprocesso)
# ---------------------------------------------------------------------------
def _backup(args) -> str:
    from cacaimoveis import backup

    destino = backup.copiar("antes_atualizar")
    apagados = backup.rodar(manter=3)
    return f"{os.path.basename(destino)}" + (f" ({len(apagados)} antigo(s) removido(s))"
                                             if apagados else "")


def _fotos(args) -> str:
    from cacaimoveis.storage import DB, baixar_fotos_pendentes

    db = DB()
    try:
        removidos = 0

        def avisar(url, n):
            nonlocal removidos
            if n < 0:
                removidos += 1
                print(f"  saiu do ar  {url[-70:]}", flush=True)
            else:
                print(f"  {n:>3} foto(s)  {url[-70:]}", flush=True)

        n_anuncios, n_fotos = baixar_fotos_pendentes(db, avisar=avisar)
    finally:
        db.close()
    return (f"{n_fotos} foto(s) em {n_anuncios} anúncio(s)"
            + (f"; {removidos} saíram do ar" if removidos else ""))


def _iptu(args) -> str:
    import sqlite3

    from cacaimoveis import iptu

    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        return iptu.rotina(conn)
    finally:
        conn.close()


def montar_etapas(args) -> list[Etapa]:
    coleta = ["coletar_tudo", "--todos"]
    if args.max_paginas:
        coleta += ["--max-paginas", str(args.max_paginas)]
    visao = ["analisar_visao", "--provedor", args.provedor]
    if args.limite_visao:
        visao += ["--limite", str(args.limite_visao)]
    if args.provedor == "deepseek":
        visao += ["--teto", str(args.teto)]

    return [
        Etapa("backup", "cópia do banco antes de mexer", funcao=_backup),
        Etapa("imovelweb", "coleta do Imovelweb (navegador; o Cloudflare às vezes bloqueia)",
              [["main"], ["enriquecer_detalhes"]], opcional="imovelweb"),
        Etapa("coleta", "anúncios novos do ZAP, QuintoAndar e OLX", [coleta]),
        Etapa("fotos", "fotos dos anúncios que ficaram sem nenhuma", funcao=_fotos),
        Etapa("duplicatas", "o mesmo imóvel em várias imobiliárias",
              [["achar_duplicatas", "--marcar", "--quieto"]]),
        Etapa("endereco", "número e CEP herdados entre cópias",
              [["herdar_endereco", "--gravar"]]),
        Etapa("visao", f"análise das fotos ({args.provedor})", [visao]),
        Etapa("itbi-baixar", "planilhas novas do ITBI",
              [["itbi", "--baixar"], ["ingerir_itbi"]], mensal=True),
        Etapa("iptu", "cadastro anual do IPTU (ingere o novo ou avisa para baixar)",
              funcao=_iptu, mensal=True),
        Etapa("ajustes", "modelo terreno+construção, razão venal e ruas",
              [["modelo_casa", "--ajustar"], ["valor_venal", "--ajustar"],
               ["referencia_geosampa", "--ajustar"]], mensal=True),
        # `--atualizar` refaz tudo só se saiu mês novo de IGP-M; o comando
        # seguinte calcula os anúncios novos (ou que mudaram de preço)
        Etapa("itbi", "comparação com o preço praticado (ITBI)",
              [["comparar_itbi", "--atualizar"], ["comparar_itbi", "--amostra", "0"]]),
        Etapa("venal", "valor venal de referência (VVR) estimado",
              [["valor_venal", "--calcular"]]),
        Etapa("venal-iptu", "valor venal oficial do IPTU", [["iptu", "--calcular"]]),
        Etapa("area", "área do anúncio x cadastro da prefeitura",
              [["referencia_geosampa", "--calcular", "--amostra", "0"]]),
    ]


def escolher(etapas: list[Etapa], args) -> list[Etapa]:
    saida = []
    for e in etapas:
        if args.so:
            if e.nome in args.so:
                saida.append(e)
            continue
        if e.nome in (args.pular or []):
            continue
        if e.mensal and not args.completo:
            continue
        if e.opcional and not getattr(args, e.opcional, False):
            continue
        saida.append(e)
    return saida


def _rodar_modulo(cmd: list[str]) -> int:
    linha = [sys.executable, "-m", f"cacaimoveis.{cmd[0]}", *cmd[1:]]
    print(f"  $ caca {' '.join(cmd)}", flush=True)
    # sem capturar a saída: o progresso do filho aparece ao vivo
    return subprocess.run(linha, cwd=config.RAIZ).returncode


def rodar(etapa: Etapa, args) -> tuple[str, str]:
    """Executa uma etapa. Devolve (situação, detalhe)."""
    if etapa.funcao:
        try:
            return "ok", etapa.funcao(args)
        except Exception as e:  # noqa: BLE001
            log.warning("etapa %s falhou: %s", etapa.nome, e, exc_info=True)
            return "falhou", str(e)[:120]
    for cmd in etapa.comandos:
        codigo = _rodar_modulo(cmd)
        if codigo == 130:
            raise KeyboardInterrupt
        if codigo != 0:
            log.warning("etapa %s: `%s` saiu com código %s", etapa.nome, " ".join(cmd), codigo)
            return "falhou", f"`{cmd[0]}` saiu com código {codigo}"
    return "ok", ""


def _travar() -> bool:
    """Impede duas atualizações ao mesmo tempo (ex.: agendada + manual)."""
    os.makedirs(os.path.dirname(TRAVA), exist_ok=True)
    try:
        fd = os.open(TRAVA, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        # trava de uma execução que morreu há mais de 12 h não vale mais
        if time.time() - os.path.getmtime(TRAVA) > 12 * 3600:
            os.remove(TRAVA)
            return _travar()
        return False
    os.write(fd, str(os.getpid()).encode())
    os.close(fd)
    return True


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Atualiza anúncios, fotos, análise visual e referências de valor",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Etapas: backup imovelweb coleta fotos duplicatas endereco visao "
               "itbi-baixar iptu ajustes itbi venal venal-iptu area")
    ap.add_argument("--completo", action="store_true",
                    help="inclui as etapas mensais (ITBI novo e ajustes dos modelos)")
    ap.add_argument("--imovelweb", action="store_true",
                    help="inclui a coleta do Imovelweb (navegador)")
    ap.add_argument("--provedor", choices=["claude", "deepseek"],
                    default=config.VISAO_PROVEDOR, help="quem analisa as fotos")
    ap.add_argument("--teto", type=float, default=20.0,
                    help="(deepseek) teto de gasto em reais na análise das fotos")
    ap.add_argument("--limite-visao", type=int, default=0, metavar="N",
                    help="analisa no máximo N anúncios nesta rodada")
    ap.add_argument("--max-paginas", type=int, default=0, metavar="N",
                    help="teto de páginas por bairro na coleta")
    ap.add_argument("--so", nargs="+", metavar="ETAPA", help="roda só estas etapas")
    ap.add_argument("--pular", nargs="+", metavar="ETAPA", help="pula estas etapas")
    ap.add_argument("--listar", action="store_true", help="mostra o plano e sai")
    args = ap.parse_args(argv)

    todas = montar_etapas(args)
    nomes = {e.nome for e in todas}
    desconhecidas = set(args.so or []) | set(args.pular or [])
    desconhecidas -= nomes
    if desconhecidas:
        print(f"etapa(s) desconhecida(s): {', '.join(sorted(desconhecidas))}")
        print(f"disponíveis: {', '.join(e.nome for e in todas)}")
        return 2

    plano = escolher(todas, args)
    print("Plano:")
    for i, e in enumerate(plano, 1):
        print(f"  {i:>2}. {e.nome:<12} {e.descricao}")
    if args.listar:
        return 0

    if not _travar():
        print(f"\nOutra atualização está rodando (trava em {TRAVA}). Saindo.")
        return 3

    resultados: list[tuple[str, str, str, float]] = []
    t_inicio = time.time()
    log.info("atualização iniciada: %s", ", ".join(e.nome for e in plano))
    try:
        for i, e in enumerate(plano, 1):
            print(f"\n{'=' * 66}\n[{i}/{len(plano)}] {e.nome} — {e.descricao}\n{'=' * 66}",
                  flush=True)
            t0 = time.time()
            situacao, detalhe = rodar(e, args)
            resultados.append((e.nome, situacao, detalhe, time.time() - t0))
            # sem backup não se mexe no banco
            if e.nome == "backup" and situacao != "ok":
                print("\nO backup falhou; nada mais será feito.")
                break
    except KeyboardInterrupt:
        print("\n[interrompido pelo usuário]")
        resultados.append(("(interrompido)", "parado", "", 0.0))
    finally:
        try:
            os.remove(TRAVA)
        except OSError:
            pass

    print(f"\n{'=' * 66}\nResumo ({_duracao(time.time() - t_inicio)})\n{'=' * 66}")
    for nome, situacao, detalhe, dur in resultados:
        marca = "ok " if situacao == "ok" else "XX "
        print(f"  {marca} {nome:<12} {_duracao(dur):>8}  {detalhe}")
    falhas = [r for r in resultados if r[1] != "ok"]
    log.info("atualização terminada: %d etapa(s), %d com falha", len(resultados), len(falhas))
    if falhas:
        print(f"\n{len(falhas)} etapa(s) com problema. Detalhes em "
              f"{config.caminho('dados', 'logs', 'caca.log')}")
    return 1 if falhas else 0


if __name__ == "__main__":
    sys.exit(main())
