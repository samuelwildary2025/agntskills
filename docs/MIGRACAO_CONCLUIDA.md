# Documentação da Migração para Skills

A migração da arquitetura do agente "Ana" foi concluída com sucesso. O objetivo de evoluir de um único "Prompt Monolítico" (God Prompt) e ferramentas sobrecarregadas para uma arquitetura limpa de **1 Agente Principal + Skills + Tools** foi alcançado de maneira incremental.

## 🌳 Nova Estrutura de Diretórios 

```text
agnt/
├── agent_multiagent.py          (Adaptado: Carrega apenas 'atendente_core.md')
├── server.py                    (Sem alterações)
├── prompts/
│   └── atendente_core.md        (NOVO: Prompt muito mais limpo e conciso)
├── skills/                      (NOVA PASTA: Comportamento modular)
│   ├── interpretar_pedido/
│   │   ├── SKILL.md
│   │   └── rules.md
│   ├── normalizar_termos/
│   │   ├── SKILL.md
│   │   └── aliases.json
│   ├── buscar_produto/
│   │   └── SKILL.md
│   ├── validar_resultado/
│   │   └── SKILL.md
│   ├── carrinho/
│   │   └── SKILL.md
│   └── checkout/
│       └── SKILL.md
└── tools/
    ├── skill_executor.py        (NOVO: Módulo que roda as "Skills" de interpretação e validação do produto)
    ├── search_router.py         (Sem alterações)
    ├── redis_tools.py           (Sem alterações)
    ├── http_tools.py            (Sem alterações)
```

## 🔄 O que Mudou

1. **Fim do "God Prompt" (`vendedor.md`)**
   - O arquivo original (`vendedor.md`) com mais de 200 linhas de regras de negócio, pesos exatos de carnes/frutas, mapeamento de gírias e processos de checkout estritos foi excluído.
   - Hoje, o sistema utiliza o `atendente_core.md`. Ele foca APENAS na *persona* do bot (nome da Ana, saudações), no objetivo final e na regra básica de não inventar preços e saber procurar/validar produtos usando as skills.

2. **Separação das Responsabilidades Pós-Busca (Skill Executor)**
   - O código que interpreta o que o cliente digitou (e converte "mao de vaca" em "ossobuco kg" ou avalia empate entre categorias de "higiene" e "limpeza") existia inteiramente dentro de `busca_produto_tool` em `agent_multiagent.py`.
   - Essa lógica foi movida para uma camada dedicada chamada `tools/skill_executor.py`. 
   - Logo, a ferramenta `busca_produto_tool` agora é apenas um *wrapper* (ponto de passagem) que acessa a inteligência contida no `skill_executor` baseada nos arquivos dentro da pasta `skills`.

3. **Arquivos da pasta `/skills`**
   - Seis habilidades reais (interpretar, normalizar, buscar, validar, carrinho e checkout) foram fisicamente desenhadas. Agora os desenvolvedores e analistas de negócio podem editar `aliases.json` para adicionar novos sinônimos ou alterar as `rules.md` se o fornecedor de maçãs passar a enviar maçãs de 0.30kg sem precisar alterar o código Python central.

## ✊ O que Permaneceu Igual

1. O fluxo primário do LangGraph em `agent.py` mantendo exatamente 1 Agente reagente ao usuário (sem invocar uma rede de múltiplos agentes).
2. As dependências base de banco de dados (`db_search.py`, `redis_tools.py`).
3. O histórico híbrido e o servidor (`server.py`). O Front-End e as outras integrações de WhatsApp continuam intactas pois a interface da "tool" para o "agente" é idêntica.

## 🎯 Benefícios Diretos
Com essa versão instalada e ativa no projeto:
- **Baixo Acoplamento:** As regras operacionais e gramaticais não brigam por espaço de limite de prompt contra a identidade da marca ou com os parâmetros de chamada da Tool.
- **Evita Alucinações:** A inteligência artificial compreende claramente em qual etapa do fluxo ela está (Se a Skill pertinente é a do `Carrinho` ou a do `Checkout`).
- **Escalabilidade:** Caso o mercadinho passe a aceitar "Pix Fiado", apenas a Skill `checkout` é modificada.
