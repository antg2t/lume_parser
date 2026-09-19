# Lógica detalhada e invariantes

## 1. Conta: construção do lançamento

O motor prevê somente a contrapartida. O lado financeiro e o valor não são previstos.

| Direção do movimento | Débito | Crédito | Valor |
|---|---|---|---|
| `inflow` (`amount > 0`) | `financial_account` | `counterpart` previsto | `abs(amount)` |
| `outflow` (`amount < 0`) | `counterpart` previsto | `financial_account` | `abs(amount)` |

Se uma das duas contas não existir, o status é `REVISAR CONTA`. Movimento com múltiplos tributos no mesmo texto (`ICMS`, `IPI`, `PIS`, `COFINS`, `INSS`, `IRRF`, `IRPJ`, `CSLL`/`C. SOC.`, `PCC`) recebe `REVISAR RATEIO`: dividir valores entre contas é uma decisão humana ou de uma fonte de cálculo, nunca um chute do classificador.

## 2. Prioridades da Conta

### 2.1 Transferência espelhada

Antes de classificar individualmente, o motor procura outro movimento:

- mesma data;
- valor exatamente oposto;
- ambas as contas financeiras conhecidas e diferentes;
- texto contendo `transfer`, ou `cdb` junto de `aplic`/`resgat`.

O par vira um único lançamento entre as duas contas financeiras, `LANCAR`, razão `transferencia_espelhada` e `account_probability=1.0` de origem `matched_transfer`.

### 2.2 Regras semânticas

As regras abaixo só produzem conta se encontrarem no Plano uma descrição compatível. Entre candidatas, vence a de maior atividade em lançamentos anteriores; o desempate é a ordem estável do plano.

| Evidência no texto normalizado | Sentido | Busca no Plano | Razão |
|---|---|---|---|
| `rendimento`, `rentab`, `juros receb` | entrada | termos `juros` ou `rendimento` e `recebid` ou `receita` | `rendimento` |
| `tarifa`, `iof`, `encargos banc`, `doc/ted` | saída | `desp` e `banc` | `despesa_bancaria` |
| `capitaliza` | ambos | `capitaliza` | `capitalizacao` |
| exatamente um tributo explícito | ambos | termo do tributo e `recolher` | `tributo:<nome>` |

Essas decisões exibem `account_probability=1.0` e `account_probability_source=deterministic_rule`. É certeza da regra, não probabilidade empírica de acerto.

### 2.3 Memória textual local

Se não houve regra, são candidatas linhas HIST anteriores que tenham a conta financeira no lado coerente da direção. Primeiro se procura a mesma conta financeira; apenas se não houver nenhuma, usam-se outras contas financeiras do cliente.

Para cada candidato:

```text
tokens = palavras com 3+ letras, sem stopwords de negócio
containment = |tokens_movimento ∩ tokens_complemento| / min(|tokens_movimento|, |tokens_complemento|)
sequence = SequenceMatcher(" ".join(sorted(tokens_movimento)),
                           " ".join(sorted(tokens_complemento))).ratio()
score = 0,75 * containment + 0,25 * sequence
```

Se `score < 0,50`, há abstenção. Caso contrário, reutiliza-se a contrapartida e o HIST da melhor linha. A faixa `>=0,70` vira confiança textual `Alta`; entre 0,50 e 0,70, `Media`. O número é publicado como `account_probability`, mas a fonte `text_similarity` deixa explícito que é similaridade, não posterior calibrado.

### 2.4 Modelo local do cliente: texto → Conta e texto → HIST

O modelo possui duas cabeças treinadas independentemente nos exemplos aprovados anteriores.

```text
features =
  direcao_<inflow|outflow>
  banco_<financial_account>
  valor_<abs(amount)*100>
  ordem_<quantidade_de_dígitos_da_parte_inteira>
  normalize_text(counterparty + notes + document)
```

`normalize_text` reduz CNPJ/CPF, datas, competência, valores, números de NF/fatura e sequências longas a marcadores; o código exato está em `reference_engine.py`. Assim, fornecedor e modalidade textual sobrevivem, mas um número específico de nota não vira uma falsa chave de memorização.

Ordem interna de cada cabeça:

