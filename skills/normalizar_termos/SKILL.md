---
name: normalizar_termos
description: Habilidade para converter gírias, sinônimos, e formas coloquiais em termos padrão de catálogo.
---

# Skill: Normalizar Termos

## Objetivo
Padronizar a busca convertendo a maneira como o cliente escreve para o termo cadastrado no sistema do supermercado, evitando falhas de pesquisa por "miss typing" ou variações regionais.

## Atuação
Sempre aplique as conversões fornecidas no `aliases.json` antes de montar a busca final ou decidir o produto.

## Casos Comuns
- Absorvente -> "abs"
- Pão carioca/carioquinha -> "pao frances"
- Massa fina / massafina -> "pão sovado"
- Carne para strogonoff -> "strogonoff kg"
- Mão de vaca / ossobuco -> "ossobuco kg"
