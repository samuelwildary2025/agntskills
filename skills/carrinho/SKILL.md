---
name: carrinho
description: Habilidade para interagir com o carrinho/sessão do pedido atual (adicionar itens, remover, ver pedido atual e calcular subtotal provisório).
---

# Skill: Carrinho e Gestão de Pedido

## Objetivo
Ler, interpretar e apresentar respostas relativas à inserção (ou remoção) de itens com foco na formatação exigida pela interface e regras do carrinho.

## Regras de Execução

1. **Adicionar Itens**
  - Chamar a ferramenta/função `add_item_tool` apenas após ter certeza (validado por `validar_resultado`) de qual item adicionar e o valor.
  - O cálculo do preço unitário * quantidade deve apresentar o **Subtotal**.
  - O formato da resposta para um novo item adicionado:
    `✅ Adicionei ao seu pedido:`
    `- item, quantidade/peso, valor`
    `📦 Subtotal: R$ XX,XX`

2. **Remover Itens**
  - Chamar `remove_item_tool`.
  - Se for para remover 1 unidade de um item que tem 3 no carrinho (Remoção Parcial), especificar a quantidade.
  - Responder confirmando o item removido e mostrar o subtotal correto.

3. **Ver Pedido**
  - Chamar `ver_pedido_tool`. Formatar como uma lista numerada e mostrar o Subtotal ao final.

4. **Zerando o pedido**
  - Se o cliente pedir para recomeçar o pedido do zero, use a tool para limpar (ex: `reset_pedido_tool`) e informe ao cliente.

## Avisos de Pesáveis
Sempre que concluir a adição de um produto **vendido a quilo** no carrinho, lembre o cliente com a frase na mesma mensagem:
`*Observação: carnes e hortifruti têm peso/valor aproximados. O valor exato é ajustado na separação.*`
