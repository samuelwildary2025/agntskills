---
name: buscar_produto
description: Habilidade para converter um pedido interpretado em uma query de busca eficiente para o motor (Typesense/DB), e priorizar os produtos retornados baseados no que foi pedido.
---

# Skill: Buscar Produto

## Objetivo
Chamar o motor de busca existente (ex: API local), utilizando uma lógica que maximize as chances de encontrar o que o cliente quer sem ambiguidades, fazendo de forma "inteligente" (tentativa repetitiva com termos simplificados caso não encontre).

## Estratégia de Busca
1. **O Agente (LLM) tem o poder:** O Vendedor não envia mais a frase bruta do cliente (ex: "tem aquele veneno q mata muricoca?"). 
2. A inteligência artificial do LLM deve deduzir a categoria real desejada e traduzi-la (ex: enviar apenas `inseticida` para a tool).
3. **Não poluir a busca:** Se o cliente disser "1 pacote da massa de tapioca da marca dona claudia", o LLM processa a gíria e passa apenas a intenção pura: `tapioca dona claudia`.
4. Nunca reescreva o método principal local `tools.search_router`, apenas passe as buscas limpas e traduzidas adiante para a skill `validar_resultado`.
