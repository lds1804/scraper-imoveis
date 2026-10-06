# Experimentos

Scripts de medição e investigação que embasaram as decisões do projeto
(índice IGP-M x IPCA, janela de 10 anos, custo da análise visual, tamanho do
banco de produção, robots.txt dos portais...). Cada um documenta no topo a
pergunta que respondeu e o resultado.

Não são código de produção nem testes: rodam uma vez, contra o `imoveis.db`
de trabalho, e imprimem números. Ficam versionados porque explicam *por que*
o código é como é.

Rodar da raiz do projeto:

```bash
python experimentos/medir_igpm.py
```
