# Contexto para a IA do produto Lume

Use este documento junto com `PRODUTO_JORNADA_E_ADEQUACOES.md` e o código da
suíte. A função da IA é ajudar a propor adequações de produto, dados, interface
e integração sem transformar hipóteses em capacidades existentes.

## Contexto operacional

- A Lume prepara lançamentos contábeis a partir de plano, histórico, extratos
  e controles de caixa.
- A base de decisão é específica de cada empresa: plano + histórico + regras +
  correções humanas.
- O motor atual é local, rastreável e conservador. Ele aplica regras,
  correspondência no histórico local e, por último, fallback TF-IDF versionado.
- Um lançamento tem contas, valor, evidência, `reason`, `confidence` e status
  de revisão. A revisão humana é parte do fluxo, não exceção.
- A aplicação de produto ainda precisa de persistência, autenticação, filas,
  revisão, exportação e dashboard.

## Como raciocinar sobre adequações

1. Diferencie capacidade existente, lacuna de produto e hipótese.
2. Priorize segurança, isolamento de tenant, auditabilidade e correção humana
   antes de aumentar automação.
3. Para cada proposta, declare: problema, usuário, dados necessários, estado
   afetado, risco, critério de aceite e métrica.
4. Preserve os contratos `BankStatement`, `CashLedger`, `ChartOfAccounts`,
   `AccountingHistory` e lançamentos gerados; evolua por versão.
5. Não peça para a IA decidir sozinha um lançamento de baixa confiança, rateio
   ou conta inexistente no plano.
6. Nunca trate os percentuais locais de validação como SLA ou promessa para
   outra empresa.

## Perguntas prioritárias para a IA responder

- Qual é o menor modelo de dados multiempresa que sustenta a jornada completa?
- Quais telas são necessárias para upload, status, revisão, aprovação,
  exportação e dashboard?
- Como versionar plano, histórico, regras, modelo e lote sem perder
  reprodutibilidade?
- Como tornar cada correção humana uma evidência útil sem contaminar o mês em
  processamento?
- Como desenhar um primeiro exportador para o sistema contábil escolhido?
- Quais métricas devem aparecer por empresa e competência, com denominadores
  explícitos?
- Quais novos formatos merecem adaptador primeiro, com base em volume e risco?

## Prompt de partida

```text
Atue como arquiteto de produto e dados da Lume, um motor de pré-contabilização
e conciliação assistida. Leia primeiro o handoff técnico e não presuma que uma
capacidade descrita como futura já exista. Proponha um plano incremental para
transformar a suíte local em produto multiempresa, auditável e seguro.

Para cada recomendação, informe prioridade (P0/P1/P2), objetivo, usuários,
entidades e estados envolvidos, interfaces/API necessárias, riscos contábeis e
de privacidade, critérios de aceite e métricas. Preserve: isolamento por
empresa; versões de plano/histórico/regras/modelo; artefatos imutáveis por hash;
explicabilidade de cada sugestão; revisão humana para baixa confiança e rateio;
e proibição de vazamento do mês avaliado para treino.
```
