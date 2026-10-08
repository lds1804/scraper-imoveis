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
    """Num terminal comum a pasta APPDATA/Claude não existe (é virtualizada para
    o app); o claude.exe de verdade está em LOCALAPPDATA/Packages/Claude_*."""
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


# ---------------------------------------------------------------------------
# Cópias do mesmo imóvel herdam a análise (sem chamar o modelo)
# ---------------------------------------------------------------------------
def _db_com_grupo(tmp_path):
    from cacaimoveis import storage

    db = storage.DB(str(tmp_path / "h.db"))
    ins = ("INSERT INTO anuncios (url, portal, dup_grupo, dup_melhor, foto_ok, foto_cuidado, "
           "foto_resumo, foto_piso_quintal, foto_modelo, foto_analisada_em) "
           "VALUES (?,?,?,?,?,?,?,?,?,?)")
    # grupo 1: um analisado (o principal) e duas cópias novas; grupo 2: ninguém analisado
    db.conn.execute(ins, ("u1", "zap", 1, 1, 1, 4, "Casa com quintal", "grama", "claude:sonnet", "2026-10-01"))
    db.conn.execute(ins, ("u2", "olx", 1, 0, None, None, None, None, None, None))
    db.conn.execute(ins, ("u3", "quintoandar", 1, 0, None, None, None, None, None, None))
    db.conn.execute(ins, ("u4", "zap", 2, 1, None, None, None, None, None, None))
    db.conn.execute(ins, ("u5", "zap", None, None, None, None, None, None, None, None))
    db.conn.commit()
    return db


def test_copia_herda_a_analise_do_grupo(tmp_path):
    db = _db_com_grupo(tmp_path)
    assert db.herdar_analise_visual() == 2
    for u in ("u2", "u3"):
        r = db.conn.execute("SELECT * FROM anuncios WHERE url = ?", (u,)).fetchone()
        assert r["foto_ok"] == 1 and r["foto_cuidado"] == 4
        assert r["foto_resumo"] == "Casa com quintal" and r["foto_piso_quintal"] == "grama"
        assert r["foto_modelo"] == "claude:sonnet"
    # quem não tem análise no grupo (ou nem tem grupo) continua pendente
    pendentes = {r["url"] for r in db.sem_analise_visual()}
    assert pendentes == {"u4", "u5"}
    db.close()


def test_herdar_nao_sobrescreve_e_e_idempotente(tmp_path):
    db = _db_com_grupo(tmp_path)
    db.conn.execute("UPDATE anuncios SET foto_ok = 1, foto_cuidado = 2 WHERE url = 'u2'")
    db.conn.commit()
    assert db.herdar_analise_visual() == 1            # só u3
    assert db.conn.execute("SELECT foto_cuidado FROM anuncios WHERE url='u2'").fetchone()[0] == 2
    assert db.herdar_analise_visual() == 0
    db.close()


def test_anuncio_removido_nao_serve_de_fonte(tmp_path):
    db = _db_com_grupo(tmp_path)
    db.conn.execute("UPDATE anuncios SET removido_em = 'x' WHERE url = 'u1'")
    db.conn.commit()
    assert db.herdar_analise_visual() == 0
    db.close()


# ---------------------------------------------------------------------------
# Número da casa lido nas fotos (raro; ver visao.py)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("valor, esperado", [
    ("335", "335"), (335, "335"), (" 12 a ", "12A"), ("0123", "123"), ("1500", "1500"),
    # o que NÃO é número de porta
    (None, ""), ("", ""), ("null", ""), ("nenhum", ""), ("0", ""), (True, ""),
    ("11987654321", ""),            # telefone de placa "vende-se"
    ("05124-010", ""),              # CEP
    ("R$ 750.000", ""), ("200 m2", ""), ("335 e 337", ""),
])
def test_numero_da_casa_so_aceita_numero_de_porta(valor, esperado):
    assert visao.numero_da_casa(valor) == esperado


def test_json_com_numero_e_certeza():
    a = visao.AnaliseFoto.do_json("u", 5, {"numero_casa": "523", "numero_casa_certeza": "Alta"})
    assert (a.numero_casa, a.numero_casa_certeza) == ("523", "alta")


def test_numero_sem_certeza_informada_conta_como_baixa():
    a = visao.AnaliseFoto.do_json("u", 5, {"numero_casa": "523"})
    assert a.numero_casa_certeza == "baixa"
    a = visao.AnaliseFoto.do_json("u", 5, {"numero_casa": "523", "numero_casa_certeza": "talvez"})
    assert a.numero_casa_certeza == "baixa"


def test_sem_numero_nao_ha_certeza():
    for dados in ({}, {"numero_casa": None, "numero_casa_certeza": "alta"},
                  {"numero_casa": "11987654321", "numero_casa_certeza": "alta"}):
        a = visao.AnaliseFoto.do_json("u", 5, dados)
        assert (a.numero_casa, a.numero_casa_certeza) == ("", "")


def test_prompt_pede_o_numero_e_avisa_do_que_nao_e_numero_de_casa():
    p = visao.PROMPT.format(n=3)
    assert '"numero_casa"' in p and '"numero_casa_certeza"' in p
    for armadilha in ("telefone", "CRECI", "CEP", "vaga", "NÃO adivinhe"):
        assert armadilha in p


def test_numero_e_gravado_e_herdado_pelas_copias(tmp_path):
    db = _db_com_grupo(tmp_path)
    a = visao.AnaliseFoto.do_json("u1", 3, {"numero_casa": "523", "numero_casa_certeza": "alta"})
    db.salvar_analise_visual(a)
    r = db.conn.execute("SELECT foto_numero_casa, foto_numero_certeza FROM anuncios "
                        "WHERE url='u1'").fetchone()
    assert (r[0], r[1]) == ("523", "alta")
    db.herdar_analise_visual()
    for u in ("u2", "u3"):
        r = db.conn.execute("SELECT foto_numero_casa, foto_numero_certeza FROM anuncios "
                            "WHERE url=?", (u,)).fetchone()
        assert (r[0], r[1]) == ("523", "alta")
    db.close()


def test_analise_sem_numero_grava_nulo(tmp_path):
    db = _db_com_grupo(tmp_path)
    db.salvar_analise_visual(visao.AnaliseFoto.do_json("u1", 3, {"cuidado_nota": 4}))
    r = db.conn.execute("SELECT foto_numero_casa, foto_numero_certeza FROM anuncios "
                        "WHERE url='u1'").fetchone()
    assert (r[0], r[1]) == (None, None)
    db.close()
