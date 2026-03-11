---
name: validar_resultado
description: Habilidade para conferir o resultado da busca, aplicar regras de negócio em categorias conflitantes e decidir se o agente deve pedir confirmação do cliente.
---

# Skill: Validar Resultado da Busca

## Objetivo
Analisar os produtos recebidos de `buscar_produto` e garantir que não adicionamos o produto errado ao carrinho sem avisar o cliente.

## Regras de Ambiguidades e Confirmações
Você não deve aceitar o produto e deve **pedir confirmação** quando:
1. **Baixo score semântico** e a cobertura dos termos originais do pedido não for forte no resultado superior (Top 1).
2. **Empate técnico ou Categorias Mistas**: Resultados com score similar na busca onde os Top 3 misturam categorias importantes ("LIMPEZA" vs "HIGIENE", "AÇOUGUE" vs "BEBIDAS"). Em caso de produtos parecidos onde a categoria difere consideravelmente, avise o cliente e aguarde ele dizer qual é a categoria que deseja.
3. Se o cliente pedir uma especificação estrita (ex: "carne para strogonoff") e não vier o produto com nome contendo exatamente esse núcleo (ex: `STROGONOFF kg`), avise que a opção oficial não está disponível e não troque silenciosamente para outro corte, a menos que ele aprove.
4. **Preferência de marca**: Se o usuário pediu a marca X e a busca retornou produtos, mas nenhum tem a marca X no nome, lance um aviso para confirmação antes de adicionar para o carrinho.
