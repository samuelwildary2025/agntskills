---
name: checkout
description: Habilidade responsável pelo processo de finalização do pedido, abrangendo endereço, taxa e forma de pagamento.
---

# Skill: Checkout e Finalização

## Objetivo
Coordenar as perguntas finais e coletar as informações necessárias para que o pedido possa ser finalizado por chamar o `finalizar_pedido_tool`

## Critérios para Finalização (Checklist Obrigatório)
Para concluir um pedido e enviá-lo para finalização (banco/api de pedidos correspondente das tools), garanta que tem essas 4 coisas essenciais:
1. Carrinho Preenchido.
2. Endereço Válido. (Chame \`salvar_endereco_tool\` ou confirme se o endereço já consta).
3. Confirmar Pagamento. (Se o cliente vai usar Pix, Cartão ou Dinheiro).
4. Aplicação de Taxa de Entrega (Mostre que ela existe).

## Executando as Etapas (Interação com Usuário)
Apenas comece a perguntar esses itens da seção Fase B (fechamento) após o cliente sinalizar encerramento do carrinho. Ex: "Pode fechar", "só isso".

1. **Passo Endereço / Taxa de entrega:** Peça e/ou confirme. Use taxa conhecida ou informe 0 se incerta.
2. **Passo Pagamento:** Confirme a forma antes de fechar.
3. **Resumo & Chamar Finalização:** Faça a soma de: Valor dos itens + Taxa. Execute a Tool.
4. Confirmação ao final: "✅ Pedido confirmado e enviado para separação."
