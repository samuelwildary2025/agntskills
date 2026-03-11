---
name: interpretar_pedido
description: Habilidade para extrair intenção, identificar itens, quantidade, marca, tamanho e características do pedido do cliente.
---

# Skill: Interpretar Pedido

## Objetivo
Analisar a mensagem do cliente e extrair corretamente cada item que ele deseja adicionar, remover ou alterar no pedido, identificando a intenção clara.

## Responsabilidades
1. **Extrair a intenção**: O cliente quer "adicionar", "remover", "alterar quantidade", ou "finalizar"?
2. **Identificar itens**: Separar múltiplos itens pedidos na mesma frase em entidades distintas. NUNCA agrupe itens diferentes numa mesma busca.
3. **Identificar quantidade e unidade**: Converter pedidos em unidades ou "kg" com base nas regras de negócio da loja.
4. **Identificar atributos**: Capturar marca, tamanho, sabor ou exigências específicas (ex: "cortado", "noturno").

## Instruções de Uso
Antes de realizar uma busca de produto, leia as regras em `rules.md` para estimar pesos de produtos pesáveis e entender formatações específicas (ex: Açougue, Frutas).

## Regras de Execução
- Se o cliente pedir uma lista (ex: "1 arroz, 2 feijoes e 1 carne"), trate cada item separadamente.
- Se a mensagem contiver apenas um número (ex: "1") sem o nome do produto, ignore e peça esclarecimento ("Um pacote de qual item?").
- Se não for especificado unidade, assuma 1 unidade como padrão (observando exceções em `rules.md`).
