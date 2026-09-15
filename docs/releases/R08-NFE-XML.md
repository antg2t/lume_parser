# R08 — NF-e em XML

## Contexto e objetivo

Esta release só começa após receber pelo menos dez NF-e XML reais e variados. São necessários exemplos autorizados, diferentes regimes, itens, impostos e ao menos uma nota com grupos IBS/CBS quando disponível.

O objetivo é transformar `NFe` ou `nfeProc` em `FiscalDocument`, preservando protocolo, itens e tributação.

## Implementação planejada

- Ler XML com proteção contra XXE e detectar namespace, versão e raiz.
- Extrair chave, modelo, número, série, emissão, operação, finalidade e ambiente.
- Extrair emitente, destinatário, endereços, inscrições e regime tributário.
- Extrair por item: código, descrição, NCM, CFOP, unidade, quantidades, valores e descontos.
- Extrair ICMS, IPI, PIS, COFINS, ISSQN e IBS/CBS conforme o layout.
- Extrair frete, seguro, outras despesas, desconto e totais.
- Preservar protocolo, status, motivo, assinatura e eventos associados no RAW.
- Validar chave, CNPJ/CPF, soma de itens, tributos e total sem tentar substituir um motor fiscal.
- Rejeitar versão desconhecida sem aplicar regra aproximada.

## Testes

- NF-e autorizada, denegada/cancelada quando aplicável, complementar e devolução.
- Múltiplos itens, diferentes CST/CSOSN, desconto, frete e campos opcionais.
- Namespace ausente, XML truncado, chave inválida e divergência de total.

## Critérios de aceite

- Todos os fixtures suportados preservam itens, totais e protocolo.
- Campos obrigatórios coincidem com goldens manuais.
- Divergências aritméticas geram warnings precisos.
- IBS/CBS é opcional por documento e preservado integralmente quando existir.
