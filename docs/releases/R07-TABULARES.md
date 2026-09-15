# R07 — CSV, XLS e XLSX genéricos

## Contexto e objetivo

Esta release só começa após receber arquivos reais de CSV Contimatic, plano de contas, histórico contábil e planilhas auxiliares. Mínimo recomendado: cinco fixtures por perfil relevante.

O objetivo é produzir `TabularDataset` fiel e permitir perfis declarativos de mapeamento, sem realizar classificação contábil.

## Contrato documental

O modelo contém formato, encoding, delimitador quando aplicável, abas, cabeçalhos, linhas, tipos originais, fórmulas, warnings e origem por linha/célula. Códigos permanecem texto; valores só viram datas ou números quando o perfil confirmar a coluna.

## Implementação planejada

- CSV: usar `csv` e `charset-normalizer`; detectar BOM, encoding, delimitador, aspas e cabeçalho.
- Processar CSV grande em streaming e registrar linhas rejeitadas com número e motivo.
- XLSX: usar `openpyxl` em modo somente leitura, preservando fórmulas e valores calculados separadamente.
- XLS/XLSB: usar `python-calamine`; comparar resultados em XLSX quando houver dúvida.
- Tratar múltiplas abas, células mescladas, cabeçalho deslocado, linhas vazias e datas seriais.
- Criar perfis versionados por origem, com aliases de coluna, tipos, campos obrigatórios e regras de limpeza.
- Nunca remover zeros à esquerda nem usar inferência ampla do pandas como fonte canônica.

## Testes

- UTF-8, UTF-8 BOM, Windows-1252, vírgula, ponto e vírgula e tabulação.
- Códigos com zeros, valores brasileiros, datas ambíguas, fórmulas e células vazias.
- Múltiplas abas, cabeçalhos duplicados, colunas extras e arquivos grandes.

## Critérios de aceite

- Cada perfil real possui golden e mapeamento versionado.
- Todas as linhas aceitas e rejeitadas são contabilizadas.
- Valores e tipos originais permanecem rastreáveis.
- O parser genérico funciona sem conhecer regras contábeis.
