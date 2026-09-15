# R00 — Fundação comum e extração de PDF digital

## Contexto e objetivo

Esta é a primeira release. O diretório contém 19 PDFs digitais: 17 NFS-e, um controle de caixa e um extrato bancário. Todos possuem camada de texto; OCR e interpretação de negócio ficam fora desta release.

O objetivo é criar a fundação Python e provar uma extração textual reproduzível para todos os PDFs atuais.

## Entrega

- Projeto Python 3.12 configurado por `pyproject.toml`, ambiente virtual e dependências travadas.
- Pacote `lume_ingestion` e CLI executável por `python -m lume_ingestion`.
- Comandos independentes: `inspect`, `extract`, `normalize`, `validate` e `batch`.
- Detector por assinatura binária e conteúdo; extensão serve apenas como indício.
- Registro de parsers para permitir novos formatos sem condicionais centrais.
- Modelos Pydantic `IngestionResult`, `SourceFile`, `Warning` e `Error`.
- Saída em `output/<hash-curto>/`: `raw.json`, `normalized.json`, `result.json` e texto por página.

O resultado registra SHA-256, arquivo, formato, tipo documental detectado quando possível, parser e versão, duração, warnings, erros e caminhos das saídas. Dinheiro usa `Decimal` e string no JSON; datas normalizadas usam ISO 8601; todo valor original continua no RAW.

## Implementação planejada

- Validar existência, tamanho, magic bytes, criptografia, número de páginas e limites de processamento.
- Usar `pypdf` para texto básico e metadados; usar `pdfplumber` para palavras, coordenadas, layout e tabelas.
- Extrair texto simples e em modo de layout, preservando a separação entre páginas.
- Calcular métricas por página: caracteres, palavras, proporção de caracteres válidos e presença de imagens.
- Classificar o PDF como textual, híbrido ou provável imagem; apenas registrar a necessidade de OCR.
- Produzir erros estruturados para PDF vazio, truncado, protegido ou inválido.
- Garantir que um arquivo não seja sobrescrito por outro com o mesmo nome.

## Testes

- Unitários para detector, hash, serialização, nomes repetidos e erros estruturados.
- Integração com os 19 PDFs atuais.
- Casos negativos mínimos: arquivo vazio, extensão falsa, PDF truncado e PDF criptografado.
- Repetir a execução e comparar os resultados sem campos voláteis.

## Critérios de aceite

- Os 19 PDFs abrem sem exceção não tratada.
- Todas as páginas geram texto útil e nenhuma aciona OCR.
- RAW, resultado e texto por página são produzidos para cada arquivo.
- Falhas geram códigos e mensagens acionáveis.
- A mesma entrada e versão do parser produzem a mesma saída semântica.
- Nenhum parser de NFS-e, caixa ou extrato é implementado nesta release.