1. memória exata da feature completa, desde que ela tenha um único rótulo;
2. memória exata sem os atributos de valor, desde que única;
3. `FeatureUnion` TF-IDF de palavras 1–2 gramas e caracteres `char_wb` 3–5 gramas; `min_df=2`, `sublinear_tf=True`;
4. `SGDClassifier(loss="log_loss", alpha=1e-5, max_iter=1000, tol=1e-3, random_state=42)`;
5. classe majoritária, score 0, quando não existe modelo treinável.

No treinamento, rótulos com suporte menor que 2 não alimentam o TF-IDF, mas ainda podem ser retornados por memória exata. Para Conta, o legado aceita somente `counterpart.isdigit()`.

As duas probabilidades não são misturadas na saída. A confiança operacional do fallback local é `min(P_conta, P_hist)` quando há cabeça HIST; se não há HIST de treino, ela é `P_conta`. Isso preserva o comportamento do motor original. `account_probability` e `history_probability` continuam disponíveis separadamente para auditoria e evolução posterior.

### 2.5 Fallback global de Conta

O fallback global é opcional. Ele treina o mesmo classificador de texto, mas o rótulo é uma identidade semântica da conta: hash SHA-256 do caminho de descrições no plano, não o código reduzido de outro cliente.

Ele é chamado apenas quando o cliente local não entrega uma escolha aprovada. O resultado é descartado se a identidade prevista não mapear para exatamente uma conta no plano do cliente atual. `global_tfidf` não prevê HIST e jamais transfere código HIST entre empresas.

## 3. Modelo C/D → HIST

O modelo recebe as contas já determinadas e ignora o texto:

```text
contagens[(debit_account, credit_account)][standard_history] += 1
hist_candidato = modo das contagens do par
P(HIST | D,C) = frequência(hist_candidato) / soma_das_frequências_do_par
```

Todos os dados são anteriores à competência alvo. O resultado é publicado sempre que há par conhecido, inclusive ambíguo:

```json
{
  "history_cd_candidate": "551",
  "history_cd_probability": 0.75,
  "history_cd_support": 4,
  "history_cd_alternatives": 2
}
```

Aplicação automática exige simultaneamente:

```text
history_cd_support >= 2
history_cd_probability == 1.0
history_cd_alternatives == 1
```

Quando aplicada, a saída `standard_history` recebe o candidato e `history_probability_source=account_pair_frequency`. Em qualquer ambiguidade, a previsão aparece nos campos `history_cd_*`, mas o motor preserva o HIST textual já existente ou deixa vazio para revisão.

## 4. Transformação de texto para Complemento HIST

A função `format_history_complement` é independente dos classificadores. Ela define um prefixo operacional e preserva a evidência de origem em maiúsculas:

| Condição, na ordem apresentada | Prefixo |
|---|---|
| contém `transfer` | `TRANSFERENCIA ENTRE CONTAS` |
| `rendimento`/`rentab` | `RENDIMENTO BANCARIO` |
| `iof` | `IOF` |
| `tarifa` | `TARIFA BANCARIA` |
| `encargo` | `ENCARGOS BANCARIOS` |
| saída com `boleto` | `BOLETO PAGO` |
| entrada/saída com `pix` | `RECEBIMENTO PIX` / `PAGAMENTO PIX` |
| entrada/saída com `ted` | `RECEBIMENTO TED` / `PAGAMENTO TED` |
| demais | `RECEBIMENTO` / `PAGAMENTO` |

Em seguida, remove prefixos técnicos de contraparte como `00015-`, elimina repetição do termo operacional (`PIX ENVIADO`, por exemplo), mantém detalhe e adiciona `DOC <documento>` caso ele ainda não apareça no detalhe.

## 5. Política de 92%: separada do algoritmo de sugestão

A previsão de Conta acontece sempre que há alguma evidência; a autorização de lançar é uma política de risco independente. Para cada cliente e mês, a escolha do limiar usa somente os pares `(acertou_conta, account_probability)` de competências anteriormente revisadas.

```python
for threshold in (0.80, 0.85, 0.90, 0.95, 0.98, 1.00):
    accepted = [correct for correct, confidence in prior if confidence >= threshold]
    if len(accepted) >= 20 and mean(accepted) >= 0.92:
        candidates.append(threshold)
selected = min(candidates) if candidates else 1.00
```

Não é permitido selecionar o limiar olhando os acertos do próprio mês que será lançado. A função pronta está no script, mas o armazenamento das revisões mensais é responsabilidade do sistema novo.
