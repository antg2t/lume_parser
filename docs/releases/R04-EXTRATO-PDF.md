# R04 — Extrato bancário em PDF

## Contexto e objetivo

Pré-requisito: fundação e extração PDF da R00. Há um extrato Itaú digital com seis páginas e texto nativo.

O objetivo é criar o primeiro adaptador de extrato bancário PDF e produzir um `BankStatement` reconciliável.

## Contrato documental

O modelo contém banco, agência, conta, titular, identificador fiscal, moeda, período, saldos e transações ordenadas. Cada transação contém data, descrição, contraparte, CNPJ/CPF, documento quando disponível, valor, saldo quando impresso, tipo inferido apenas por regra explícita e evidência de origem.

## Implementação planejada

- Classificar o banco e a versão do layout por conteúdo interno.
- Extrair cabeçalho, período, saldo anterior, saldo final, limites e disponibilidade sem confundi-los.
- Reconstruir lançamentos por âncoras de data e coordenadas de coluna.
- Unir descrições e dados de contraparte quebrados entre linhas ou páginas.
- Separar transações de linhas informativas como saldo diário.
- Preservar duplicatas legítimas; não deduplicar sem identificador forte.
- Reconciliar saldos diários e final, emitindo warnings com a primeira divergência.
- Manter o adaptador Itaú isolado para futuros bancos.

## Testes

- Golden manual de todas as transações e saldos do arquivo atual.
- Valores positivos, negativos, IOF, PIX, boleto, TED e descrições multilinha.
- Quebra de página no meio de um dia e linhas sem CNPJ/CPF.
- Teste garantindo que o controle de caixa não seja classificado como extrato.

## Critérios de aceite

- O PDF é classificado como extrato Itaú.
- Todas as transações da golden aparecem uma vez e na ordem correta.
- Saldos informativos não viram transações.
- Valores e saldos reconciliam ou geram warning detalhado.
- Nenhum OCR, IA ou regra contábil é utilizado.
