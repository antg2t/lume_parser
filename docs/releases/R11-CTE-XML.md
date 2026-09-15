# R11 — CT-e em XML

## Contexto e objetivo

Esta release só começa após obter CT-e XML reais. O conjunto mínimo deve cobrir CT-e normal, complemento, anulação/substituição quando aplicável, modais relevantes e eventos.

O objetivo é produzir um `FiscalDocument` especializado em transporte, sem forçar a semântica de itens da NF-e.

## Implementação planejada

- Detectar `CTe` e `cteProc`, namespace, versão, modelo, ambiente e protocolo.
- Extrair chave, número, série, emissão, CFOP, natureza e tipo do serviço.
- Extrair emitente, remetente, expedidor, recebedor, destinatário e tomador.
- Extrair municípios de início/fim, modal, características da carga e documentos vinculados.
- Extrair componentes do valor, total da prestação, valor a receber e tributos.
- Preservar dados modais específicos, assinatura, protocolo e eventos no RAW.
- Validar chave, participantes, documentos referenciados e coerência de valores.

## Testes

- Um fixture por modalidade e finalidade suportadas.
- Múltiplas NF-e referenciadas, participantes opcionais, complemento e cancelamento.
- XML truncado, versão desconhecida e referência inválida.

## Critérios de aceite

- Todos os fixtures suportados produzem documento de transporte rastreável.
- Participantes, percurso, referências e valores coincidem com as goldens.
- Campos próprios de CT-e não são descartados por limitações do modelo de NF-e.
- Eventos e protocolo permanecem associados ao documento correto.
