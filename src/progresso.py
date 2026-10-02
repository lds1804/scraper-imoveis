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
    """Barra de progresso com contadores, ritmo e previsão de término."""

    def __init__(self, total: int, largura: int = 24, stream=None):
        self.total = max(1, total)
        self.largura = largura
        self.stream = stream or sys.stdout
        self.feitos = 0
        self.ok = 0
        self.erros = 0
        self.reaproveitados = 0
        self.t0 = time.time()
        self._ultimo_desenho = 0.0
        self._largura_linha = 0

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

        texto = f"\r{self.feitos:>4}/{self.total} [{miolo}] {frac * 100:>3.0f}%"

        contadores = [f"ok={self.ok}"]
        if self.reaproveitados:
            contadores.append(f"dup={self.reaproveitados}")
        if self.erros:
            contadores.append(f"erro={self.erros}")

        # ritmo/previsão só fazem sentido quando cada item leva um tempo
        # perceptível; em lotes instantâneos o número arredonda para 0,0s
        # e só polui a linha.
        ritmo = self._ritmo()
        if self.feitos and ritmo >= 0.05:
            contadores.append(f"{ritmo:.1f}s/ad")
            contadores.append(f"resta {_duracao(self._restante())}")

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
