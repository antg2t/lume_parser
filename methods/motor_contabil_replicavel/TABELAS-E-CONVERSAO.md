# Tabelas e conversão para o contrato

## Regras gerais de ingestão

- Ler códigos de conta, `HIST`, documento, CNPJ e identificadores como **texto**, nunca como número. Isso preserva zeros à esquerda.
- Datas devem sair em ISO `YYYY-MM-DD`; competência é `YYYY-MM`.
- Valores devem usar ponto decimal no JSON (`"1234.56"`). Sinal positivo é entrada e sinal negativo é saída. Não enviar colunas Entrada/Saída simultaneamente ao motor sem antes convertê-las para esse sinal.
- String vazia e nulo são diferentes no Excel, mas ambos podem ser convertidos para `null`/`""` conforme o campo. Não substituir texto ausente por `"N/A"` ou zero.
- Todo `approved_examples` e `history` usado para um alvo `target_period=M` deve ter data `< M`. O script também filtra, porém a ETL deve garantir a regra como controle independente.

## `accounts` — Plano de Contas

| Campo | Obrigatório | Exemplo | Regra |
|---|---|---|---|
| `code` | sim | `1.1.1.02.001` | Código hierárquico original; deve incluir níveis-pai relevantes. |
| `reduced_code` | sim | `1000012` | Identificador usado nos lançamentos. Deve ser único no cliente. |
| `description` | sim | `Banco Horizonte` | Descrição original. É usada para reconhecer bancos/aplicações e regras semânticas. |

Para derivar contas financeiras, o motor procura no plano as descrições contendo `bancos conta movimento` e `aplicacoes de curto prazo`. Todos os códigos-filho daqueles pais passam a ser contas financeiras. Assim, manter as linhas-pai do plano é obrigatório para a mesma equivalência.

## `history` — lançamentos HIST revisados

| Campo | Obrigatório | Uso |
|---|---|---|
| `date` | sim | Corte temporal e atividade das contas. |
| `debit_account`, `credit_account` | sim | Memória textual, contador de atividade e chave do modelo C/D → HIST. |
| `standard_history` | recomendado | Rótulo alvo do modelo C/D → HIST. Vazio não vira classe. |
| `complement` | sim | Texto de referência da memória textual. |
| `amount` | não pelo motor central | Auditoria/reconciliação; manter quando disponível. |

Uma linha só participa quando as duas contas existem no plano do cliente e a data é anterior ao período de previsão. Não deduplicar lançamentos reais apenas porque possuem igual data, valor ou texto: a frequência do par C/D é parte do modelo de HIST.

## `approved_examples` — ponte obrigatória para o modelo de Conta

Esta tabela é obtida juntando um movimento financeiro de origem à classificação contábil já revisada. Ela precisa manter o texto de origem, e não substituir pelo Complemento editado pelo contador.

| Campo | Obrigatório | Origem |
|---|---|---|
| `date` | sim | Data do movimento pareado. |
| `text` | sim | Concatenação semântica de contraparte, descrição/observação e documento do extrato/caixa. |
| `direction` | sim | `inflow` se a conta financeira foi debitada; `outflow` se foi creditada. |
| `financial_account` | sim | Conta bancária/aplicação reduzida no plano do cliente. |
| `counterpart` | sim | Outra ponta correta do lançamento; deve ser código numérico no modelo legado. |
| `standard_history` | recomendado | HIST revisado; vazio é aceito e não treina a cabeça HIST de texto. |
| `amount` | recomendado | Valor absoluto do movimento; vira atributo textual de valor/ordem. |

O modelo local legado exclui, por segurança de contrato, exemplos cuja `counterpart` não seja composta apenas de dígitos (`str.isdigit()`). Se o novo plano usar códigos alfanuméricos, há duas opções explícitas: manter uma chave numérica de treinamento ou alterar esta regra e revalidar todos os benchmarks. Não faça a mudança silenciosamente.

### Pareamento correto

O pareamento mínimo é por cliente, conta financeira, direção, data e valor absoluto, com reconciliação quando houver duplicatas. Use documento, fornecedor e proximidade de data apenas como desempate auditável. Linhas derivadas apenas do `Complemento` contábil são `accounting_proxy`: podem entrar no treino histórico como dado complementar, mas não entram na métrica de texto de extrato, pois não testam o problema real.

## `movements` — caixa/extrato do período alvo

| Campo | Obrigatório | Regra |
|---|---|---|
| `id` | sim | Chave estável de origem. |
| `date` | sim | Deve pertencer a `target_period`. |
| `amount` | sim | Positivo=entrada, negativo=saída. |
| `financial_account` | sim para lançar | Código reduzido já resolvido da conta bancária/aplicação. Sem ele, o motor revisa. |
| `counterparty` | não | Nome/identidade como veio da origem. |
| `notes` | não | Descrição/observação bancária ou de caixa. |
| `document` | não | Documento de origem. Preservar como texto. |
| `financial_name` | não | Nome exibível da conta financeira. |
| `source_row` | não | Linha original para auditoria. |

`text` não é enviado separadamente para o movimento: o motor compõe exatamente `counterparty + notes + document`, ignorando nulos. Alterar a ordem ou inserir campos adicionais muda o resultado da normalização, do TF-IDF e da similaridade.

## `global_examples` — apenas fallback global de Conta

| Campo | Obrigatório | Regra |
|---|---|---|
| `date`, `text`, `direction` | sim | Mesmas convenções do exemplo aprovado. |
| `semantic_key` | sim | SHA-256 da hierarquia normalizada de descrições do plano de origem. |

Para produzir a chave, o motor forma o caminho `descrição_pai > ... > descrição_da_conta`, normaliza com `plain`, prefixa `chart-description-path.v1\0` e calcula SHA-256. Uma previsão global só é usada se a mesma chave existir em **uma e apenas uma** conta local. Ambiguidade implica abstenção.

O fallback global só entra depois das regras, da memória local e do modelo local não aprovado. Ele não treina nem prevê `standard_history`.

## Exemplo de transformação de uma linha

Entrada de caixa/extrato:

```json
{
  "id": "2",
  "date": "2026-08-02",
  "amount": "-11078.78",
  "counterparty": "00015-AIRLIQUIDEBRASILLTDA-PAULINIA",
  "notes": "PIX ENVIADO",
  "document": "0000355193",
  "financial_account": "1000012"
}
```

Complemento determinístico produzido:

```text
PAGAMENTO PIX - AIRLIQUIDEBRASILLTDA-PAULINIA - DOC 0000355193
```

Não se acrescenta NF, CNPJ formatado, fornecedor corrigido ou conta de despesa que não estava no movimento. Essas informações podem existir em outros sistemas, mas exigem uma fonte de evidência adicional e rastreável.
