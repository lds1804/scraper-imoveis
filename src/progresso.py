"""Barra de progresso simples, feita para o terminal do Windows.

Por que não usar uma biblioteca pronta (tqdm, rich): o terminal aqui é o
PowerShell 5.1 com codificação cp1252, que engasga com acentos e desenhos
Unicode. Esta barra usa só ASCII (`=`, `>`, `.`) e volta com `\\r`, então
funciona em qualquer console.

Também funciona quando o progresso vem de VÁRIAS threads: quem chama deve
segurar um lock em volta de `passo()`/`escrever()` para as linhas não se
misturarem.
"""

from __future__ import annotations

import sys
import time


def _duracao(segundos: float) -> str:
    """Formata segundos como '1m23s' (ou '45s' quando é pouco)."""
    if segundos < 0 or segundos != segundos:  # negativo ou NaN
        return "--"
    s = int(segundos)
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m{s % 60:02d}s"
    return f"{s // 3600}h{(s % 3600) // 60:02d}m"


class Barra:
    """Barra de progresso com contadores, ritmo e previsão de término.

    Serve para dois casos:

      1. Um total conhecido de itens (ex.: analisar 178 anúncios).
         Cria com `Barra(178)` e chama `passo()` a cada item.

      2. Vários lotes de tamanho desconhecido (ex.: coletar por bairro, sem
         saber quantos anúncios cada um tem). Cria com
         `Barra.etapas(["Vila Mangalot", "City América", ...])`, chama
         `iniciar_etapa()` e depois `contar()` conforme os itens chegam.

    Os três níveis ligados por contexto aparecem numa linha só:
      geral    -> quantas etapas concluíram  [####....]  2/21
      etapa    -> nome da etapa atual        "City América"
      atual    -> itens feitos nesta etapa   847 itens
    """

    def __init__(self, total: int, largura: int = 24, stream=None,
                 rotulo: str = ""):
        self.total = max(1, total)
        self.largura = largura
        self.stream = stream or sys.stdout
        self.rotulo = rotulo
        self.feitos = 0
        self.ok = 0
        self.erros = 0
        self.reaproveitados = 0
        self.pulados = 0
        self.extras: dict[str, int] = {}
        self.t0 = time.time()
        self._ultimo_desenho = 0.0
        self._largura_linha = 0
        # por etapa (modo `etapas`)
        self.modo_etapas = False
        self.etapas: list[str] = []
        self.etapa_atual = ""
        self.etapa_indice = 0
        self.itens_etapa = 0
        self.itens_total = 0
        self._t0_etapa = time.time()

    # -- construtores -----------------------------------------------------
    @classmethod
    def etapas(cls, nomes: list[str], largura: int = 24, stream=None,
               rotulo: str = "") -> "Barra":
        """Barra para uma sequência de lotes (bairros, arquivos, páginas).

        Como só se sabe o total de itens DEPOIS de percorrer cada lote, o
        progresso geral é medido em etapas concluídas, e os itens da etapa
        atual aparecem como contagem crescente.
        """
        b = cls(len(nomes) or 1, largura, stream, rotulo)
        b.modo_etapas = True
        b.etapas = list(nomes)
        return b

    # -- estado -----------------------------------------------------------
    def passo(self, ok: bool = True, reaproveitado: bool = False) -> None:
        """Avança um item e redesenha a barra."""
        self.feitos += 1
        if reaproveitado:
            self.reaproveitados += 1
            self.ok += 1
        elif ok:
            self.ok += 1
        else:
            self.erros += 1
        self.desenhar()

    def contar(self, n: int = 1) -> None:
        """Modo etapas: soma itens à etapa atual e redesenha."""
        self.itens_etapa += n
        self.itens_total += n
        self.desenhar()

    def avancar(self, nome: str = "") -> None:
        """Modo etapas: conclui a etapa atual e passa para a próxima.

        O nome da etapa NÃO é limpo ao terminar a última: assim a linha final
        continua dizendo em que se estava trabalhando, em vez de virar um
        genérico "ok=0".
        """
        self.feitos += 1
        self.etapa_indice = min(self.feitos, len(self.etapas))
        self.itens_etapa = 0
        self._t0_etapa = time.time()
        if nome:
            self.etapa_atual = nome
        elif self.feitos < len(self.etapas):
            self.etapa_atual = self.etapas[self.feitos]
        self.desenhar(forcar=True)

    def iniciar_etapa(self, nome: str) -> None:
        """Modo etapas: define o nome da etapa que está começando."""
        self.etapa_atual = nome
        self.itens_etapa = 0
        self.itens_total += 0
        self._t0_etapa = time.time()
        self.desenhar(forcar=True)

    def soma_tag(self, tag: str, n: int = 1) -> None:
        """Contadores extras livres (ex.: 'pulados', 'apartamentos')."""
        self.extras[tag] = self.extras.get(tag, 0) + n

    @property
    def decorrido(self) -> float:
        return time.time() - self.t0

    def _ritmo(self) -> float:
        """Segundos por item (média até agora)."""
        return self.decorrido / self.feitos if self.feitos else 0.0

    def _restante(self) -> float:
        falta = self.total - self.feitos
        return self._ritmo() * falta

    # -- desenho ----------------------------------------------------------
    def _linha(self) -> str:
        frac = self.feitos / self.total
        cheio = int(self.largura * frac)

        if cheio > 0:
            miolo = "=" * (cheio - 1) + ">"
        else:
            miolo = ""
        miolo += "." * (self.largura - len(miolo))

        cabeca = f"{self.rotulo} " if self.rotulo else ""
        texto = (f"\r{cabeca}{self.feitos:>3}/{self.total} [{miolo}]"
                 f" {frac * 100:>3.0f}%")

        contadores = []

        # modo etapas: mostra o que está acontecendo agora
        if self.modo_etapas and self.etapa_atual:
            nome = self.etapa_atual
            if len(nome) > 26:
                nome = nome[:25] + "~"
            contadores.append(f"-> {nome}")
            if self.itens_etapa:
                contadores.append(f"{self.itens_etapa} itens")
            elif self.itens_total:
                contadores.append(f"{self.itens_total} itens no total")

            ritmo = self._ritmo()
            if self.feitos and ritmo >= 0.05:
                contadores.append(f"{ritmo:.1f}s/etapa")
                falta = self._restante()
                # projetar "0s" não ajuda: só vale a pena quando há o que esperar
                if falta >= 5:
                    contadores.append(f"faltam {_duracao(falta)}")
        else:
            contadores.append(f"ok={self.ok}")
            if self.reaproveitados:
                contadores.append(f"dup={self.reaproveitados}")
            if self.erros:
                contadores.append(f"erro={self.erros}")

        for tag, n in self.extras.items():
            if n:
                contadores.append(f"{tag}={n}")

        # ritmo/previsão só fazem sentido quando cada item leva um tempo
        # perceptível; em lotes instantâneos o número arredonda para 0,0s
        # e só polui a linha.
        if not self.modo_etapas:
            ritmo = self._ritmo()
            if self.feitos and ritmo >= 0.05:
                contadores.append(f"{ritmo:.1f}s/item")
                contadores.append(f"faltam {_duracao(self._restante())}")

        return texto + "  " + " ".join(contadores)

    def desenhar(self, forcar: bool = False) -> None:
        """Redesenha a linha (no máximo ~12x por segundo, para não piscar)."""
        agora = time.time()
        if not forcar and agora - self._ultimo_desenho < 0.08:
            return
        self._ultimo_desenho = agora

        linha = self._linha()
        # preenche o que sobrou da linha anterior para não deixar lixo
        sobra = max(0, self._largura_linha - len(linha))
        self.stream.write(linha + " " * sobra)
        self._largura_linha = len(linha)
        self.stream.flush()

    # -- mensagens --------------------------------------------------------
    def escrever(self, texto: str) -> None:
        """Imprime uma linha normal sem quebrar a barra."""
        self._apagar()
        self.stream.write(texto + "\n")
        self.stream.flush()
        self.desenhar(forcar=True)

    def _apagar(self) -> None:
        if self._largura_linha:
            self.stream.write("\r" + " " * self._largura_linha + "\r")
            self._largura_linha = 0

    def encerrar(self) -> None:
        """Fecha a barra (desenha o estado final e quebra a linha)."""
        self.desenhar(forcar=True)
        self.stream.write("\n")
        self.stream.flush()
        self._largura_linha = 0


def resumo(titulo: str, pares: list[tuple[str, object]],
           largura_rotulo: int = 24) -> None:
    """Imprime um bloco de resumo alinhado no fim de uma coleta.

    Ex.:
        ==============================
        OLX · coleta finalizada
        ==============================
        salvos                    847
        já existiam                12
    """
    linha = "=" * 58
    print(f"\n{linha}\n{titulo}\n{linha}")
    for rotulo, valor in pares:
        if isinstance(valor, float):
            print(f"  {rotulo:<{largura_rotulo}} {valor:,.2f}")
        elif isinstance(valor, int):
            print(f"  {rotulo:<{largura_rotulo}} {valor:,}")
        else:
            print(f"  {rotulo:<{largura_rotulo}} {valor}")
