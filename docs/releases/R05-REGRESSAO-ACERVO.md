# R05 — Regressão e fechamento do acervo atual

## Contexto e objetivo

Esta release fecha o primeiro ciclo. O acervo privado original cobria 29 fixtures: 24 documentos operacionais/fiscais, dois planos de contas e três históricos contábeis XLSX. O manifesto distribuível cobre 26 fixtures: os três históricos contábeis, suas goldens e seus hashes permanecem exclusivamente no acervo privado.

O objetivo é provar que a arquitetura funciona em lote, medir qualidade e congelar uma base de regressão antes de adicionar novos formatos.

## Entrega

- Manifesto único de fixtures com hash, tipo esperado, parser esperado e golden associada.
- Adaptadores XLSX para `chart_of_accounts` e `accounting_history`, com origem por linha e célula.
- Comando de lote recursivo que não depende dos nomes das pastas.
- Relatório agregado em JSON e Markdown com sucesso, falha, duração, warnings, campos esperados/encontrados, OCR e IA.
- Matriz de cobertura por tipo documental e por campo.
- Registro explícito de limitações conhecidas e layouts suportados.

## Operação da baseline

O manifesto versionado fica em `tests/fixtures_manifest.json`. Ele fixa as 26
fixtures públicas por SHA-256 — e não por nome ou diretório — com formato,
tipo documental, parser e golden correspondente. Assim, os arquivos podem ser
movidos ou renomeados dentro de `docs` sem alterar a seleção da regressão.

```powershell
poetry run python -m lume_ingestion regress docs
```

O comando grava `output/regression/report.json` e
`output/regression/report.md`; os artefatos de cada documento continuam em
subdiretórios isolados pelo hash. O JSON inclui resultado por fixture, tempos
de extração/normalização/validação/total, warnings, erros, campos
esperados/encontrados, uso de OCR/IA, cobertura e a comparação cruzada entre
o controle de caixa XLSX e PDF.

Os layouts de plano de contas foram promovidos à baseline pública. O adaptador
de histórico contábil continua suportado pelo produto, mas a validação com
históricos reais deve usar manifesto e acervo privados, fora do Git. Extratos
tabulares genéricos continuam no escopo da R07.

## Implementação planejada

- Processar cada arquivo em diretório de saída isolado por hash.
- Continuar o lote quando um documento falhar.
- Comparar somente saídas normalizadas estáveis; o RAW não será congelado integralmente.
- Medir tempo por estágio e total, sem impor SLA antes da primeira linha de base.
- Confirmar que os arquivos originais não foram alterados.
- Executar validações cruzadas do controle de caixa e reconciliações financeiras.

## Testes

- Execução das 26 fixtures públicas em ordem diferente e com nomes copiados/alterados.
- Reexecução integral para verificar determinismo.
- Inserção de arquivos vazios, corrompidos e com extensão falsa para testar isolamento de falhas.
- Validação dos relatórios agregados e códigos de saída da CLI.

## Critérios de aceite

- 26 de 26 fixtures públicas classificadas corretamente.
- 26 de 26 produzem RAW, normalizado e resultado, ou erro estruturado previsto.
- Zero exceções não tratadas.
- Campos obrigatórios coincidem com as goldens.
- Valores financeiros permanecem exatos.
- Nenhum documento atual usa OCR ou IA.
- As limitações restantes estão documentadas por tipo e fixture.
