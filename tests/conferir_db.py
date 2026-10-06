"""Conferência rápida do estado do banco."""


from _bootstrap import iniciar

iniciar()  # poe src/ no sys.path e fixa a raiz como diretorio de trabalho

import sqlite3

c = sqlite3.connect("imoveis.db")
q = lambda sql: c.execute(sql).fetchone()[0]

print("total              :", q("SELECT COUNT(*) FROM anuncios"))
print("titulo = preco     :", q("SELECT COUNT(*) FROM anuncios WHERE titulo LIKE 'R$%'"))
print("titulo vazio       :", q("SELECT COUNT(*) FROM anuncios WHERE titulo IS NULL OR titulo=''"))
print("area_terreno ok    :", q("SELECT COUNT(*) FROM anuncios WHERE area_terreno IS NOT NULL"))
print("area_construida ok :", q("SELECT COUNT(*) FROM anuncios WHERE area_construida IS NOT NULL"))
print("banheiros ok       :", q("SELECT COUNT(*) FROM anuncios WHERE banheiros IS NOT NULL"))
print("endereco ok        :", q("SELECT COUNT(*) FROM anuncios WHERE endereco IS NOT NULL AND endereco<>''"))
