"""Análise das fotos pelo Claude Code CLI (sem chamar o CLI de verdade)."""

import json
import os
import types

import pytest
from PIL import Image

from cacaimoveis import config, visao

RESPOSTA = {
    "area_externa": "sim", "tem_quintal": "sim", "piso_quintal": "grama",
    "quintal_terra": "nao", "parece_cimentado": "nao", "arvores": "sim",
    "vegetacao_nota": 4, "iluminacao_nota": 3, "arejamento_nota": 4,
    "cuidado_nota": 5, "janelas_grandes": "sim", "fachada": "terrea",
    "parece_reformado": "sim", "piso": "porcelanato", "comodos": ["sala"],
    "extras": ["churrasqueira"], "problemas": [], "tem_planta_baixa": "nao",
    "resumo": "Casa térrea com quintal gramado.", "confianca": "alta",
}


@pytest.fixture()
def fotos(tmp_path):
    caminhos = []
    for i in range(3):
        c = tmp_path / f"{i:02d}.jpg"
        Image.new("RGB", (2000, 1500), (40 * i, 120, 80)).save(c)
        caminhos.append(str(c))
    return caminhos


def _falso_claude(monkeypatch, saida: dict, chamadas: list):
    monkeypatch.setattr(visao, "achar_claude", lambda: "claude")
    monkeypatch.setattr(visao.time, "sleep", lambda s: None)

    def run(cmd, **kw):
        pasta = cmd[cmd.index("--add-dir") + 1]
        enviadas = sorted(os.listdir(pasta))
        tamanhos = [Image.open(os.path.join(pasta, f)).size for f in enviadas]
        chamadas.append({"cmd": cmd, "cwd": kw.get("cwd"), "fotos": enviadas, "tamanhos": tamanhos})
        return types.SimpleNamespace(returncode=0, stdout=json.dumps(saida), stderr="")

    monkeypatch.setattr(visao.subprocess, "run", run)


def test_claude_analisa_e_grava_o_modelo(monkeypatch, fotos):
    chamadas: list = []
    resposta = "Aqui está:\n```json\n" + json.dumps(RESPOSTA) + "\n```"
    _falso_claude(monkeypatch, {"is_error": False, "result": resposta}, chamadas)

    a = visao.analisar_anuncio("u", fotos, verbose=False, provedor="claude")

    assert a.ok and a.tem_quintal and a.piso_quintal == "grama" and a.cuidado_nota == 5
    assert a.modelo == f"claude:{config.VISAO_CLAUDE_MODELO}"
    (c,) = chamadas
    cmd = c["cmd"]
    # só leitura de arquivo, só na pasta temporária, sem MCP nem sessão salva
    assert cmd[cmd.index("--tools") + 1] == "Read"
    assert cmd[cmd.index("--allowedTools") + 1] == "Read"
    assert "--strict-mcp-config" in cmd and "--no-session-persistence" in cmd
    assert c["cwd"] == cmd[cmd.index("--add-dir") + 1]
    # as 3 fotos foram reduzidas antes de ir
    assert len(c["fotos"]) == 3
    assert all(max(t) <= config.VISAO_LADO_MAX_PX for t in c["tamanhos"])
    # e o prompt cita cada arquivo
    prompt = cmd[cmd.index("-p") + 1]
    assert all(f in prompt for f in c["fotos"])


def test_claude_sem_login_explica_o_que_fazer(monkeypatch, fotos):
    chamadas: list = []
    _falso_claude(monkeypatch, {"is_error": True, "result": "Not logged in · Please run /login"},
                  chamadas)
    a = visao.analisar_anuncio("u", fotos, verbose=False, provedor="claude")
    assert not a.ok
    assert "login" in a.erro
    assert len(chamadas) == config.VISAO_MAX_TENTATIVAS


def test_claude_limita_as_fotos_espalhadas_pela_galeria(monkeypatch, tmp_path):
    caminhos = []
    for i in range(30):
        c = tmp_path / f"{i:02d}.jpg"
        Image.new("RGB", (64, 48)).save(c)
        caminhos.append(str(c))
    chamadas: list = []
    _falso_claude(monkeypatch, {"is_error": False, "result": json.dumps(RESPOSTA)}, chamadas)
    visao.analisar_anuncio("u", caminhos, verbose=False, provedor="claude")
    assert len(chamadas[0]["fotos"]) == config.VISAO_CLAUDE_MAX_FOTOS


def test_provedor_pronto(monkeypatch):
    monkeypatch.setattr(visao, "achar_claude", lambda: None)
    assert "não encontrado" in visao.provedor_pronto("claude")
    monkeypatch.setattr(visao, "achar_claude", lambda: "claude")
    assert visao.provedor_pronto("claude") is None
    assert "desconhecido" in visao.provedor_pronto("gpt")


def test_falha_de_login_e_transitoria(monkeypatch, fotos):
    """Não pode ser gravada como "falhou": a próxima rodada tem de tentar."""
    _falso_claude(monkeypatch, {"is_error": True, "result": "Not logged in"}, [])
    assert visao.analisar_anuncio("u", fotos, verbose=False, provedor="claude").transitorio


def test_resposta_sem_json_nao_e_transitoria(monkeypatch, fotos):
    _falso_claude(monkeypatch, {"is_error": False, "result": "não sei"}, [])
    a = visao.analisar_anuncio("u", fotos, verbose=False, provedor="claude")
    assert not a.ok and not a.transitorio


