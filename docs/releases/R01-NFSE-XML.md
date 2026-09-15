# R01 — NFS-e Nacional em XML

## Contexto e objetivo

Pré-requisito: fundação de entrada, saída e modelos de resultado da R00. Há quatro XMLs reais com raiz `NFSe` e namespace nacional; uma amostra contém o grupo IBS/CBS.

O objetivo é transformar cada XML em RAW rastreável e em um `FiscalDocument` normalizado, sem depender de certificado, consulta externa ou API.

## Contrato documental

O documento normalizado contém tipo, versão, chave, número, DPS, série, ambiente, situação, emissão, competência, prestador, tomador, intermediário, locais, serviço, valores, retenções, tributos e metadados. Cada campo indica o XPath de origem. Valores monetários são exatos; campos opcionais ausentes são `null`.

## Implementação planejada

- Ler XML de forma segura com `defusedxml`; usar `lxml/xmlschema` apenas para validação XSD.
- Detectar namespace e versão antes de selecionar o adaptador.
- Extrair identificação da NFS-e e DPS, datas, situação e ambiente.
- Extrair CNPJ/CPF, nomes, inscrições, contatos e endereços de prestador e tomador.
- Extrair município, código nacional/municipal do serviço, NBS e descrição.
- Extrair base, alíquota, ISS, retenções, total bruto, descontos e valor líquido.
- Interpretar IBS/CBS quando presente sem torná-lo obrigatório.
- Preservar assinatura, digest, grupos desconhecidos e XML original no RAW.
- Validar CNPJ/CPF, chave, datas, valores e coerência básica dos totais.
- Rejeitar versão desconhecida com erro explícito, sem aplicar mapeamento aproximado.

## Testes

- Uma golden annotation por XML com os campos obrigatórios conferidos manualmente.
- Testes de namespace, elementos opcionais, acentuação, valores e datas.
- Caso específico assegurando a preservação integral de IBS/CBS.
- Casos negativos: XML inválido, namespace desconhecido, XXE, chave inconsistente e valor não numérico.

## Critérios de aceite

- Quatro de quatro XMLs geram `FiscalDocument` válido.
- Chave, número, emissão, competência, prestador, tomador, serviço e totais coincidem com as goldens.
- O grupo IBS/CBS é extraído na amostra correspondente e não causa erro nas demais.
- Todo campo normalizado possui XPath ou justificativa de derivação.
- Nenhuma chamada de rede, certificado ou IA é utilizada.
