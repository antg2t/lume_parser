# R10 — NFS-e XML municipal

## Contexto e objetivo

Esta release trata layouts municipais fora do padrão nacional. Só começa após receber fixtures reais identificados por município, provedor e versão. O parser nacional continua isolado e não será alterado para aceitar XMLs incompatíveis.

O objetivo é criar uma arquitetura de adaptadores municipais que produza o mesmo `FiscalDocument` da NFS-e Nacional.

## Implementação planejada

- Detectar provedor, município, namespace, raiz e versão antes de selecionar o adaptador.
- Criar um adaptador independente por família de layout.
- Mapear identificação, RPS, prestador, tomador, serviço, município, ISS, retenções e valores.
- Preservar campos proprietários e XML desconhecido no RAW.
- Versionar o mapeamento e os XSDs por layout.
- Recusar layout desconhecido com diagnóstico que permita adicionar um novo adaptador.
- Manter consulta por chave, NSU, certificado e API fora desta release local de parsing.

## Testes

- Mínimo de cinco XMLs por família de layout suportada.
- Campos opcionais, namespace variável, RPS substituído e nota cancelada.
- Garantir que XML nacional continue usando exclusivamente o parser nacional.

## Critérios de aceite

- Cada layout declarado possui detector, adaptador, goldens e documentação de versão.
- O resultado usa o mesmo contrato canônico da NFS-e Nacional.
- Nenhum fallback genérico produz dados aproximados.
- Adicionar um novo município não exige alterar adaptadores existentes.
