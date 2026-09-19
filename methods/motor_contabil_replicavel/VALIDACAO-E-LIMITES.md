# Validação, decisões e limites conhecidos

## Protocolo que produziu os benchmarks

- Clientes avaliados: Vanguarda, Martine e Luftklima.
- Corte walk-forward por competência: todo treino usa somente dados do mesmo cliente anteriores ao mês testado; a exceção é o fallback global, que usa apenas clientes/períodos anteriores e identidade semântica de conta.
- As linhas `accounting_proxy` foram removidas antes da formação do mês de teste. Elas não foram contadas como texto de extrato.
- Métrica de Conta: coincidência exata da contrapartida. Não mede somente grupo/família.
- Meta de 92%: precisão **entre linhas automáticas**, não acurácia em 100% da base.
- Métrica de HIST C/D: usa as contas corretas como entrada. A precisão ponta a ponta deve ser monitorada separadamente, porque erro de Conta muda o par C/D.

## Resultado aprovado: Conta com porta prequencial

| Cliente | Base de extrato | Automáticas | Cobertura | Precisão de Conta |
|---|---:|---:|---:|---:|
| Vanguarda | 835 | 629 | 75,33% | 94,91% |
| Martine | 991 | 410 | 41,37% | 94,63% |
| Luftklima | 2.060 | 1.431 | 69,47% | 94,41% |
| **Total** | **3.886** | **2.470** | **63,56%** | **94,57%** |

A sugestão em cobertura total ficou em 85,51%. Essa diferença é a razão pela qual `REVISAR CONTA` continua parte essencial do produto: a automação aprovada é seletiva, não uma alegação de 92% para todas as linhas.

Há uma exceção que precisa continuar explícita: no último período individual de Luftklima em que o limiar 0,85 foi aplicado, a precisão observada foi 90,84%. A política foi escolhida apenas com meses anteriores e mede generalização no mês corrente; deve ser acompanhada e ficar mais conservadora se houver degradação.

## Resultado aprovado: HIST condicionado a C/D

| Cliente | Linhas com HIST-alvo | Par C/D estável | Cobertura | Precisão se preenchido |
|---|---:|---:|---:|---:|
| Vanguarda | 835 | 745 | 89,22% | 99,87% |
| Martine | 158 | 0 | 0% | — |
| Luftklima | 0 na base de extrato pareada disponível | 0 | — | — |

Martine e Luftklima não devem receber um código HIST “emprestado” da Vanguarda: os meses anteriores não continham rótulos HIST preenchidos compatíveis. Para esses clientes, o comportamento correto é deixar `standard_history` pendente e registrar a ausência de `history_cd_*`.

## Hipóteses avaliadas e não promovidas

| Hipótese | Resultado | Decisão |
|---|---|---|
| Uma regra pura “último fornecedor” | 88,5% / 84,5% / 81,4% por cliente | Rejeitada: fornecedor pode ter modalidades contábeis diferentes. |
| Fornecedor + subtipo/grupo | Não superou o benchmark local | Rejeitada como substituta. |
| Dois meses estáveis de fornecedor | 89,0% / 85,4% / 83,8% | Útil para explicar, não para substituir o classificador. |
| Separar entidades conhecidas e texto genérico | 85,7% / 83,9% / 81,2% | Rejeitada: remove sinal complementar. |
| Transferir HIST entre clientes | Queda severa para Martine/Luftklima | Rejeitada: HIST não é semântica global. |
| SVM, logística, Naive Bayes e variações de regularização | Melhor cobertura total ~85,8% | Nenhuma alcançou 92% sem seleção. |

## Onde o modelo erra mais

- texto genérico ou entidade nova: Martine teve 70,6% e Luftklima 51,8% nesse segmento em agosto;
- fornecedor existente com natureza diferente (fornecedor × adiantamento × despesa);
- transferências sem contraparte explícita;
- ausência de HIST previamente preenchido.

O que funciona melhor é evidência repetida e específica: memória exata, texto de entidade reconhecida, direção, conta financeira e par C/D estável. O valor, data e documento são preservados para auditoria, mas não “resolvem” por si a modalidade contábil.

## Monitoramento obrigatório no novo sistema

1. Por cliente/mês, guardar total de sugestões, automáticas, revisadas, acertos e erros de Conta.
2. Recalcular o limiar antes de cada mês exclusivamente com revisões anteriores.
3. Para HIST, medir cobertura, precisão quando preenchido, suporte médio e erros por par C/D.
4. Registrar distribuição de `account_probability_source`; uma mudança súbita de `exact_memory` para `client_majority` é alerta de degradação.
5. Não sobrescrever a revisão humana: ela é a nova fonte de `approved_examples`/`history` para meses seguintes.