# ---------------------------------------------------------------------------
# Falhar rápido quando o transporte não funciona (login, rede)
# ---------------------------------------------------------------------------
def test_checagem_de_login_explica_o_que_fazer(monkeypatch):
    monkeypatch.setattr(visao, "achar_claude", lambda: "claude")
    monkeypatch.setattr(visao.subprocess, "run", lambda *a, **k: types.SimpleNamespace(
        returncode=1, stdout=json.dumps({"is_error": True, "result": "Not logged in"}), stderr=""))
    msg = visao.verificar_login_claude()
    assert "não está logado" in msg and "/login" in msg and "deepseek" in msg


def test_checagem_de_login_passa_quando_responde(monkeypatch):
    monkeypatch.setattr(visao, "achar_claude", lambda: "claude")
    monkeypatch.setattr(visao.subprocess, "run", lambda *a, **k: types.SimpleNamespace(
        returncode=0, stdout=json.dumps({"is_error": False, "result": "ok"}), stderr=""))
    assert visao.verificar_login_claude() is None


def test_lote_nao_comeca_sem_login(monkeypatch, capsys):
    from cacaimoveis import analisar_visao as av

    chamadas = []
    monkeypatch.setattr(av, "verificar_login_claude", lambda: "o Claude Code não está logado.")
    monkeypatch.setattr(av, "analisar_anuncio", lambda *a, **k: chamadas.append(1))
    monkeypatch.setattr(av, "provedor_pronto", lambda p: None)
    monkeypatch.setattr("sys.argv", ["caca-visao", "--provedor", "claude", "--limite", "20"])
    assert av.main() == 1
    assert chamadas == []
    assert "não iniciada" in capsys.readouterr().out


@pytest.mark.parametrize("paralelo", [1, 3])
def test_lote_aborta_depois_de_falhas_de_transporte_seguidas(monkeypatch, capsys, paralelo):
    from cacaimoveis import analisar_visao as av

    chamadas = []

    def falha(url, fotos, **kw):
        chamadas.append(url)
        return visao.AnaliseFoto(url=url, ok=False, erro="rede caiu", transitorio=True)

    monkeypatch.setattr(av, "verificar_login_claude", lambda: None)
    monkeypatch.setattr(av, "provedor_pronto", lambda p: None)
    monkeypatch.setattr(av, "analisar_anuncio", falha)
    monkeypatch.setattr(config, "VISAO_CLAUDE_PARALELO", paralelo)
    monkeypatch.setattr("sys.argv", ["caca-visao", "--provedor", "claude", "--limite", "40"])
    assert av.main() == 1
    assert av.FALHAS_SEGUIDAS_MAX <= len(chamadas) < 40
    assert "ABORTADO" in capsys.readouterr().out


def test_caca_login_ja_logado_nao_abre_nada(monkeypatch, capsys):
    from cacaimoveis import login

    abriu = []
    monkeypatch.setattr(login, "achar_claude", lambda: "claude")
    monkeypatch.setattr(login, "verificar_login_claude", lambda: None)
    monkeypatch.setattr(login.subprocess, "call", lambda *a, **k: abriu.append(1))
    assert login.main() == 0
    assert abriu == [] and "já está logado" in capsys.readouterr().out


def test_caca_login_abre_o_claude_e_confere_depois(monkeypatch, capsys):
    from cacaimoveis import login

    estados = iter(["não está logado", None])        # antes: sem login; depois: logado
    abriu = []
    monkeypatch.setattr(login, "achar_claude", lambda: "C:/x/claude.exe")
    monkeypatch.setattr(login, "verificar_login_claude", lambda: next(estados))
    monkeypatch.setattr(login.subprocess, "call", lambda cmd, **k: abriu.append(cmd))
    monkeypatch.setattr("builtins.input", lambda *a: "")
    assert login.main() == 0
    assert abriu == [["C:/x/claude.exe"]]
    assert "Login confirmado" in capsys.readouterr().out


def test_caca_login_avisa_se_o_login_nao_pegou(monkeypatch):
    from cacaimoveis import login

    monkeypatch.setattr(login, "achar_claude", lambda: "claude")
    monkeypatch.setattr(login, "verificar_login_claude", lambda: "não está logado")
    monkeypatch.setattr(login.subprocess, "call", lambda *a, **k: 0)
    monkeypatch.setattr("builtins.input", lambda *a: "")
    assert login.main() == 1


def test_acha_o_claude_na_pasta_real_do_app_empacotado(tmp_path, monkeypatch):
    """Num terminal comum %APPDATA%\Claude não existe (é virtualizada para o
    app); o claude.exe de verdade está em LOCALAPPDATA\Packages\Claude_*."""
    exe = (tmp_path / "Local" / "Packages" / "Claude_abc123" / "LocalCache" / "Roaming"
           / "Claude" / "claude-code" / "2.1.288" / "hash" / "claude.exe")
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"")
    monkeypatch.setattr(config, "VISAO_CLAUDE_BIN", "")
    monkeypatch.setattr(visao.shutil, "which", lambda n: None)
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))           # sem nada dentro
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
    assert visao.achar_claude() == str(exe)


def test_escolhe_a_versao_mais_recente(tmp_path, monkeypatch):
    base = tmp_path / "Roaming" / "Claude" / "claude-code"
    for versao, mtime in (("2.1.1", 100), ("2.1.9", 900)):
        exe = base / versao / "h" / "claude.exe"
        exe.parent.mkdir(parents=True)
        exe.write_bytes(b"")
        import os
        os.utime(exe, (mtime, mtime))
    monkeypatch.setattr(config, "VISAO_CLAUDE_BIN", "")
    monkeypatch.setattr(visao.shutil, "which", lambda n: None)
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
    assert "2.1.9" in visao.achar_claude()
