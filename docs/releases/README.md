# Releases do laboratório de ingestão

Cada documento desta pasta é autocontido: contém contexto, escopo, decisões, tarefas, testes e critérios de aceite suficientes para implementar somente aquela release.

## Acervo atual: executar em sequência

1. [R00 — Fundação e PDF digital](R00-FUNDACAO-PDF.md)
2. [R01 — NFS-e Nacional XML](R01-NFSE-XML.md)
3. [R02 — NFS-e/DANFSe PDF](R02-NFSE-PDF.md)
4. [R03 — Controle de caixa XLSX e PDF](R03-CONTROLE-CAIXA.md)
5. [R04 — Extrato bancário PDF](R04-EXTRATO-PDF.md)
6. [R05 — Regressão do acervo atual](R05-REGRESSAO-ACERVO.md)

## Tipos ainda sem cobertura genérica: executar após receber amostras reais

7. [R06 — OFX/QFX](R06-OFX-QFX.md)
8. [R07 — CSV, XLS e XLSX genéricos](R07-TABULARES.md) — suporte genérico ainda pendente; dois layouts bancários reais estão documentados na R13
9. [R08 — NF-e XML](R08-NFE-XML.md)
10. [R09 — OCR de PDF e imagens](R09-OCR.md)
11. [R10 — NFS-e XML municipal](R10-NFSE-MUNICIPAL.md)
12. [R11 — CT-e XML](R11-CTE-XML.md)
13. [R12 — IA como fallback](R12-IA-FALLBACK.md)
13. [R13 — Extratos tabulares reais](R13-EXTRATOS-TABULARES.md)

Regras permanentes: Python 3.12, execução local, arquivos originais intactos, RAW separado do normalizado, valores financeiros exatos e nenhum tipo aprovado sem fixtures reais.
