"""Detecção de duplicatas: o agrupamento rápido tem de dar o MESMO resultado."""

import random
import sqlite3

import imagehash
import pytest
from PIL import Image

from cacaimoveis import achar_duplicatas as ad
from cacaimoveis import config, migracoes


def _hash(valor: int):
    return imagehash.hex_to_hash(f"{valor:016x}")


def _cenario(seed: int, n_anuncios: int = 220):
    """Anúncios com 1-4 fotos; alguns são cópias (fotos a poucos bits de outras)."""
    rnd = random.Random(seed)
    base = [rnd.getrandbits(64) for _ in range(60)]
    hashes = {}
    for i in range(n_anuncios):
        fotos = []
        for _ in range(rnd.randint(1, 4)):
            v = rnd.choice(base) if rnd.random() < 0.55 else rnd.getrandbits(64)
            for bit in rnd.sample(range(64), rnd.choice([0, 0, 3, 8, 10, 12])):
                v ^= 1 << bit          # ruído: 0 a 12 bits trocados (o limiar é 10)
            fotos.append(_hash(v))
        hashes[f"https://x/{i}"] = fotos
    return hashes


def _normalizar(grupos):
    return sorted(sorted(g) for g in grupos)


@pytest.mark.parametrize("seed", [1, 2, 3])
@pytest.mark.parametrize("limiar", [6, 10])
def test_agrupamento_rapido_igual_ao_lento(seed, limiar):
    hashes = _cenario(seed)
    rapido = ad._agrupar(hashes, limiar, verbose=False)
    lento = ad._agrupar_lento(hashes, limiar, verbose=False)
    assert _normalizar(rapido) == _normalizar(lento)
    assert rapido, "o cenário deveria ter grupos"


def test_limiar_e_inclusivo():
    a, b = _hash(0), _hash(0b1111111111)           # 10 bits de diferença
    assert ad._agrupar({"a": [a], "b": [b]}, 10, verbose=False)
    assert not ad._agrupar({"a": [a], "b": [b]}, 9, verbose=False)


def test_cadeia_a_b_c_vira_um_grupo():
    """A~B e B~C (por fotos diferentes) juntam os três, mesmo sem A~C."""
    a1, b1, b2, c1 = _hash(0), _hash(0), _hash((1 << 63) - 1), _hash((1 << 63) - 1)
    (g,) = ad._agrupar({"A": [a1], "B": [b1, b2], "C": [c1]}, 10, verbose=False)
    assert sorted(g) == ["A", "B", "C"]


def test_cache_de_hashes(tmp_path, monkeypatch):
    conn = sqlite3.connect(":memory:")
    migracoes.migrar(conn)
    caminhos = []
    for i in range(3):
        c = tmp_path / f"{i}.jpg"
        Image.effect_noise((64, 64), 60 + 40 * i).convert("RGB").save(c)
        caminhos.append(str(c))
    fotos = {"u": caminhos}
    chamadas = []
    original = ad._hash_arquivo
    monkeypatch.setattr(ad, "_hash_arquivo", lambda c, t: chamadas.append(c) or original(c, t))

    h1 = ad._hash_anuncios(fotos, config.DUP_HASH_SIZE, verbose=False, conn=conn)
    assert len(chamadas) == 3
    h2 = ad._hash_anuncios(fotos, config.DUP_HASH_SIZE, verbose=False, conn=conn)
    assert len(chamadas) == 3                         # tudo veio do cache
    assert [str(h) for h in h1["u"]] == [str(h) for h in h2["u"]]

    # foto substituída (mesmo nome, outro conteúdo/mtime): é recalculada
    import os
    Image.effect_noise((64, 64), 250).convert("RGB").save(caminhos[1])
    os.utime(caminhos[1], (1, 1))
    ad._hash_anuncios(fotos, config.DUP_HASH_SIZE, verbose=False, conn=conn)
    assert chamadas[3:] == [caminhos[1]]
