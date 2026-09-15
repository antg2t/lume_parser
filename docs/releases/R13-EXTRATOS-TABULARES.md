# R13 — Extratos bancários tabulares reais

## Objetivo

Normalizar dois layouts bancários tabulares reais para o contrato comum
`BankStatement`, sem depender do nome do cliente ou do arquivo:

1. exportação tabular do Itaú em XLSX;
2. Bradesco Net Empresa em XLS legado.

## Implementação entregue

- `parsers/xls.py` lê XLS de forma somente leitura com `xlrd` 2.0.1.
- `detection.py` reconhece XLS pela assinatura Compound File, não pela extensão.
- `bank_statement_spreadsheet.py` reconhece o cabeçalho, os sinais internos do
  banco e normaliza conta, agência, período, lançamentos, saldos e evidências
  por célula.
- `BankStatementOrigin` passou a aceitar origem PDF, XLSX e XLS.
- O Bradesco Net Empresa encerra a coleta ao encontrar um segundo cabeçalho de
  extrato, evitando misturar o mês seguinte no lote atual.

## Regras de segurança

- Crédito e débito são definidos mecanicamente pelas colunas/valor impresso.
- Linha de saldo anterior inicia a conciliação; saldo diário ou final é
  preservado como evidência.
- Divergência de saldo retorna `warning`, não uma alteração silenciosa no
  movimento.
- Um layout não reconhecido retorna erro estruturado; não é reinterpretado como
  caixa ou histórico por causa do nome do arquivo.

## Cobertura comprovada

| Layout | Resultado |
|---|---|
| Itaú XLSX | 352 lançamentos, 17 saldos diários, origem por célula |
| Bradesco Net Empresa XLS | 311 lançamentos de agosto, 21 saldos diários, período seguinte separado |

Os números pertencem ao acervo de validação local; não acompanham este
handoff. Os testes de integração correspondentes estão em
`tests/test_bank_statement.py` e `tests/test_detection.py`.

## Fora de escopo

Esta release não cria um leitor universal para XLS/XLSX, não converte arquivos
proprietários e não adiciona OFX/QFX ou CSV. Cada novo layout requer fixture
real autorizado, reconhecimento por conteúdo, contrato auditável e teste de
regressão.
