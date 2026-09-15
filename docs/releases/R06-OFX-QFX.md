# R06 — Extratos bancários OFX/QFX

## Contexto e objetivo

Esta release só começa após receber amostras reais. Mínimo recomendado: dez arquivos de bancos e versões diferentes, incluindo OFX 1.x/SGML e OFX 2.x/XML. O modelo de destino é `BankStatement`; PDF, OCR e IA ficam fora do escopo.

O objetivo é importar extratos estruturados preservando identificadores bancários e reconciliando saldos.

## Implementação planejada

- Detectar OFX/QFX pelo cabeçalho e conteúdo, não pela extensão.
- Normalizar encoding, quebras e tags não fechadas de OFX 1.x antes do parser.
- Avaliar `ofxparse` contra os fixtures; manter adaptador próprio para diferenças de versão.
- Extrair banco, agência, conta, tipo, moeda, período, ledger balance e available balance.
- Extrair data, valor, tipo, `FITID`, cheque, nome e memo de cada transação.
- Preservar timezone e data original no RAW.
- Marcar FITIDs repetidos, sem excluir automaticamente transações.
- Reconciliar saldo inicial, movimentações e saldo final quando o arquivo fornecer dados suficientes.

## Testes

- OFX 1.x e 2.x, diferentes encodings, valores negativos e datas com timezone.
- Tags opcionais ausentes, memo multilinha, FITID duplicado e arquivo truncado.
- Comparação com extrato visual ou golden manual do mesmo período.

## Critérios de aceite

- Todos os fixtures suportados geram `BankStatement` sem perda de transações.
- Conta, período, valores, FITIDs e saldos coincidem com as goldens.
- Variantes desconhecidas falham com diagnóstico de versão/layout.
- Nenhuma deduplicação destrutiva ocorre.
