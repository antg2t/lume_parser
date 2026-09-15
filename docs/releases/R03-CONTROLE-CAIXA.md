# R03 — Controle de caixa em XLSX e PDF

## Contexto e objetivo

Pré-requisito: fundação e extração PDF da R00. Há um controle de caixa em XLSX e o relatório correspondente em PDF, com 12 páginas. O XLSX será a referência estrutural; o PDF será validado contra ele.

O objetivo é produzir um `CashLedger` idêntico nos dois formatos.

## Contrato documental

O modelo contém empresa, conta, período, saldo inicial, saldo final e lançamentos ordenados. Cada lançamento contém data, data de emissão, documento, cliente/fornecedor, anotações, entrada, saída, saldo e origem. Valores usam `Decimal`; linhas não transacionais permanecem no RAW.

## Implementação planejada

- XLSX: usar `openpyxl` em modo somente leitura, detectando abas, área útil, cabeçalho, linhas vazias, células mescladas e fórmulas.
- Carregar fórmulas e valores calculados separadamente quando existirem; avisar sobre cache ausente ou desatualizado.
- Detectar a linha de cabeçalho por conteúdo, sem depender de posição fixa.
- PDF: reconstruir colunas com coordenadas, remover cabeçalhos/rodapés repetidos e unir descrições continuadas.
- Preservar aba/célula para XLSX e página/região para PDF.
- Validar a cada linha: `saldo anterior + entrada - saída = saldo atual`.
- Comparar lançamentos dos dois formatos por ordem, datas, documento e valores; não esconder divergências.

## Testes

- Golden do XLSX com contagem e campos de todas as linhas úteis.
- Comparação integral PDF versus XLSX.
- Linhas com somente entrada, somente saída, descrição quebrada e documento vazio.
- Casos negativos: planilha corrompida, fórmula sem cache, cabeçalho ausente e quebra inesperada de página.

## Critérios de aceite

- Todas as linhas úteis do XLSX viram lançamentos rastreáveis.
- O cálculo de saldo fecha ou cada divergência é identificada precisamente.
- PDF e XLSX produzem o mesmo conjunto ordenado de lançamentos, salvo diferenças documentadas.
- Nenhuma classificação contábil é feita.
- O parser não depende do nome `Vanguarda` nem de uma conta bancária fixa.
